"""Command line: python -m app.cli <command> --help"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from . import bank_view as bv
from .check_sources import check_all
from .config import ROOT, data_dir
from .db import connect, init_db, sync_sources
from .models import BankViewIn


def _p(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="nafa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("sync-sources")
    sub.add_parser("db-info")
    c = sub.add_parser("check-sources"); c.add_argument("--source")
    c = sub.add_parser("run-source"); c.add_argument("source_id", nargs="?"); c.add_argument("--all", action="store_true")
    c = sub.add_parser("backfill-fx"); c.add_argument("--start", required=True); c.add_argument("--end", required=True)
    c = sub.add_parser("load-clients"); c.add_argument("--uid", required=True); c.add_argument("--links"); c.add_argument("--payments"); c.add_argument("--force", action="store_true")
    c = sub.add_parser("corpus-parse"); c.add_argument("directory"); c.add_argument("--force", action="store_true")
    sub.add_parser("corpus-report")
    c = sub.add_parser("corpus-verify"); c.add_argument("chunk_id"); c.add_argument("--text"); c.add_argument("--numbers-json")
    c = sub.add_parser("corpus-verify-key"); c.add_argument("csv", nargs="?", default=str(ROOT / "data" / "ref" / "key_numbers_to_verify.csv"))
    c.add_argument("--confirm-reviewed", action="store_true", help="Mark matching chunks manual ONLY after a human reviews their original pages")
    c = sub.add_parser("bankview-seed"); c.add_argument("path", nargs="?", default=str(ROOT / "data" / "bank_view_seed.csv"))
    c = sub.add_parser("bankview-list"); c.add_argument("--indicator"); c.add_argument("--horizon"); c.add_argument("--as-of")
    c = sub.add_parser("bankview-add")
    for a in ("indicator", "horizon", "as_of"):
        c.add_argument(f"--{a.replace('_', '-')}", required=True)
    for a in ("value", "unit", "stance", "doc_id", "page", "quote_span", "note"):
        c.add_argument(f"--{a.replace('_', '-')}")
    c.add_argument("--verified-by", default="manual", choices=["manual", "auto", "seed"])
    c = sub.add_parser("macro-add")
    c.add_argument("--series", required=True); c.add_argument("--date", required=True); c.add_argument("--value", type=float, required=True)
    c.add_argument("--unit", required=True); c.add_argument("--source", required=True)
    c = sub.add_parser("byo"); c.add_argument("--text"); c.add_argument("--url"); c.add_argument("--title")
    c = sub.add_parser("synth"); c.add_argument("--out", default=str(data_dir() / "synthetic"))
    c = sub.add_parser("search-news"); c.add_argument("query"); c.add_argument("-k", type=int, default=5)
    c = sub.add_parser("search-alfa"); c.add_argument("query"); c.add_argument("-k", type=int, default=5)
    c = sub.add_parser("search-alfa-eval"); c.add_argument("csv", nargs="?", default=str(ROOT / "data" / "ref" / "alfa_search_golden.csv")); c.add_argument("-k", type=int, default=3)
    c = sub.add_parser("quality"); c.add_argument("--days", type=int, default=7)
    c = sub.add_parser("dedup-eval"); c.add_argument("pairs_csv", help="CSV: doc_id_a,doc_id_b,label(1/0)")
    c = sub.add_parser("report"); c.add_argument("--out", default=str(ROOT / "docs" / "backend"))
    sub.add_parser("worker")
    c = sub.add_parser("serve"); c.add_argument("--host", default="0.0.0.0"); c.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)

    if a.cmd == "worker":
        from .scheduler import main as worker_main
        worker_main(); return 0
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("app.api:app", host=a.host, port=a.port); return 0

    conn = init_db()
    sync_sources(conn)
    if a.cmd in ("init-db", "sync-sources"):
        _p({"ok": True})
    elif a.cmd == "db-info":
        from .config import db_path
        p = db_path()
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table') AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
            "AND name NOT LIKE '%\\_fts\\_%' ESCAPE '\\' ORDER BY name")]
        _p({"db_path": str(p), "exists": p.exists(), "size_mb": round(p.stat().st_size / 1e6, 2),
            "side_files": [x.name for x in p.parent.glob(p.name + "-*")],
            "rows": {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}})
    elif a.cmd == "check-sources":
        _p(check_all(conn, a.source))
    elif a.cmd == "run-source":
        from .connectors.registry import PARSERS, is_macro
        from .config import get_sources
        from .ingest.macro import run_macro_source
        from .ingest.news import run_source
        ids = [s for s, c in get_sources().items() if c.get("enabled", True) and c.get("parser") in PARSERS] if a.all else [a.source_id]
        _p([(run_macro_source if is_macro(s) else run_source)(conn, s).__dict__ for s in ids])
    elif a.cmd == "backfill-fx":
        from .ingest.macro import backfill_cbr_daily
        runs = backfill_cbr_daily(conn, date.fromisoformat(a.start), date.fromisoformat(a.end))
        _p({"days": len(runs), "failed": sum(r.status == "failed" for r in runs)})
    elif a.cmd == "load-clients":
        from .ingest.clients import load_all
        _p(load_all(conn, a.uid, a.links, a.payments, a.force))
    elif a.cmd == "corpus-parse":
        from .ingest.alfa_corpus import parse_corpus
        from .llm_gateway import LLMGateway
        result = parse_corpus(conn, a.directory, LLMGateway(conn), a.force)
        _p(result)
        # Make a complete extraction failure visible to shells/automation as well as JSON.
        if result["failed"] and not result["parsed"] and not result["partial"] and not result["skipped"]:
            return 2
    elif a.cmd == "corpus-report":
        from .ingest.alfa_corpus import corpus_report, load_registry
        load_registry(conn); _p(corpus_report(conn))
    elif a.cmd == "corpus-verify":
        from .ingest.alfa_corpus import verify_chunk
        _p({"verified": verify_chunk(conn, a.chunk_id, a.text, json.loads(a.numbers_json) if a.numbers_json else None)})
    elif a.cmd == "corpus-verify-key":
        from .ingest.alfa_corpus import verify_key_numbers
        _p(verify_key_numbers(conn, a.csv, confirm_review=a.confirm_reviewed))
    elif a.cmd == "bankview-seed":
        _p({"seeded": bv.seed_from_csv(conn, a.path)})
    elif a.cmd == "bankview-list":
        _p(bv.get_bank_view(conn, a.indicator, a.horizon, a.as_of) if a.indicator else bv.list_bank_view(conn))
    elif a.cmd == "bankview-add":
        _p({"id": bv.add_bank_view(conn, BankViewIn(
            indicator=a.indicator, horizon=a.horizon, as_of=date.fromisoformat(a.as_of), value=float(a.value) if a.value else None,
            unit=a.unit, stance=a.stance, doc_id=a.doc_id, page=int(a.page) if a.page else None, quote_span=a.quote_span,
            verified_by=a.verified_by, note=a.note))})
    elif a.cmd == "macro-add":
        from .ingest.macro import add_manual_point
        _p(add_manual_point(conn, a.series, date.fromisoformat(a.date), a.value, a.unit, a.source))
    elif a.cmd == "byo":
        from .ingest import byo
        if bool(a.text) == bool(a.url):
            print("provide exactly one of --text/--url", file=sys.stderr); return 2
        _p(byo.ingest_text(conn, a.text, a.title) if a.text else byo.ingest_url(conn, a.url))
    elif a.cmd == "synth":
        from .ingest.clients import load_all
        from .synth import generate_clients, load_synthetic_news
        paths = generate_clients(Path(a.out))
        _p({"files": {k: str(v) for k, v in paths.items()},
            "loaded": load_all(conn, paths["clients"], paths["links"], paths["payments"]), "news": load_synthetic_news(conn)})
    elif a.cmd == "search-news":
        from .search import search_news
        _p(search_news(conn, a.query, k=a.k))
    elif a.cmd == "search-alfa":
        from .search import search_alfa_analytics
        _p(search_alfa_analytics(conn, a.query, k=a.k))
    elif a.cmd == "search-alfa-eval":
        from .search import evaluate_alfa_queries
        _p(evaluate_alfa_queries(conn, a.csv, a.k))
    elif a.cmd == "quality":
        from .quality import ingestion_summary
        _p(ingestion_summary(conn, a.days))
    elif a.cmd == "dedup-eval":
        import csv
        from .dedup import evaluate_pairs
        with open(a.pairs_csv, encoding="utf-8-sig") as f:
            pairs = [(r[0], r[1], int(r[2])) for r in csv.reader(f) if r and r[0] != "doc_id_a"]
        _p(evaluate_pairs(conn, pairs))
    elif a.cmd == "report":
        from .report import write_reports
        _p([str(p) for p in write_reports(conn, a.out)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
