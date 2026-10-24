from __future__ import annotations

from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(settings: dict[str, Any]) -> requests.Session:
    """Create a reusable HTTP session with bounded retries and backoff."""
    retry = Retry(
        total=int(settings.get("retries", 5)),
        connect=int(settings.get("retries", 5)),
        read=int(settings.get("retries", 5)),
        status=int(settings.get("retries", 5)),
        backoff_factor=float(settings.get("retry_backoff_seconds", 0.7)),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": settings["user_agent"],
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html, application/json, */*",
    })
    return session


def fetch_url(url: str, settings: dict[str, Any], *, session: requests.Session | None = None) -> tuple[bytes, str, str]:
    """Download a URL; returns (body bytes, resolved URL, content type)."""
    own_session = session is None
    session = session or make_session(settings)
    try:
        response = session.get(url, timeout=float(settings.get("timeout_seconds", 25)))
        response.raise_for_status()
        return response.content, response.url, response.headers.get("Content-Type", "")
    finally:
        if own_session:
            session.close()


def read_local_file(path: str | Path) -> bytes:
    """Read a configured local file as bytes."""
    return Path(path).expanduser().read_bytes()
