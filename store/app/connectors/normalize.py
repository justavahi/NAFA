"""Raw feed item -> Document (spec 4.2): canonical URL, tz-aware dates, snippet, hashes, stale flag."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from ..config import get_config
from ..models import Document
from ..nlp import detect_language
from ..util import local_tz, sha1


class ItemFailure(Exception):
    def __init__(self, error_type: str, detail: str = ""):
        super().__init__(f"{error_type}: {detail}")
        self.error_type, self.detail = error_type, detail


def canonicalize_url(url: str) -> str:
    drop = set(get_config().get("news.tracking_params", []))
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in drop and not k.lower().startswith("utm_")]
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(q), ""))


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "lxml").get_text(" ", strip=True)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(value: str | None) -> datetime:
    """RFC-822 (RSS) or ISO-8601. Naive values get the source's default offset (Moscow) instead of being dropped."""
    if not value or not value.strip():
        raise ItemFailure("missing_date")
    v = value.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ItemFailure("bad_date", v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt


def content_hash(title: str, snippet: str | None) -> str:
    norm = re.sub(r"\W+", " ", f"{title} {snippet or ''}".lower().replace("ё", "е")).strip()
    return sha1(norm)


def build_document(source_id: str, source_cfg: dict, raw: dict, fetched_at: datetime, *, origin: str = "feed",
                   kind: str = "news") -> Document:
    cfg = get_config()
    title = clean_text(raw.get("title"))
    if not title:
        raise ItemFailure("missing_title")
    link = (raw.get("link") or "").strip()
    if not link:
        raise ItemFailure("missing_link")
    published = parse_date(raw.get("pubDate") or raw.get("published"))
    snippet_full = clean_text(raw.get("description"))
    max_chars = cfg.get("news.snippet_max_chars", 500)
    snippet = snippet_full[:max_chars] or None

    store = (source_cfg.get("legal") or {}).get("store", "snippet")
    text = None
    if store == "full_text":
        text = clean_text(raw.get("full_text")) or snippet_full or None
    if store == "metadata":
        snippet = None

    canonical = canonicalize_url(link)
    stale_days = cfg.get("news.stale_after_days", 14)
    stale = (fetched_at - published) > timedelta(days=stale_days)
    try:
        return Document(
            doc_id=sha1(canonical), source_id=source_id, kind=kind, origin=origin, title=title, url=link,
            canonical_url=canonical, published_at=published, fetched_at=fetched_at,
            language=detect_language(f"{title} {snippet or ''}"), category=raw.get("category"),
            snippet=snippet, text=text, tier_weight=float(source_cfg.get("tier_weight", 0.4)),
            content_hash=content_hash(title, snippet), stale=stale,
        )
    except ValueError as e:
        raise ItemFailure("validation", str(e)) from e
