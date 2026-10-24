from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


def connect_db(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with dictionary-like rows and safe defaults."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def database(database_path: str | Path):
    connection = connect_db(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(database_path: str | Path) -> None:
    """Create the small set of storage tables; safe to call repeatedly."""
    with database(database_path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            source_id TEXT PRIMARY KEY,
            name TEXT,
            type TEXT NOT NULL,
            url TEXT,
            config_json TEXT NOT NULL,
            last_attempt TEXT,
            last_success TEXT
        );
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            language TEXT,
            category TEXT,
            snippet TEXT,
            text TEXT,
            tier_weight REAL NOT NULL,
            content_hash TEXT NOT NULL,
            cluster_id TEXT,
            stale INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_documents_published_at ON documents(published_at);
        CREATE INDEX IF NOT EXISTS idx_documents_source_id ON documents(source_id);
        CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
        CREATE TABLE IF NOT EXISTS document_sources (
            doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            source_item_id TEXT,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (doc_id, source_id, source_item_id)
        );
        CREATE TABLE IF NOT EXISTS macro_series (
            series_id TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            source_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            revision TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (series_id, date, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_macro_series_date ON macro_series(series_id, date);
        CREATE TABLE IF NOT EXISTS dataset_rows (
            source_id TEXT NOT NULL,
            dataset_name TEXT NOT NULL,
            source_file TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            row_key TEXT,
            payload_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (source_id, source_file, row_number)
        );
        CREATE INDEX IF NOT EXISTS idx_dataset_name ON dataset_rows(dataset_name);
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            parsed_count INTEGER NOT NULL DEFAULT 0,
            saved_count INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ingestion_source_time ON ingestion_runs(source_id, started_at);
        """)


def upsert_source(db: sqlite3.Connection, source: dict[str, Any]) -> None:
    safe_config = {k: v for k, v in source.items() if not k.startswith("_")}
    db.execute("""
        INSERT INTO sources(source_id, name, type, url, config_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          name=excluded.name, type=excluded.type, url=excluded.url,
          config_json=excluded.config_json
    """, (
        source["id"], source.get("name"), source["type"], source.get("url"),
        json.dumps(safe_config, ensure_ascii=False, default=str),
    ))


def store_documents(db: sqlite3.Connection, documents: Iterable[dict[str, Any]]) -> int:
    saved = 0
    for document in documents:
        payload_json = json.dumps(document.get("payload", {}), ensure_ascii=False, default=str)
        db.execute("""
            INSERT INTO documents(
                doc_id, source_id, kind, title, url, canonical_url, published_at,
                fetched_at, language, category, snippet, text, tier_weight,
                content_hash, cluster_id, stale, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_url) DO UPDATE SET
                title=CASE WHEN excluded.title != '(untitled)' THEN excluded.title ELSE documents.title END,
                published_at=COALESCE(excluded.published_at, documents.published_at),
                fetched_at=excluded.fetched_at,
                language=COALESCE(excluded.language, documents.language),
                category=COALESCE(excluded.category, documents.category),
                snippet=COALESCE(excluded.snippet, documents.snippet),
                text=COALESCE(excluded.text, documents.text),
                content_hash=excluded.content_hash,
                payload_json=excluded.payload_json
        """, (
            document["doc_id"], document["source_id"], document["kind"],
            document["title"], document["url"], document["canonical_url"],
            document.get("published_at"), document["fetched_at"],
            document.get("language"), document.get("category"), document.get("snippet"),
            document.get("text"), document["tier_weight"], document["content_hash"],
            document.get("cluster_id"), int(document.get("stale", False)), payload_json,
        ))
        stored = db.execute("SELECT doc_id FROM documents WHERE canonical_url = ?", (document["canonical_url"],)).fetchone()
        db.execute("""
            INSERT OR IGNORE INTO document_sources(doc_id, source_id, source_item_id, fetched_at)
            VALUES (?, ?, ?, ?)
        """, (stored["doc_id"], document["source_id"], document.get("source_item_id"), document["fetched_at"]))
        saved += 1
    return saved


def store_macro_series(db: sqlite3.Connection, records: Iterable[dict[str, Any]]) -> int:
    saved = 0
    for record in records:
        db.execute("""
            INSERT INTO macro_series(series_id, date, value, unit, source_id, fetched_at, revision, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, date, source_id) DO UPDATE SET
                value=excluded.value, unit=excluded.unit,
                fetched_at=excluded.fetched_at, revision=excluded.revision,
                payload_json=excluded.payload_json
        """, (
            record["series_id"], record["date"], float(record["value"]),
            record.get("unit", ""), record["source_id"], record["fetched_at"],
            record.get("revision"), json.dumps(record, ensure_ascii=False, default=str),
        ))
        saved += 1
    return saved


def store_dataset_rows(db: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    saved = 0
    for row in rows:
        db.execute("""
            INSERT INTO dataset_rows(source_id, dataset_name, source_file, row_number, row_key, payload_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_file, row_number) DO UPDATE SET
                dataset_name=excluded.dataset_name, row_key=excluded.row_key,
                payload_json=excluded.payload_json, fetched_at=excluded.fetched_at
        """, (
            row["source_id"], row["dataset_name"], row["source_file"], row["row_number"],
            row.get("row_key"), json.dumps(row.get("payload", {}), ensure_ascii=False, default=str),
            row["fetched_at"],
        ))
        saved += 1
    return saved


def search_documents(database_path: str | Path, query: str = "", *, limit: int = 50, source_id: str | None = None) -> list[dict[str, Any]]:
    """Simple text search that can be replaced later by FTS5/BM25."""
    sql = "SELECT * FROM documents WHERE 1=1"
    params: list[Any] = []
    if query:
        sql += " AND (title LIKE ? OR snippet LIKE ? OR text LIKE ?)"
        pattern = f"%{query}%"
        params.extend([pattern, pattern, pattern])
    if source_id:
        sql += " AND source_id = ?"
        params.append(source_id)
    sql += " ORDER BY published_at DESC, fetched_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with database(database_path) as db:
        rows = db.execute(sql, params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item["stale"] = bool(item["stale"])
            output.append(item)
        return output
