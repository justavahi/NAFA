"""Tolerant RSS parser (SRC-01, SRC-03, SRC-04, ...). Uses lxml in recover mode, so truncated feeds
(RBC) and odd namespaces (Interfax atom via https) do not break parsing. Namespaces are ignored on purpose."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from lxml import etree

from ..http import FetchError, host_allowed
from ..util import now_utc
from .base import Connector, FetchOutcome, ItemError
from .normalize import ItemFailure, build_document


def _local(tag) -> str:
    if not isinstance(tag, str):                 # comments / processing instructions
        return ""
    return tag.rsplit("}", 1)[-1]


def parse_rss_items(content: bytes) -> list[dict]:
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=True)
    try:
        root = etree.fromstring(content, parser=parser)
    except etree.XMLSyntaxError as e:
        raise FetchError("parse_error", str(e)) from e
    if root is None:
        raise FetchError("parse_error", "empty or unparseable XML")
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d: dict = {"category": []}
        for ch in el:
            name = _local(ch.tag)
            if not name:
                continue
            if name == "link":                    # Atom uses href, RSS uses text
                val = (ch.get("href") or "".join(ch.itertext())).strip()
            else:
                val = "".join(ch.itertext()).strip()
            if name == "category":
                if val:
                    d["category"].append(val)
            elif name == "full-text":
                d["full_text"] = val
            elif name in ("summary", "content") and "description" not in d:
                d["description"] = val
            elif name in ("updated",) and "pubDate" not in d:
                d["pubDate"] = val
            elif name not in d:
                d[name] = val
        d["category"] = "; ".join(dict.fromkeys(d["category"])) or None
        items.append(d)
    if not items and root is not None and _local(root.tag) not in ("rss", "feed", "RDF"):
        raise FetchError("parse_error", f"not a feed (root <{_local(root.tag)}>)")
    return items


class RssConnector(Connector):
    def fetch(self, now: datetime | None = None, **kwargs) -> FetchOutcome:
        url = self.cfg["url"]
        host = urlparse(url).hostname or ""
        res = self.http.get(url, allowed_domains=[host])
        fetched_at = now or now_utc()
        raw_items = parse_rss_items(res.content)
        docs, errors = [], []
        for i, raw in enumerate(raw_items):
            try:
                docs.append(build_document(self.source_id, self.cfg, raw, fetched_at))
            except ItemFailure as e:
                errors.append(ItemError(i, e.error_type, e.detail))
        return FetchOutcome(docs, len(raw_items), errors, res.status, res.attempts, res.robots)
