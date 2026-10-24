from nafa_ingest.normalize import canonicalize_url, normalize_document


def test_canonicalize_url_removes_configured_tracking():
    result = canonicalize_url(
        "HTTPS://Example.org/article?utm_source=mail&id=2#intro",
        tracking_prefixes=["utm_"],
    )
    assert result == "https://example.org/article?id=2"


def test_normalize_document_has_timezone_and_stable_id():
    source = {"id": "SRC-X", "tier_weight": 0.85}
    raw = {
        "title": "  Test   title ",
        "url": "https://example.org/a?utm_source=x",
        "published_at": "2026-10-20T10:00:00+03:00",
        "snippet": " hello\nworld ",
    }
    first = normalize_document(raw, source, tracking_prefixes=["utm_"])
    second = normalize_document(raw, source, tracking_prefixes=["utm_"])
    assert first["title"] == "Test title"
    assert first["snippet"] == "hello world"
    assert first["published_at"].endswith("+03:00")
    assert first["doc_id"] == second["doc_id"]
