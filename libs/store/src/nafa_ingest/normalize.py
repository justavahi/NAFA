import hashlib, re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING = {"fbclid", "gclid", "yclid", "ref"}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def digest(value):
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()

def canonical_url(url):
    p=urlsplit((url or "").strip())
    query=[(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
           if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or "/",urlencode(query),""))

def parse_date(value):
    if not value: return None
    try:
        d=parsedate_to_datetime(value)
        if d.tzinfo is None: return None
        return d.isoformat()
    except (ValueError, TypeError, OverflowError):
        try:
            d=datetime.fromisoformat(value.replace("Z","+00:00"))
            return d.isoformat() if d.tzinfo else None
        except ValueError:
            return None

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def document_record(source, title, url, published_at=None, snippet="", text=None, category=None):
    url=canonical_url(url)
    if not title or not url: return None
    title=clean_text(title); snippet=clean_text(snippet)
    return {
      "doc_id": digest(url)[:40], "source_id": source["id"], "kind": source.get("kind","news"),
      "title": title, "url": url, "canonical_url": url, "published_at": parse_date(published_at),
      "fetched_at": now_iso(), "language": source.get("language","ru"),
      "category": clean_text(category) or None, "snippet": snippet or None,
      "text": text, "tier_weight": float(source.get("tier_weight",0.4)),
      "content_hash": digest(title+"\n"+snippet+"\n"+(text or "")),
      "cluster_id": None, "stale": 0
    }
