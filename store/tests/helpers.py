from datetime import datetime, timedelta, timezone

from app.connectors.normalize import build_document
from app.config import get_sources

MSK = timezone(timedelta(hours=3))


def mkdoc(source_id: str, title: str, snippet: str, url: str, pub: str = "2026-09-11T13:30:00+03:00", now: datetime | None = None):
    return build_document(source_id, get_sources()[source_id], {"title": title, "link": url, "pubDate": pub, "description": snippet},
                          now or datetime(2026, 9, 11, 14, 0, tzinfo=MSK))
