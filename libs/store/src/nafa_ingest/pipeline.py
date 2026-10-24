from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .fetch import fetch_url, make_session, read_local_file
from .normalize import utc_now
from .parsers import parse_csv, parse_html, parse_json, parse_pdf, parse_rss, parse_xml
from .storage import database, init_db, store_dataset_rows, store_documents, store_macro_series, upsert_source


def _absolute_config_path(source: dict[str, Any], field: str) -> Path:
    path = Path(source[field]).expanduser()
    if not path.is_absolute():
        path = Path(source["_config_dir"]) / path
    return path.resolve()


def _source_files(source: dict[str, Any], config_dir: Path) -> list[Path]:
    if source.get("path_glob"):
        pattern = Path(source["path_glob"]).expanduser()
        if not pattern.is_absolute():
            pattern = config_dir / pattern
        return [Path(item).resolve() for item in sorted(glob.glob(str(pattern)))]
    if source.get("path"):
        path = Path(source["path"]).expanduser()
        if not path.is_absolute():
            path = config_dir / path
        return [path.resolve()]
    return []


def _parse_payload(source: dict[str, Any], payload: bytes, settings: dict[str, Any], *, fetched_at: str, url: str) -> tuple[list[dict], list[dict], list[dict]]:
    source_type = source["type"].lower()
    if source_type == "rss":
        return parse_rss(payload, source, settings, fetched_at=fetched_at, base_url=url), [], []
    if source_type == "html":
        parsed = parse_html(payload, source, settings, fetched_at=fetched_at, base_url=url)
    elif source_type == "json":
        parsed = parse_json(payload, source, settings, fetched_at=fetched_at, base_url=url)
    elif source_type == "xml":
        parsed = parse_xml(payload, source, settings, fetched_at=fetched_at, base_url=url)
    elif source_type == "pdf":
        parsed = parse_pdf(payload, source, settings, fetched_at=fetched_at, document_url=url)
    else:
        raise ValueError(f"Unsupported remote source type: {source_type}")

    if source.get("record_type") == "macro_series":
        return [], parsed, []
    return parsed, [], []


def ingest_source(source: dict[str, Any], settings: dict[str, Any], *, database_path: str | Path, config_dir: str | Path | None = None) -> dict[str, Any]:
    """Fetch, parse, normalize and store one configured source.

    Example:
        settings, sources, config_dir = load_config("config/sources.yaml")
        result = ingest_source(sources[0], settings,
                               database_path=settings["database_path"],
                               config_dir=config_dir)
    """
    source = dict(source)
    config_dir = Path(config_dir or source.get("_config_dir") or Path.cwd()).resolve()
    started_at = utc_now()
    parsed_count = 0
    saved_count = 0
    status = "success"
    error_type = None
    error_message = None
    init_db(database_path)

    try:
        with database(database_path) as db:
            upsert_source(db, source)
            db.execute("UPDATE sources SET last_attempt = ? WHERE source_id = ?", (started_at, source["id"]))

        source_type = source["type"].lower()
        if source_type == "csv":
            files = _source_files(source, config_dir)
            if not files:
                raise FileNotFoundError(f"No CSV files matched configured path for {source['id']}")
            for path in files:
                rows = parse_csv(path, source)
                parsed_count += len(rows)
                with database(database_path) as db:
                    saved_count += store_dataset_rows(db, rows)
        elif source_type == "pdf" and (source.get("path") or source.get("path_glob")):
            files = _source_files(source, config_dir)
            if not files:
                raise FileNotFoundError(f"No PDF files matched configured path for {source['id']}")
            for path in files:
                per_file_source = {**source, "path": str(path)}
                payload = read_local_file(path)
                fetched_at = utc_now()
                docs = parse_pdf(payload, per_file_source, settings, fetched_at=fetched_at, document_url=path.as_uri())
                parsed_count += len(docs)
                with database(database_path) as db:
                    saved_count += store_documents(db, docs)
        elif source_type in {"rss", "html", "json", "xml", "pdf"}:
            if not source.get("url"):
                raise ValueError(f"Source {source['id']} needs a URL or a local path")
            with make_session(settings) as session:
                payload, final_url, _ = fetch_url(source["url"], settings, session=session)
            fetched_at = utc_now()
            docs, macro, rows = _parse_payload(source, payload, settings, fetched_at=fetched_at, url=final_url)
            parsed_count += len(docs) + len(macro) + len(rows)
            with database(database_path) as db:
                saved_count += store_documents(db, docs)
                saved_count += store_macro_series(db, macro)
                saved_count += store_dataset_rows(db, rows)
        else:
            raise ValueError(f"Unsupported source type: {source_type}")

    except Exception as exc:
        status = "error"
        error_type = type(exc).__name__
        # Avoid logging downloaded text or CSV row data into the error table.
        error_message = str(exc)[:500]

    finished_at = utc_now()
    with database(database_path) as db:
        upsert_source(db, source)
        db.execute("""
            INSERT INTO ingestion_runs(source_id, started_at, finished_at, status,
                                       parsed_count, saved_count, error_type, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (source["id"], started_at, finished_at, status, parsed_count, saved_count, error_type, error_message))
        if status == "success":
            db.execute("UPDATE sources SET last_success = ? WHERE source_id = ?", (finished_at, source["id"]))

    return {
        "source_id": source["id"], "status": status,
        "parsed": parsed_count, "saved_or_updated": saved_count,
        "error_type": error_type, "error": error_message,
    }


def ingest_all(config_path: str | Path, *, source_ids: set[str] | None = None, include_disabled: bool = False) -> list[dict[str, Any]]:
    """Ingest configured sources, optionally limiting to source IDs."""
    settings, sources, config_dir = load_config(config_path)
    database_path = settings["database_path"]
    init_db(database_path)
    results = []
    for source in sources:
        if source_ids and source["id"] not in source_ids:
            continue
        if not source.get("enabled", True) and not include_disabled:
            continue
        results.append(ingest_source(source, settings, database_path=database_path, config_dir=config_dir))
    return results
