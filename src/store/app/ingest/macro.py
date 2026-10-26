"""Macro series (M-xx): upsert with revision tracking, plus manual/fallback points (e.g. inflation from DOC-04)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from ..connectors.registry import build_connector
from ..db import tx
from ..http import FetchError, PoliteClient
from ..models import MacroPoint
from ..util import iso, now_utc
from .news import RunSummary, finish_run, start_run


def upsert_points(conn: sqlite3.Connection, points: list[MacroPoint], fetched_at: datetime | None = None) -> dict:
    """Same value again -> only fetched_at moves. Changed value -> new revision (history kept)."""
    fetched = iso(fetched_at or now_utc())
    new = changed = same = 0
    with tx(conn):
        for p in points:
            d = p.date.isoformat()
            cur = conn.execute("SELECT value, revision FROM macro_series WHERE series_id=? AND date=? ORDER BY revision DESC LIMIT 1",
                               (p.series_id, d)).fetchone()
            if cur is None:
                conn.execute("INSERT INTO macro_series(series_id,date,value,unit,source_id,fetched_at,revision) VALUES(?,?,?,?,?,?,0)",
                             (p.series_id, d, p.value, p.unit, p.source_id, fetched)); new += 1
            elif abs(cur["value"] - p.value) < 1e-12:
                conn.execute("UPDATE macro_series SET fetched_at=? WHERE series_id=? AND date=? AND revision=?",
                             (fetched, p.series_id, d, cur["revision"])); same += 1
            else:
                conn.execute("INSERT INTO macro_series(series_id,date,value,unit,source_id,fetched_at,revision) VALUES(?,?,?,?,?,?,?)",
                             (p.series_id, d, p.value, p.unit, p.source_id, fetched, cur["revision"] + 1)); changed += 1
    return {"new": new, "changed": changed, "unchanged": same}


def run_macro_source(conn: sqlite3.Connection, source_id: str, http: PoliteClient | None = None, **kwargs) -> RunSummary:
    own = http is None
    http = http or PoliteClient()
    run_id = start_run(conn, source_id)
    try:
        out = build_connector(source_id, http).fetch(**kwargs)
    except FetchError as e:
        s = RunSummary(run_id, source_id, "failed", error_type=e.error_type, detail=str(e)[:300], http_status=e.status)
        finish_run(conn, s, e.attempts)
        return s
    finally:
        if own:
            http.close()
    stats = upsert_points(conn, out.items)
    status = "ok" if not out.item_errors else "partial"
    s = RunSummary(run_id, source_id, status, out.items_total, len(out.items), stats["new"], 0, len(out.item_errors),
                   http_status=out.http_status)
    if out.item_errors:
        s.error_type, s.detail = "item_errors", "; ".join(f"{e.error_type}:{e.detail}" for e in out.item_errors)[:300]
    finish_run(conn, s, out.attempts)
    return s


def backfill_cbr_daily(conn: sqlite3.Connection, start: date, end: date, http: PoliteClient | None = None) -> list[RunSummary]:
    """One request per day against XML_daily?date_req=... (the verified endpoint). Upsert is keyed by the date inside the XML."""
    own = http is None
    http = http or PoliteClient()
    out, d = [], start
    try:
        while d <= end:
            out.append(run_macro_source(conn, "SRC-02", http, day=d))
            d += timedelta(days=1)
    finally:
        if own:
            http.close()
    return out


def add_manual_point(conn: sqlite3.Connection, series_id: str, day: date, value: float, unit: str, source_id: str) -> dict:
    """Fallback path (spec section 5): e.g. weekly CPI taken from DOC-04 with source and date recorded."""
    return upsert_points(conn, [MacroPoint(series_id=series_id, date=day, value=value, unit=unit, source_id=source_id)])


def get_series(conn: sqlite3.Connection, series_id: str, start: str | None = None, end: str | None = None) -> list[dict]:
    """Tool `get_macro_series`: latest revision per date."""
    sql = """SELECT m.date, m.value, m.unit, m.source_id, m.fetched_at, m.revision FROM macro_series m
             JOIN (SELECT date, MAX(revision) r FROM macro_series WHERE series_id=? GROUP BY date) x
               ON x.date=m.date AND x.r=m.revision WHERE m.series_id=?"""
    args: list = [series_id, series_id]
    if start:
        sql += " AND m.date>=?"; args.append(start)
    if end:
        sql += " AND m.date<=?"; args.append(end)
    return [dict(r) for r in conn.execute(sql + " ORDER BY m.date", args)]
