from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx
import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAFA_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("NAFA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAFA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("NAFA_CONTACT", "tests@example.org")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    from app import config
    config.reload_config()
    cfg = config.get_config()
    cfg.raw["http"]["min_interval_s"] = 0
    cfg.raw["http"]["backoff_base_s"] = 0
    yield
    config.reload_config()


@pytest.fixture
def conn():
    from app.db import connect, init_db, sync_sources
    c = connect()
    init_db(c)
    sync_sources(c)
    yield c
    c.close()


class Routes:
    """MockTransport helper: map URL substring -> (status, body, headers) or callable(request)->Response."""
    def __init__(self, routes: dict[str, object]):
        self.routes, self.calls = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if request.url.path.endswith("/robots.txt") and not any("robots.txt" in k for k in self.routes):
            return httpx.Response(404)
        for key, val in self.routes.items():
            if key in str(request.url):
                if callable(val):
                    return val(request)
                status, body, *rest = val
                return httpx.Response(status, content=body, headers=rest[0] if rest else {})
        return httpx.Response(404)


@pytest.fixture
def make_http():
    from app.http import PoliteClient
    made = []

    def _make(routes: dict[str, object]):
        r = Routes(routes)
        c = PoliteClient(transport=httpx.MockTransport(r), sleep=lambda s: None)
        c.routes = r
        made.append(c)
        return c
    yield _make
    for c in made:
        c.close()


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()
