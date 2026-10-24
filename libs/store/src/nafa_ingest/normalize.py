from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser


def clean_text(value: Any) -> str | None:
    """Convert a scalar or simple list to whitespace-normalized text."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        values = [clean_text(item) for item in value]
        text = " ".join(item for item in values if item)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_datetime(value: Any, *, default_timezone: str = "UTC", date_format: str | None = None) -> str | None:
    """Return an ISO-8601 timestamp with a timezone; missing/unparseable values stay None."""
    if value is None or clean_text(value) is None:
        return None
    try:
        if date_format:
            parsed = datetime.strptime(clean_text(value), date_format)
        elif isinstance(value, datetime):
            parsed = value
        else:
            parsed = date_parser.parse(clean_text(value))
    except (ValueError, TypeError, OverflowError):
        return None

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(default_timezone))
        except Exception:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def canonicalize_url(
    url: str,
    *,
    base_url: str | None = None,
    tracking_parameters: list[str] | None = None,
    tracking_prefixes: list[str] | None = None,
) -> str:
    """Normalize a URL and remove configurable analytics parameters."""
    if base_url:
        url = urljoin(base_url, url)
    parts = urlsplit(url.strip())
    ignored = {item.lower() for item in (tracking_parameters or [])}
    prefixes = tuple(item.lower() for item in (tracking_prefixes or []))
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in ignored and not key.lower().startswith(prefixes)
    ]
    query.sort()
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def json_safe(value: Any) -> Any:
    """Convert parser/pandas values into JSON-serializable values."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (ValueError, TypeError):
            pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return None
        return value
    return str(value)


def normalize_document(
    raw: dict[str, Any],
    source: dict[str, Any],
    *,
    fetched_at: str | None = None,
    base_url: str | None = None,
    default_timezone: str = "UTC",
    tracking_parameters: list[str] | None = None,
    tracking_prefixes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Map one parsed item into the shared Document schema used by NAFA."""
    title = clean_text(raw.get("title"))
    raw_url = clean_text(raw.get("url"))
    snippet = clean_text(raw.get("snippet"))
    text = clean_text(raw.get("text"))

    if not raw_url and not title and not text and not snippet:
        return None
    if not raw_url:
        fingerprint = sha256_text("\n".join(part or "" for part in (title, snippet, text)))
        raw_url = f"urn:sha256:{fingerprint}"

    url = urljoin(base_url, raw_url) if base_url and not raw_url.startswith("urn:") else raw_url
    canonical_url = canonicalize_url(
        url,
        tracking_parameters=tracking_parameters,
        tracking_prefixes=tracking_prefixes,
    )
    published_at = parse_datetime(
        raw.get("published_at"),
        default_timezone=raw.get("timezone") or source.get("timezone") or default_timezone,
        date_format=source.get("published_date_format"),
    )
    fetched_at = fetched_at or utc_now()
    content_hash = sha256_text("\n".join(part or "" for part in (title, snippet, text)))
    doc_id = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()

    return {
        "doc_id": doc_id,
        "source_id": source["id"],
        "kind": clean_text(raw.get("kind") or source.get("kind") or "news"),
        "title": title or "(untitled)",
        "url": url,
        "canonical_url": canonical_url,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "language": clean_text(raw.get("language") or source.get("language") or "ru"),
        "category": clean_text(raw.get("category")),
        "snippet": snippet,
        "text": text,
        "tier_weight": float(source.get("tier_weight", 0.4)),
        "content_hash": content_hash,
        "cluster_id": clean_text(raw.get("cluster_id")),
        "stale": bool(raw.get("stale", False)),
        "payload": json_safe(raw.get("payload", raw)),
        "source_item_id": clean_text(raw.get("source_item_id") or raw.get("guid") or canonical_url),
    }
