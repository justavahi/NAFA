import hashlib, json, time, uuid
from pathlib import Path
from .config import load_config
from .fetch import fetch_url
from .parsers import parse_rss, parse_html, parse_xml_series, parse_csv_file, parse_pdf
from .storage import initialize_database, connect, upsert_document, save_macro, import_frame
from .normalize import now_iso

def _source_file(source, base):
    return (base / source["path"]).resolve()

def ingest_source(source, settings, database_path, config_dir):
    run_id=str(uuid.uuid4()); started=time.perf_counter(); fetched=now_iso()
    initialize_database(database_path)
    headers={"User-Agent":settings["user_agent"]}
    try:
        kind=source["type"].lower()
        if kind in {"rss","xml","html"}:
            payload, final_url, content_type=fetch_url(source["url"],headers=headers,
                timeout=settings.get("timeout_seconds",20),retries=settings.get("retries",4))
            if kind=="rss": records, parse_meta=parse_rss(payload,source)
            elif kind=="xml": records=parse_xml_series(payload,source); parse_meta={}
            else: records=parse_html(payload,source,final_url); parse_meta={}
            with connect(database_path) as db:
                if kind=="xml": save_macro(db,records); written=len(records); duplicates=0
                else:
                    written=0; duplicates=0
                    for rec in records:
                        duplicate=upsert_document(db,rec)
                        duplicates += int(duplicate); written += int(not duplicate)
                        db.execute("""INSERT INTO ingestion_log(run_id,source_id,doc_id,published_at,
                            fetched_at,ready_at,status,is_duplicate,rows_seen,rows_written,duration_ms)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (run_id,source["id"],rec["doc_id"],rec.get("published_at"),fetched,
                             now_iso(),"success",int(duplicate),1,int(not duplicate),
                             (time.perf_counter()-started)*1000))
                db.execute("""INSERT INTO ingestion_log(run_id,source_id,fetched_at,ready_at,status,
                    rows_seen,rows_written,duration_ms) VALUES(?,?,?,?,?,?,?,?)""",
                    (run_id,source["id"],fetched,now_iso(),"success",len(records),written,
                     (time.perf_counter()-started)*1000))
            return {"source_id":source["id"],"status":"success","seen":len(records),
                    "written":written,"duplicates":duplicates}
        if kind in {"csv","client_csv","payment_csv"}:
            path=_source_file(source,config_dir)
            df, report=parse_csv_file(path,source)
            table="payment_rows" if kind=="payment_csv" else "client_rows"
            with connect(database_path) as db:
                written=import_frame(db,df,source["id"],table)
                db.execute("""INSERT INTO ingestion_log(run_id,source_id,fetched_at,ready_at,status,
                    rows_seen,rows_written,duration_ms) VALUES(?,?,?,?,?,?,?,?)""",
                    (run_id,source["id"],fetched,now_iso(),"success",len(df),written,
                     (time.perf_counter()-started)*1000))
            report.update({"source_id":source["id"],"status":"success","written":written})
            return report
        if kind in {"pdf","image"}:
            path=_source_file(source,config_dir)
            pages=parse_pdf(path) if kind=="pdf" else [{"page":1,"text":""}]
            file_hash=hashlib.sha256(path.read_bytes()).hexdigest()
            with connect(database_path) as db:
                doc_id=source.get("doc_id",source["id"])
                title=source.get("name",path.name)
                url="file://"+str(path)
                # Document canonical URL is unique; store local file reference for traceability.
                db.execute("""INSERT OR IGNORE INTO documents
                    (doc_id,source_id,kind,title,url,canonical_url,published_at,fetched_at,language,
                    category,snippet,text,tier_weight,content_hash,cluster_id,stale)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (doc_id,source["id"],"alfa_analytics",title,url,url,source.get("as_of_date"),
                     fetched,"ru",None,None,None,float(source.get("tier_weight",1.0)),file_hash,None,0))
                for p in pages:
                    chunk_id=f"{doc_id}-p{p['page']}"
                    db.execute("""INSERT OR REPLACE INTO chunks(chunk_id,doc_id,page,block_type,text,
                      numbers_json,as_of_date,extraction_model,extraction_confidence,needs_manual_check)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (chunk_id,doc_id,p["page"],"text",p["text"],"[]",source.get("as_of_date"),
                       "pymupdf-text",None,1))
                db.execute("""INSERT INTO ingestion_log(run_id,source_id,fetched_at,ready_at,status,
                  rows_seen,rows_written,duration_ms) VALUES(?,?,?,?,?,?,?,?)""",
                  (run_id,source["id"],fetched,now_iso(),"success",len(pages),len(pages),
                   (time.perf_counter()-started)*1000))
            return {"source_id":source["id"],"status":"success","pages":len(pages),
                    "file_sha256":file_hash,"needs_vlm_extraction":True}
        return {"source_id":source["id"],"status":"unsupported","type":kind}
    except Exception as exc:
        with connect(database_path) as db:
            db.execute("""INSERT INTO ingestion_log(run_id,source_id,fetched_at,status,error_type,
              duration_ms) VALUES(?,?,?,?,?,?)""",
              (run_id,source["id"],fetched,"error",type(exc).__name__,
               (time.perf_counter()-started)*1000))
        return {"source_id":source["id"],"status":"error","error_type":type(exc).__name__}

def ingest_all(config_path="config/sources.yaml", source_id=None):
    settings,sources,config_dir=load_config(config_path)
    database_path=settings["database_path"]
    initialize_database(database_path)
    results=[]
    for source in sources:
        if not source.get("enabled",False): continue
        if source_id and source["id"]!=source_id: continue
        results.append(ingest_source(source,settings,database_path,config_dir))
    return results
