from nafa_ingest.normalize import canonical_url, document_record

def test_canonical_url_removes_tracking():
    assert canonical_url("https://EXAMPLE.com/a?utm_source=x&id=2#top") == "https://example.com/a?id=2"

def test_document_record_has_source_and_hash():
    rec=document_record({"id":"SRC-01","tier_weight":1},"Title","https://example.com/a",
                        "Tue, 20 Oct 2026 10:00:00 +0300","Snippet")
    assert rec["source_id"]=="SRC-01"
    assert rec["published_at"].endswith("+03:00")
    assert rec["url"].startswith("https://")
