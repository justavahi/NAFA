import pytest
from fastapi.testclient import TestClient

from app.quality import ingestion_summary, q23_parse_success, q24_latency, q26_dups_errors
from app.util import iso, now_utc
from datetime import timedelta
from tests.conftest import fixture_bytes


def _log(conn, src, pub_ago_min, ready_after_s, fetched_after_s=60, status="ready", dup=0):
    now = now_utc()
    pub = now - timedelta(minutes=pub_ago_min)
    fetched = pub + timedelta(seconds=fetched_after_s)
    ready = pub + timedelta(seconds=ready_after_s)
    conn.execute("INSERT INTO ingestion_log(source_id,doc_id,published_at,fetched_at,ready_at,status,is_duplicate) VALUES(?,?,?,?,?,?,?)",
                 (src, "d", iso(pub), iso(fetched), iso(ready), status, dup))


def test_q24_percentiles_and_backfill_exclusion(conn):
    for s in (60, 120, 180, 240, 600):
        _log(conn, "SRC-03", 30, s)
    _log(conn, "SRC-03", 60 * 48, 3600 * 40, fetched_after_s=3600 * 40)      # fetched 40 h after publication = feed backfill
    r = {x["source_id"]: x for x in q24_latency(conn)}["SRC-03"]
    assert r["n"] == 5 and r["p50_s"] == 180 and r["excluded_backfill"] == 1 and r["p95_s"] > 500


def test_q23_q26(conn, make_http):
    from app.ingest.news import run_source
    ok = make_http({"cbr.ru/rss/RssPress": (200, fixture_bytes("cbr_press.xml"))})
    bad = make_http({"cbr.ru/rss/RssPress": (503, b"")})
    for h in (ok, ok, bad, bad):
        run_source(conn, "SRC-01", h)
    q = {x["source_id"]: x for x in q23_parse_success(conn)}["SRC-01"]
    assert (q["attempts"], q["ok"], q["failed"], q["success_rate"]) == (4, 2, 2, 0.5)
    e = {x["source_id"]: x for x in q26_dups_errors(conn)}["SRC-01"]
    assert e["error_share"] > 0 and e["records"] == 4                         # 2 ready docs + 2 failed-run error rows


def test_summary_shape(conn):
    s = ingestion_summary(conn)
    assert set(s) == {"window_days", "q23_parse_success", "q24_latency", "q25_tokens", "q26_duplicates_errors", "q27_processing"}
    assert s["q25_tokens"]["today"]["remaining_rub"] == 500


@pytest.fixture
def client():
    from app.api import app
    with TestClient(app) as c:
        yield c


def test_api_health_sources_ingest_search(client):
    assert client.get("/health").json() == {"status": "ok"}
    ids = {s["source_id"]: s for s in client.get("/sources").json()}
    assert "SRC-01" in ids and ids["SRC-05"]["status"] == "disabled"
    r = client.post("/events/ingest", json={"text": "Банк России повысил прогноз по инфляции на 2026 год. Это новая новость."})
    assert r.status_code == 200 and r.json()["ready_at"]
    hits = client.get("/search/news", params={"q": "прогноз инфляции"}).json()
    assert hits and hits[0]["doc_id"] == r.json()["doc_id"]
    assert client.post("/events/ingest", json={}).status_code == 422
    assert client.post("/events/ingest", json={"text": "a", "url": "https://cbr.ru"}).status_code == 422
    assert client.post("/events/ingest", json={"url": "https://evil.example/x"}).status_code == 403


def test_api_bank_view_admin_token_and_get(client):
    body = {"indicator": "key_rate", "horizon": "end_2026", "value": 14.0, "as_of": "2026-09-11", "doc_id": "DOC-07"}
    assert client.post("/bank-view", json=body).status_code == 401
    assert client.post("/bank-view", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post("/bank-view", json=body, headers={"X-Admin-Token": "tok"}).status_code == 200
    g = client.get("/bank-view", params={"indicator": "key_rate"}).json()
    assert g[0]["active"]["value"] == 14.0


def test_api_clients_series_quality_budget(client, tmp_path):
    from app.db import connect
    from app.ingest.clients import load_all
    from app.synth import generate_clients
    f = generate_clients(tmp_path / "s")
    conn = connect(); load_all(conn, f["clients"], f["links"], f["payments"]); conn.close()
    assert len(client.get("/clients", params={"limit": 3}).json()) == 3
    p = client.get("/clients/SYN-U-0002/profile").json()
    assert p["mainokved_code"] == "24.10" and p["flags"]["dep"] == 1
    assert client.get("/clients/zzz/profile").status_code == 404
    assert client.get("/series/M-03").json() == {"series_id": "M-03", "points": []}      # known series, no data yet
    assert client.get("/series/M-99").status_code == 404
    assert client.get("/quality/ingestion").status_code == 200
    assert client.get("/budget").json()["today"]["limit_rub"] == 500


def test_cli_end_to_end_synth_report_check(tmp_path, capsys):
    from app.cli import main
    assert main(["synth", "--out", str(tmp_path / "syn")]) == 0
    assert main(["bankview-seed"]) == 0
    assert main(["report", "--out", str(tmp_path / "docs")]) == 0
    md = (tmp_path / "docs" / "03_metrics_collected.md").read_text(encoding="utf-8")
    assert "| M-03 |" in md and "not implemented" in md and "agent layer" in md
    src = (tmp_path / "docs" / "01_parsing_sources.md").read_text(encoding="utf-8")
    assert "SRC-14" in src and "synthetic://EV-" in src
    ps = (tmp_path / "docs" / "02_sources_descriptions.md").read_text(encoding="utf-8")
    assert "NOT VERIFIED - manual review required" in ps                    # terms are never claimed as checked
