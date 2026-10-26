import httpx
import pytest

from app.http import FetchError, PoliteClient, host_allowed


def test_retries_then_success(make_http):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        return httpx.Response(503) if state["n"] < 3 else httpx.Response(200, content=b"ok")
    http = make_http({"example.org/data": handler})
    r = http.get("https://example.org/data")
    assert r.status == 200 and r.attempts == 3


def test_gives_up_after_max_retries(make_http):
    http = make_http({"example.org/data": (500, b"")})
    with pytest.raises(FetchError) as e:
        http.get("https://example.org/data")
    assert e.value.error_type == "http_error" and e.value.attempts == 6   # 1 + 5 retries (spec: not more than 5)


def test_4xx_is_not_retried(make_http):
    http = make_http({"example.org/data": (404, b"")})
    with pytest.raises(FetchError) as e:
        http.get("https://example.org/data")
    assert e.value.attempts == 1


def test_robots_disallow_blocks_fetch(make_http):
    http = make_http({"robots.txt": (200, b"User-agent: *\nDisallow: /private\n"), "example.org/": (200, b"x")})
    assert http.get("https://example.org/public").status == 200
    with pytest.raises(FetchError) as e:
        http.get("https://example.org/private/x")
    assert e.value.error_type == "robots_disallowed"


def test_robots_specific_to_our_token(make_http):
    http = make_http({"robots.txt": (200, b"User-agent: NAFA-prototype\nDisallow: /\n"), "example.org/": (200, b"x")})
    with pytest.raises(FetchError):
        http.get("https://example.org/a")


def test_domain_whitelist_and_redirect_hop(make_http):
    http = make_http({"good.ru/start": (302, b"", {"location": "https://evil.com/steal"})})
    with pytest.raises(FetchError) as e:
        http.get("https://good.ru/start", allowed_domains=["good.ru"])
    assert e.value.error_type == "domain_not_allowed"                      # re-checked on every hop (SSRF guard)
    assert host_allowed("www.cbr.ru", ["cbr.ru"]) and not host_allowed("evilcbr.ru", ["cbr.ru"])


def test_bad_scheme(make_http):
    with pytest.raises(FetchError) as e:
        make_http({}).get("file:///etc/passwd")
    assert e.value.error_type == "bad_scheme"
