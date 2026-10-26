"""News pipeline: fetch -> normalise -> store -> FTS index -> dedup/cluster -> ready. Everything is logged (WP-B18)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..config import get_config, get_sources
from ..connectors.base import ItemError
from ..connectors.registry import build_connector
from ..db import set_source_status, tx
from ..dedup import assign_clusters
from ..http import FetchError, PoliteClient
from ..models import Document
from ..search import index_news
from ..util import iso, iso_utc, new_id, now_utc


@dataclass
class RunSummary:
    run_id: str
    source_id: str
    status: str                       # ok | partial | failed
    items_total: int = 0
    items_ok: int = 0
    items_new: int = 0
    items_duplicate: int = 0
    items_failed: int = 0
    error_type: str | None = None
    detail: str | None = None
    http_status: int | None = None


def start_run(conn: sqlite3.Connection, source_id: str) -> str:
    run_id = new_id("run_")
    conn.execute("INSERT INTO fetch_runs(run_id,source_id,started_at,status) VALUES(?,?,?, 'running')",
                 (run_id, source_id, iso(now_utc())))
    return run_id


def finish_run(conn: sqlite3.Connection, s: RunSummary, attempts: int | None = None) -> None:
    conn.execute(
        """UPDATE fetch_runs SET finished_at=?, http_status=?, attempts=?, items_total=?, items_ok=?, items_failed=?,
           items_duplicate=?, items_new=?, status=?, error_type=?, detail=? WHERE run_id=?""",
        (iso(now_utc()), s.http_status, attempts, s.items_total, s.items_ok, s.items_failed, s.items_duplicate,
         s.items_new, s.status, s.error_type, s.detail, s.run_id))
    st = {"ok": "works", "partial": "partial", "failed": "failed"}[s.status]
    set_source_status(conn, s.source_id, st, s.detail if s.status != "ok" else None, success=s.status != "failed")


def store_documents(conn: sqlite3.Connection, source_id: str, run_id: str | None, docs: list[Document],
                    errors: list[ItemError] | None = None) -> dict:
    """Insert new documents, index, dedup, mark ready. Re-polled known URLs are skipped silently (idempotent)."""
    errors = errors or []
    new_ids, fetched_at_by_id = [], {}
    with tx(conn):
        for d in docs:
            if conn.execute("SELECT 1 FROM documents WHERE doc_id=?", (d.doc_id,)).fetchone():
                continue
            conn.execute(
                """INSERT INTO documents(doc_id,source_id,kind,origin,title,url,canonical_url,published_at,published_at_utc,
                   fetched_at,language,category,snippet,text,tier_weight,content_hash,stale)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d.doc_id, d.source_id, d.kind, d.origin, d.title, d.url, d.canonical_url, iso(d.published_at),
                 iso_utc(d.published_at), iso(d.fetched_at), d.language, d.category, d.snippet, d.text, d.tier_weight,
                 d.content_hash, int(d.stale)))
            index_news(conn, d.doc_id, d.title, d.snippet)
            new_ids.append(d.doc_id)
            fetched_at_by_id[d.doc_id] = d
        kinds = assign_clusters(conn, new_ids)
        ready = iso(now_utc())
        n_dup = 0
        for did in new_ids:
            d = fetched_at_by_id[did]
            kind = kinds.get(did)
            is_dup = kind in ("exact", "near")
            n_dup += is_dup
            conn.execute("UPDATE documents SET ready_at=? WHERE doc_id=?", (ready, did))
            conn.execute(
                """INSERT INTO ingestion_log(run_id,source_id,doc_id,published_at,fetched_at,ready_at,status,is_duplicate,detail)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id, source_id, did, iso(d.published_at), iso(d.fetched_at), ready,
                 "duplicate" if is_dup else "ready", int(is_dup), kind))
        for e in errors:
            conn.execute(
                """INSERT INTO ingestion_log(run_id,source_id,fetched_at,status,error_type,detail) VALUES(?,?,?, 'error', ?, ?)""",
                (run_id, source_id, iso(now_utc()), e.error_type, f"item#{e.index}: {e.detail}"[:300]))
    return {"new": len(new_ids), "duplicate": n_dup, "doc_ids": new_ids}


def run_source(conn: sqlite3.Connection, source_id: str, http: PoliteClient | None = None,
               now: datetime | None = None) -> RunSummary:
    own = http is None
    http = http or PoliteClient()
    run_id = start_run(conn, source_id)
    try:
        conn_ = build_connector(source_id, http)
        out = conn_.fetch(now=now)
    except (FetchError, NotImplementedError, KeyError) as e:
        et = e.error_type if isinstance(e, FetchError) else type(e).__name__
        s = RunSummary(run_id, source_id, "failed", error_type=et, detail=str(e)[:300],
                       http_status=getattr(e, "status", None))
        finish_run(conn, s, getattr(e, "attempts", None))
        conn.execute("INSERT INTO ingestion_log(run_id,source_id,fetched_at,status,error_type,detail) VALUES(?,?,?, 'error', ?, ?)",
                     (run_id, source_id, iso(now_utc()), et, str(e)[:300]))
        return s
    finally:
        if own:
            http.close()
    stats = store_documents(conn, source_id, run_id, out.items, out.item_errors)
    tol = get_config().get("news.parse_failure_tolerance", 0.01)
    failed = len(out.item_errors)
    status = "ok" if (out.items_total == 0 or failed / out.items_total <= tol) else "partial"
    s = RunSummary(run_id, source_id, status, out.items_total, len(out.items), stats["new"], stats["duplicate"],
                   failed, http_status=out.http_status)
    if status == "partial":
        s.error_type, s.detail = "item_errors", json.dumps([e.error_type for e in out.item_errors][:10])
    finish_run(conn, s, out.attempts)
    return s
