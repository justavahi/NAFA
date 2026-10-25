"""WP-B13 / C05: accept a news item the system has never seen (text or URL) and push it through the SAME pipeline
(normalise -> index -> dedup -> ready). No special branches for prepared scenarios. URL fetching is limited to a domain whitelist."""
from __future__ import annotations

import copy
import sqlite3

from bs4 import BeautifulSoup

from ..config import get_config, get_sources
from ..connectors.normalize import ItemFailure, build_document, clean_text
from ..http import FetchError, PoliteClient
from ..util import iso, now_utc, sha1
from .news import RunSummary, finish_run, start_run, store_documents

SOURCE_ID = "SRC-14"


def _result(conn, doc_id: str, extra: dict) -> dict:
    r = conn.execute("SELECT doc_id,title,url,published_at,cluster_id,dup_of,dup_kind,ready_at,origin,tier_weight FROM documents WHERE doc_id=?",
                     (doc_id,)).fetchone()
    return {**dict(r), **extra}


def ingest_text(conn: sqlite3.Connection, text: str, title: str | None = None, published_at: str | None = None,
                origin: str = "byo") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    limit = get_config().get("byo.max_text_chars", 20000)
    truncated = len(text) > limit
    text = text[:limit]
    now = now_utc()
    cfg = get_sources()[SOURCE_ID]
    raw = {
        "title": title or clean_text(text).split(". ")[0][:140],
        "link": f"byo://{sha1(text)}",
        "pubDate": published_at or iso(now),
        "description": text,
        "full_text": text,
    }
    run_id = start_run(conn, SOURCE_ID)
    try:
        doc = build_document(SOURCE_ID, cfg, raw, now, origin=origin)
    except ItemFailure as e:
        finish_run(conn, RunSummary(run_id, SOURCE_ID, "failed", 1, 0, 0, 0, 1, e.error_type, e.detail))
        raise ValueError(f"{e.error_type}: {e.detail}") from e
    stats = store_documents(conn, SOURCE_ID, run_id, [doc])
    finish_run(conn, RunSummary(run_id, SOURCE_ID, "ok", 1, 1, stats["new"], stats["duplicate"], 0))
    return _result(conn, doc.doc_id, {"truncated": truncated, "published_at_source": "user" if published_at else "ingest_time",
                                      "already_known": stats["new"] == 0})


def _extract_page(html: bytes) -> dict:
    soup = BeautifulSoup(html, "lxml")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"itemprop": n})
            if t and t.get("content"):
                return t["content"].strip()
        return None

    title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else None)
    desc = meta("og:description", "description", "twitter:description")
    if not desc:
        desc = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p")[:5])
    published = meta("article:published_time", "datePublished", "pubdate")
    if not published:
        t = soup.find("time", attrs={"datetime": True})
        published = t["datetime"] if t else None
    return {"title": title, "description": desc, "pubDate": published}


def ingest_url(conn: sqlite3.Connection, url: str, http: PoliteClient | None = None) -> dict:
    allowed = get_config().get("byo.allowed_domains", [])
    own = http is None
    http = http or PoliteClient()
    now = now_utc()
    run_id = start_run(conn, SOURCE_ID)
    try:
        res = http.get(url, allowed_domains=allowed)          # whitelist is re-checked on every redirect hop
    except FetchError as e:
        finish_run(conn, RunSummary(run_id, SOURCE_ID, "failed", 1, 0, 0, 0, 1, e.error_type, str(e)[:300], e.status))
        raise
    finally:
        if own:
            http.close()
    page = _extract_page(res.content)
    page["link"] = res.final_url
    source = copy.deepcopy(get_sources()[SOURCE_ID])
    source["legal"] = {"store": "snippet"}                       # a third-party page: keep only title + snippet
    source["tier_weight"] = 0.4
    published_source = "page" if page.get("pubDate") else "ingest_time"
    page["pubDate"] = page.get("pubDate") or iso(now)
    try:
        doc = build_document(SOURCE_ID, source, page, now, origin="byo")
    except ItemFailure as e:
        finish_run(conn, RunSummary(run_id, SOURCE_ID, "failed", 1, 0, 0, 0, 1, e.error_type, e.detail))
        raise ValueError(f"{e.error_type}: {e.detail}") from e
    stats = store_documents(conn, SOURCE_ID, run_id, [doc])
    finish_run(conn, RunSummary(run_id, SOURCE_ID, "ok", 1, 1, stats["new"], stats["duplicate"], 0, http_status=res.status))
    return _result(conn, doc.doc_id, {"published_at_source": published_source, "already_known": stats["new"] == 0})
