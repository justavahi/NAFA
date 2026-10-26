"""WP-B18: operational metrics Q-23..Q-27 computed from fetch_runs / ingestion_log / llm_calls (window: last N days)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import numpy as np

from .config import get_config
from .llm_gateway import usage_today
from .util import iso, now_utc, parse_iso_utc


def _since(days: int) -> str:
    return iso(now_utc() - timedelta(days=days))


def _pct(vals: list[float], q: float) -> float | None:
    return round(float(np.percentile(vals, q)), 1) if vals else None


def q23_parse_success(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Share of fetch attempts that got a response, parsed and wrote documents. 'partial' (>1% bad items) does not count as success."""
    out = []
    for r in conn.execute(
        """SELECT source_id, COUNT(*) attempts, SUM(status='ok') ok, SUM(status='partial') partial, SUM(status='failed') failed,
           SUM(items_total) items_total, SUM(items_failed) items_failed
           FROM fetch_runs WHERE started_at>=? AND status!='running' GROUP BY source_id ORDER BY source_id""", (_since(days),)):
        out.append({"source_id": r["source_id"], "attempts": r["attempts"], "ok": r["ok"], "partial": r["partial"], "failed": r["failed"],
                    "success_rate": round(r["ok"] / r["attempts"], 4) if r["attempts"] else None,
                    "item_parse_rate": round(1 - r["items_failed"] / r["items_total"], 4) if r["items_total"] else None})
    return out


def q24_latency(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """ready_at - published_at, seconds, p50/p95 per source. Items already older than latency.max_window_hours when first fetched
    are backfill (first run of a feed) and are excluded, otherwise the metric would measure the age of the feed, not our delay."""
    window = get_config().get("latency.max_window_hours", 24) * 3600
    per: dict[str, list[float]] = {}
    excluded: dict[str, int] = {}
    for r in conn.execute("SELECT source_id, published_at, fetched_at, ready_at FROM ingestion_log WHERE status IN ('ready','duplicate') AND ready_at IS NOT NULL AND fetched_at>=?",
                          (_since(days),)):
        pub, fet, rdy = (datetime.fromisoformat(r[k]) for k in ("published_at", "fetched_at", "ready_at"))
        if (fet - pub).total_seconds() > window:
            excluded[r["source_id"]] = excluded.get(r["source_id"], 0) + 1
            continue
        per.setdefault(r["source_id"], []).append(max(0.0, (rdy - pub).total_seconds()))
    return [{"source_id": s, "n": len(v), "p50_s": _pct(v, 50), "p95_s": _pct(v, 95), "excluded_backfill": excluded.get(s, 0)}
            for s, v in sorted(per.items())]


def q25_tokens(conn: sqlite3.Connection, days: int = 7) -> dict:
    from .db import llm_ledger
    with llm_ledger(conn) as ledger:
        return _q25_tokens(ledger, days)


def _q25_tokens(conn: sqlite3.Connection, days: int) -> dict:
    by_op = [dict(r) for r in conn.execute(
        """SELECT operation, COUNT(*) calls, SUM(cache_hit) cache_hits, SUM(tokens_in) tokens_in, SUM(tokens_out) tokens_out, ROUND(SUM(cost_rub),4) cost_rub
           FROM llm_calls WHERE ts>=? AND status='ok' GROUP BY operation ORDER BY operation""", (_since(days),))]
    limit = get_config().get("budget.daily_limit_rub", 500)
    today = usage_today(conn)
    blocked = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE ts>=? AND status='blocked'", (_since(days),)).fetchone()[0]
    return {"by_operation": by_op, "today": {**today, "remaining_rub": round(limit - today["cost_rub"], 4)}, "blocked_calls": blocked}


def q26_dups_errors(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    out = []
    for r in conn.execute(
        """SELECT source_id, COUNT(*) total, SUM(is_duplicate) dups, SUM(status='error') errors
           FROM ingestion_log WHERE fetched_at>=? GROUP BY source_id ORDER BY source_id""", (_since(days),)):
        out.append({"source_id": r["source_id"], "records": r["total"], "duplicate_share": round(r["dups"] / r["total"], 4),
                    "error_share": round(r["errors"] / r["total"], 4)})
    return out


def q27_daily(conn: sqlite3.Connection, days: int = 7) -> dict:
    news = [dict(r) for r in conn.execute(
        """SELECT substr(fetched_at,1,10) day, COUNT(*) received, SUM(status!='error') parsed, SUM(ready_at IS NOT NULL) AS "indexed"
           FROM ingestion_log WHERE fetched_at>=? GROUP BY day ORDER BY day""", (_since(days),))]
    alfa = [dict(r) for r in conn.execute(
        """SELECT substr(parsed_at,1,10) day, COUNT(*) docs, SUM(pages) pages FROM alfa_docs WHERE parsed_at>=? AND parse_status IN ('parsed','partial')
           GROUP BY day ORDER BY day""", (_since(days),))]
    return {"news_by_day": news, "alfa_pages_by_day": alfa,
            "note": "'classified' stage is added by the DA/PE classifier; it is not produced by the ingestion layer"}


def ingestion_summary(conn: sqlite3.Connection, days: int = 7) -> dict:
    """Payload of GET /quality/ingestion."""
    return {"window_days": days, "q23_parse_success": q23_parse_success(conn, days), "q24_latency": q24_latency(conn, days),
            "q25_tokens": q25_tokens(conn, days), "q26_duplicates_errors": q26_dups_errors(conn, days), "q27_processing": q27_daily(conn, days)}
