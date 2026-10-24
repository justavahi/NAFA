import json, sqlite3
from pathlib import Path
from .normalize import now_iso

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS documents(
 doc_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
 url TEXT NOT NULL, canonical_url TEXT NOT NULL UNIQUE, published_at TEXT, fetched_at TEXT NOT NULL,
 language TEXT, category TEXT, snippet TEXT, text TEXT, tier_weight REAL NOT NULL,
 content_hash TEXT NOT NULL, cluster_id TEXT, stale INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_published ON documents(published_at);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);
CREATE TABLE IF NOT EXISTS chunks(
 chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, page INTEGER NOT NULL, block_type TEXT NOT NULL,
 text TEXT, numbers_json TEXT NOT NULL DEFAULT '[]', as_of_date TEXT, extraction_model TEXT,
 extraction_confidence REAL, needs_manual_check INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);
CREATE TABLE IF NOT EXISTS macro_series(
 series_id TEXT NOT NULL, date TEXT NOT NULL, value REAL NOT NULL, unit TEXT,
 source_id TEXT NOT NULL, fetched_at TEXT NOT NULL, revision TEXT,
 PRIMARY KEY(series_id,date,source_id)
);
CREATE TABLE IF NOT EXISTS client_rows(
 source_id TEXT NOT NULL, row_key TEXT NOT NULL, data_json TEXT NOT NULL,
 imported_at TEXT NOT NULL, PRIMARY KEY(source_id,row_key)
);
CREATE TABLE IF NOT EXISTS payment_rows(
 source_id TEXT NOT NULL, row_key TEXT NOT NULL, data_json TEXT NOT NULL,
 imported_at TEXT NOT NULL, PRIMARY KEY(source_id,row_key)
);
CREATE TABLE IF NOT EXISTS bank_view(
 id INTEGER PRIMARY KEY AUTOINCREMENT, indicator TEXT NOT NULL, horizon TEXT NOT NULL,
 value REAL, unit TEXT, stance TEXT, as_of TEXT NOT NULL, doc_id TEXT, page INTEGER,
 quote_span TEXT, version_rank INTEGER NOT NULL DEFAULT 1, superseded_by INTEGER,
 extraction_method TEXT, verified_by TEXT,
 FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_bank_view_active ON bank_view(indicator,horizon,superseded_by);
CREATE TABLE IF NOT EXISTS ingestion_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
 doc_id TEXT, published_at TEXT, fetched_at TEXT NOT NULL, ready_at TEXT,
 status TEXT NOT NULL, error_type TEXT, is_duplicate INTEGER NOT NULL DEFAULT 0,
 rows_seen INTEGER DEFAULT 0, rows_written INTEGER DEFAULT 0, duration_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_ingestion_time ON ingestion_log(fetched_at);
CREATE TABLE IF NOT EXISTS metric_values(
 metric_id TEXT NOT NULL, date TEXT NOT NULL, value REAL, unit TEXT, source_id TEXT,
 fetched_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'collected',
 details_json TEXT, PRIMARY KEY(metric_id,date,source_id)
);
CREATE TABLE IF NOT EXISTS trace_events(
 trace_id TEXT NOT NULL, card_id TEXT, step TEXT NOT NULL, tool TEXT, args_redacted TEXT,
 started_at TEXT NOT NULL, duration_ms REAL, model TEXT, tokens_in INTEGER, tokens_out INTEGER,
 cost_rub REAL, result_ref TEXT, status TEXT, retry_of TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage(
 id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, operation TEXT, model TEXT,
 tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, cost_rub REAL DEFAULT 0,
 cache_hit INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards(
 card_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(doc_id UNINDEXED,title,snippet,text,content='');
"""

def connect(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def initialize_database(path):
    with connect(path) as db: db.executescript(SCHEMA)

def upsert_document(db, rec):
    old=db.execute("SELECT doc_id FROM documents WHERE canonical_url=?",(rec["canonical_url"],)).fetchone()
    cols=list(rec)
    db.execute(f"INSERT INTO documents({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) "
       "ON CONFLICT(canonical_url) DO UPDATE SET "
       "title=excluded.title, published_at=COALESCE(excluded.published_at,documents.published_at), "
       "fetched_at=excluded.fetched_at, snippet=COALESCE(excluded.snippet,documents.snippet), "
       "content_hash=excluded.content_hash, tier_weight=excluded.tier_weight",
       [rec[c] for c in cols])
    db.execute("INSERT INTO documents_fts(doc_id,title,snippet,text) VALUES(?,?,?,?)",
       (rec["doc_id"],rec["title"],rec.get("snippet"),rec.get("text")))
    return old is not None

def save_macro(db, rows):
    now=now_iso()
    for row in rows:
        db.execute("""INSERT INTO macro_series(series_id,date,value,unit,source_id,fetched_at,revision)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(series_id,date,source_id) DO UPDATE SET
        value=excluded.value,unit=excluded.unit,fetched_at=excluded.fetched_at""",
        (row["series_id"],row["date"],row["value"],row.get("unit"),row["source_id"],now,row.get("revision")))

def import_frame(db, df, source_id, table):
    if table not in {"client_rows","payment_rows"}: raise ValueError("Unsupported import table")
    written=0
    for i,row in df.iterrows():
        data={str(k):(None if v is None or str(v)=="<NA>" else str(v)) for k,v in row.items()}
        import hashlib
        key=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cur=db.execute(f"INSERT OR IGNORE INTO {table}(source_id,row_key,data_json,imported_at) VALUES(?,?,?,?)",
                       (source_id,key,json.dumps(data,ensure_ascii=False),now_iso()))
        written += cur.rowcount
    return written

def search_documents(path, query, limit=20):
    with connect(path) as db:
        rows=db.execute("""SELECT d.* FROM documents_fts f JOIN documents d ON d.doc_id=f.doc_id
          WHERE documents_fts MATCH ? ORDER BY rank LIMIT ?""",(query,limit)).fetchall()
        return [dict(r) for r in rows]
