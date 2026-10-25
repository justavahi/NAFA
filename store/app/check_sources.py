"""`check-sources`: for every enabled source record FACTS (robots.txt result, test fetch, parse status, 3 samples) so that
docs/backend/01_parsing_sources.md and 02_sources_descriptions.md say what was checked, not "ok".
Terms-of-use review cannot be automated: legal.terms_checked_at stays null until a human fills it in."""
from __future__ import annotations

import json
import sqlite3

from .config import get_sources
from .connectors.registry import PARSERS, is_macro
from .http import PoliteClient
from .ingest.macro import run_macro_source
from .ingest.news import run_source
from .util import iso, now_utc


def check_source(conn: sqlite3.Connection, source_id: str, http: PoliteClient) -> dict:
    cfg = get_sources()[source_id]
    res: dict = {"source_id": source_id}
    if not cfg.get("enabled", True) or not cfg.get("url") or cfg.get("parser") not in PARSERS:
        res["status"] = "skipped (disabled / no URL / no connector)"
        return res
    robots = http.robots_status(cfg["url"])
    res["robots"] = robots
    row = conn.execute("SELECT legal_json FROM sources WHERE source_id=?", (source_id,)).fetchone()
    legal = json.loads(row["legal_json"] or "{}") if row else {}
    legal.update({"robots": robots, "robots_checked_at": iso(now_utc())})
    conn.execute("UPDATE sources SET legal_json=? WHERE source_id=?", (json.dumps(legal, ensure_ascii=False), source_id))
    if robots in ("disallowed",):
        from .db import set_source_status
        set_source_status(conn, source_id, "failed", "blocked by robots.txt")
        res["status"] = "failed: blocked by robots.txt"
        return res
    s = run_macro_source(conn, source_id, http) if is_macro(source_id) else run_source(conn, source_id, http)
    res.update({"status": s.status, "items_total": s.items_total, "items_failed": s.items_failed,
                "error_type": s.error_type, "detail": s.detail, "http_status": s.http_status})
    return res


def check_all(conn: sqlite3.Connection, only: str | None = None, http: PoliteClient | None = None) -> list[dict]:
    own = http is None
    http = http or PoliteClient()
    try:
        ids = [only] if only else list(get_sources())
        return [check_source(conn, sid, http) for sid in ids]
    finally:
        if own:
            http.close()
