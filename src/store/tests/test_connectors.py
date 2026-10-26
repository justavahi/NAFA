from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import get_sources
from app.connectors.cbr import parse_key_rate_html, parse_xml_daily
from app.connectors.normalize import canonicalize_url, parse_date, ItemFailure
from app.connectors.registry import build_connector
from app.http import FetchError
from app.util import sha1
from tests.conftest import fixture_bytes

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_cbr_press_rss(make_http):
    http = make_http({"cbr.ru/rss/RssPress": (200, fixture_bytes("cbr_press.xml"))})
    out = build_connector("SRC-01", http).fetch(now=NOW)
    assert out.items_total == 2 and not out.item_errors
    d = out.items[0]
    assert d.published_at.utcoffset() == timedelta(hours=3)              # +0300 preserved
    assert d.tier_weight == 1.0 and d.source_id == "SRC-01"
    assert d.text is None and d.snippet                                   # store=snippet -> no full text
    assert d.doc_id == sha1(d.canonical_url)
    assert "utm_source" not in out.items[1].canonical_url                 # tracking param stripped
    assert out.items[1].canonical_url.endswith("id=1002")


def test_interfax_tolerant_cp1251_html_and_missing_date(make_http):
    http = make_http({"interfax.ru/rss.asp": (200, fixture_bytes("interfax.xml"))})
    out = build_connector("SRC-03", http).fetch(now=NOW)
    assert out.items_total == 3
    assert [e.error_type for e in out.item_errors] == ["missing_date"]    # bad item is reported, others survive
    d = out.items[0]
    assert d.title.startswith("Недельная инфляция")                       # windows-1251 decoded
    assert "<" not in d.snippet and "6,32%" in d.snippet                  # CDATA html stripped
    assert d.category == "Экономика; Финансы"
    assert d.stale                                                        # 02.09 vs now=24.10 (> stale_after_days)
    assert d.language == "ru"
    ua = [r for r in http.routes.calls]                                   # identifying UA is configured
    assert "tests@example.org" in http.user_agent and "NAFA-prototype" in http.user_agent


def test_rbc_truncated_feed_does_not_break_and_full_text_is_not_stored(make_http):
    http = make_http({"rssexport.rbc.ru": (200, fixture_bytes("rbc_truncated.xml"))})
    out = build_connector("SRC-04", http).fetch(now=NOW)
    good = [d for d in out.items]
    assert len(good) == 2                                                 # third item is cut off -> missing_date, first two parsed
    assert out.items_total == 3 and out.item_errors[0].error_type == "missing_date"
    assert all(d.text is None for d in good)                              # licence not verified -> snippet only
    assert "from=newsfeed" not in good[0].canonical_url


def test_garbage_is_a_parse_error(make_http):
    http = make_http({"cbr.ru/rss/RssPress": (200, b"<html><body>captcha</body></html>")})
    with pytest.raises(FetchError) as e:
        build_connector("SRC-01", http).fetch(now=NOW)
    assert e.value.error_type == "parse_error"


def test_xml_daily_windows1251_decimal_comma():
    pts, total, errs = parse_xml_daily(fixture_bytes("xml_daily_1251.xml"), get_sources()["SRC-02"]["series"])
    got = {p.series_id: p.value for p in pts}
    assert got == {"M-03": 82.8504, "M-05": 93.2316, "M-04": 12.3869}     # values of 20.10.2026 from the case docs
    assert all(p.date == date(2026, 10, 20) for p in pts) and total == 3 and not errs


def test_key_rate_table():
    pts, total, errs = parse_key_rate_html(fixture_bytes("keyrate.html"))
    assert [p.value for p in pts] == [14.0, 14.0, 14.0] and pts[0].date == date(2026, 10, 21) and pts[0].series_id == "M-01"
    assert total == 4 and [e.error_type for e in errs] == ["bad_row"]
    with pytest.raises(FetchError):
        parse_key_rate_html(b"<html><table><tr><td>layout</td></tr></table></html>")


def test_date_and_url_helpers():
    assert parse_date("Fri, 11 Sep 2026 13:30:00 +0300").utcoffset() == timedelta(hours=3)
    assert parse_date("2026-09-11T10:00:00").utcoffset() == timedelta(hours=3)   # naive -> Moscow, never dropped
    with pytest.raises(ItemFailure):
        parse_date("yesterday-ish")
    assert canonicalize_url("HTTPS://Example.com/a/b/?utm_medium=x&id=5#frag") == "https://example.com/a/b?id=5"
