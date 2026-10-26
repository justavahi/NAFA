"""Worker: runs every enabled source on its own interval (APScheduler). Idempotent jobs, no overlapping runs per source."""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from .config import get_sources
from .connectors.registry import PARSERS, is_macro
from .db import connect, init_db, sync_sources
from .http import PoliteClient
from .ingest.macro import run_macro_source
from .ingest.news import run_source

log = logging.getLogger("nafa.worker")
_http: PoliteClient | None = None


def job(source_id: str) -> None:
    global _http
    _http = _http or PoliteClient()
    conn = connect()
    try:
        s = run_macro_source(conn, source_id, _http) if is_macro(source_id) else run_source(conn, source_id, _http)
        log.info("%s: %s total=%s new=%s failed=%s %s", source_id, s.status, s.items_total, s.items_new, s.items_failed, s.error_type or "")
    except Exception:  # noqa: BLE001 - a crashing job must not kill the worker
        log.exception("job %s crashed", source_id)
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = init_db()
    sync_sources(conn)
    conn.close()
    sched = BlockingScheduler(timezone="UTC")
    for sid, cfg in get_sources().items():
        if cfg.get("enabled", True) and cfg.get("parser") in PARSERS and cfg.get("schedule_minutes"):
            sched.add_job(job, "interval", minutes=cfg["schedule_minutes"], args=[sid], id=sid, max_instances=1, coalesce=True,
                          jitter=20, next_run_time=None)
            sched.add_job(job, args=[sid], id=f"{sid}-boot")       # one immediate run at start
            log.info("scheduled %s every %s min", sid, cfg["schedule_minutes"])
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
