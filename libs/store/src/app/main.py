from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Query
from nafa_ingest.config import load_config
from nafa_ingest.storage import initialize_database, connect, search_documents
from nafa_ingest.pipeline import ingest_all

app=FastAPI(title="NAFA Backend Parsing",version="0.2.0")

def db_path():
    settings,_,_=load_config()
    initialize_database(settings["database_path"])
    return settings["database_path"]

@app.get("/health")
def health():
    return {"status":"ok"}

@app.get("/sources")
def sources():
    _,items,_=load_config()
    with connect(db_path()) as db:
        out=[]
        for item in items:
            last=db.execute("SELECT MAX(fetched_at) v FROM ingestion_log WHERE source_id=? AND status='success'",
                            (item["id"],)).fetchone()["v"]
            out.append({**item,"last_success_at":last})
        return out

@app.get("/series/{series_id}")
def series(series_id:str,start:str|None=None,end:str|None=None):
    with connect(db_path()) as db:
        q="SELECT * FROM macro_series WHERE series_id=?"; args=[series_id]
        if start: q+=" AND date>=?"; args.append(start)
        if end: q+=" AND date<=?"; args.append(end)
        q+=" ORDER BY date"
        return [dict(r) for r in db.execute(q,args)]

@app.get("/news")
def news(q:str="",limit:int=Query(20,ge=1,le=100)):
    if not q: 
        with connect(db_path()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM documents WHERE kind='news' ORDER BY published_at DESC LIMIT ?",(limit,))]
    return search_documents(db_path(),q,limit)

@app.post("/ingestion/run")
def run_ingestion(source_id:str|None=None):
    return {"results":ingest_all("config/sources.yaml",source_id)}

@app.get("/quality/ingestion")
def quality_ingestion():
    cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with connect(db_path()) as db:
        rows=db.execute("SELECT source_id,status,is_duplicate,duration_ms,published_at,ready_at FROM ingestion_log WHERE fetched_at>=?",(cutoff,)).fetchall()
    grouped={}
    for r in rows:
        g=grouped.setdefault(r["source_id"],{"attempts":0,"successes":0,"errors":0,"duplicates":0,"latencies_ms":[]})
        g["attempts"]+=1; g["successes"]+=r["status"]=="success"; g["errors"]+=r["status"]=="error"; g["duplicates"]+=r["is_duplicate"] or 0
        if r["duration_ms"] is not None: g["latencies_ms"].append(r["duration_ms"])
    for g in grouped.values():
        vals=sorted(g.pop("latencies_ms"))
        g["success_rate"]=g["successes"]/g["attempts"] if g["attempts"] else 0
        g["p50_ms"]=vals[len(vals)//2] if vals else None
        g["p95_ms"]=vals[min(len(vals)-1,int(len(vals)*.95))] if vals else None
    return {"period_days":7,"sources":grouped}
