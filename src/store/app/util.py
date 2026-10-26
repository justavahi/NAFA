from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

from .config import get_config

UTC = timezone.utc


def local_tz() -> timezone:
    return timezone(timedelta(hours=get_config().get("timezone_offset_hours", 3)))


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """ISO-8601 with offset (what we show to people)."""
    return dt.isoformat(timespec="seconds")


def iso_utc(dt: datetime) -> str:
    """Sortable UTC string used for ordering/filters."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def pseudonym(value: str | None) -> str:
    """Stable pseudonym for client IDs in logs and prompts (R06). Never log raw IDs."""
    if value is None:
        return "u_none"
    salt = os.getenv("NAFA_PSEUDONYM_SALT", "dev-salt-change-me").encode()
    return "u_" + hmac.new(salt, str(value).encode(), hashlib.sha256).hexdigest()[:10]
