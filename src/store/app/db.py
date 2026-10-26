"""SQLite access. One short-lived connection per unit of work; WAL lets the API and the worker share the file."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import db_path, get_series, get_sources
from .util import iso, now_utc

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    # FastAPI may execute dependency creation, endpoint and cleanup on different
    # worker threads. Connections remain per request; never share them concurrently.
    conn = sqlite3.connect(str(path or db_path()), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def llm_ledger(conn):
    """Optional existing shared usage/cache DB; never create a fresh budget ledger."""
    configured = os.getenv("NAFA_LLM_LEDGER_PATH")
    if not configured:
        yield conn
        return
    from .config import get_config
    limit = get_config().get("budget.daily_token_limit")
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("shared LLM ledger requires a positive daily token limit")
    path = Path(configured).resolve()
    if not path.is_file():
        raise ValueError("shared LLM ledger must already exist")
    current = conn.execute("PRAGMA database_list").fetchone()[2]
    if current and Path(current).resolve() == path:
        yield conn
        return
    ledger = connect(path)
    try:
        tables = {r[0] for r in ledger.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"llm_calls", "llm_cache"}.issubset(tables):
            raise ValueError("shared LLM ledger schema is missing")
        yield ledger
    finally:
        ledger.close()


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    conn = conn or connect()
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def sync_sources(conn: sqlite3.Connection) -> int:
    """Mirror config/sources.yaml and series.yaml into the DB (config stays the source of truth)."""
    n = 0
    for sid, s in get_sources().items():
        legal = s.get("legal") or {}
        existing = conn.execute("SELECT legal_json, status FROM sources WHERE source_id=?", (sid,)).fetchone()
        if existing and existing["legal_json"]:  # keep facts recorded by check-sources
            old = json.loads(existing["legal_json"])
            legal = {**legal, **{k: v for k, v in old.items() if v is not None}}
        enabled = 1 if s.get("enabled", True) else 0
        status = (existing["status"] if existing else "not_checked") if enabled else "disabled"
        extra = {k: s[k] for k in ("series", "quirks") if k in s}
        conn.execute(
            """INSERT INTO sources(source_id,name,owner,type,url,priority,tier_weight,enabled,parser,schedule_minutes,legal_json,config_json,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_id) DO UPDATE SET name=excluded.name, owner=excluded.owner, type=excluded.type,
                 url=excluded.url, priority=excluded.priority, tier_weight=excluded.tier_weight, enabled=excluded.enabled,
                 parser=excluded.parser, schedule_minutes=excluded.schedule_minutes, legal_json=excluded.legal_json,
                 config_json=excluded.config_json, status=excluded.status""",
            (sid, s.get("name"), s.get("owner"), s.get("type"), s.get("url"), s.get("priority"), s.get("tier_weight"),
             enabled, s.get("parser"), s.get("schedule_minutes"), json.dumps(legal, ensure_ascii=False),
             json.dumps(extra, ensure_ascii=False), status),
        )
        n += 1
    for ser, m in get_series().items():
        conn.execute(
            """INSERT INTO series_catalog(series_id,name,unit,source_id,frequency,priority) VALUES(?,?,?,?,?,?)
               ON CONFLICT(series_id) DO UPDATE SET name=excluded.name, unit=excluded.unit, source_id=excluded.source_id,
                 frequency=excluded.frequency, priority=excluded.priority""",
            (ser, m.get("name"), m.get("unit"), m.get("source_id"), m.get("frequency"), m.get("priority")),
        )
    return n


def set_source_status(conn, source_id: str, status: str, error: str | None = None, success: bool = False) -> None:
    now = iso(now_utc())
    conn.execute(
        "UPDATE sources SET status=?, status_checked_at=?, last_error=?, last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END WHERE source_id=?",
        (status, now, error, 1 if success else 0, now, source_id),
    )
