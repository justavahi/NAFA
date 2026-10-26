"""HTTP use cases against the merged backend; external LLM quality is separate."""
import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from app.api import app
from app.ingest.clients import load_all
from app.synth import generate_clients, load_synthetic_news


@pytest.fixture
def scenario_client(conn, tmp_path):
    paths = generate_clients(tmp_path / "synthetic")
    load_all(conn, paths["clients"], paths["links"], paths["payments"])
    load_synthetic_news(conn)
    client_id = conn.execute("SELECT synthetic_u FROM clients ORDER BY synthetic_u LIMIT 1").fetchone()[0]
    doc_id = conn.execute("SELECT doc_id FROM documents WHERE origin='synthetic' ORDER BY published_at_utc DESC LIMIT 1").fetchone()[0]
    with TestClient(app, raise_server_exceptions=False) as api:
        yield api, client_id, doc_id


@pytest.mark.parametrize("path", ["/health", "/sources", "/budget", "/quality/summary", "/quality/ingestion", "/clients", "/openapi.json"])
def test_read_endpoints(scenario_client, path):
    assert scenario_client[0].get(path).status_code == 200


@pytest.mark.parametrize("path", ["/clients/missing/profile", "/feed?client_id=missing", "/cards/missing", "/cards/missing/trace", "/series/M-99"])
def test_unknown_identity_is_404(scenario_client, path):
    assert scenario_client[0].get(path).status_code == 404


def create_card(scenario_client):
    api, client_id, doc_id = scenario_client
    response = api.post("/cards/generate", json=dict(client_id=client_id, event_doc_id=doc_id, persona="PER-03"))
    assert response.status_code == 200, response.text
    return response.json()


def test_end_to_end_profile_feed_card_question_feedback_trace(scenario_client):
    api, client_id, _ = scenario_client
    assert api.get(f"/clients/{client_id}/profile").status_code == 200
    feed = api.get("/feed", params=dict(client_id=client_id, k=5))
    assert feed.status_code == 200 and len(feed.json()) <= 5
    card = create_card(scenario_client)
    cid = card["card_id"]
    assert api.get(f"/cards/{cid}").json() == card
    assert api.post(f"/cards/{cid}/ask", json=dict(question="Что изменилось для компании?")).status_code == 200
    assert api.post(f"/cards/{cid}/feedback", json=dict(helpful=True, reason="Тест")).json()["stored"]
    trace = api.get(f"/cards/{cid}/trace").json()
    assert len(trace) >= 4
    assert all(row["trace_id"] == card["trace_id"] for row in trace)


def test_unseen_byo_text_reaches_same_card_route(scenario_client):
    api, client_id, _ = scenario_client
    response = api.post("/events/ingest", json=dict(title="Новая тестовая новость", published_at="2026-10-20T10:00:00Z",
        text="Банк России опубликовал новый обзор условий кредитования компаний. Предприятия сообщают об изменении спроса на финансирование."))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result.get("doc_id")
    generated = api.post("/cards/generate", json=dict(client_id=client_id, event_doc_id=result["doc_id"]))
    assert generated.status_code == 200, generated.text


@pytest.mark.parametrize("body", [{}, {"text": "x", "url": "https://example.org"}])
def test_ingest_requires_exactly_one_input(scenario_client, body):
    assert scenario_client[0].post("/events/ingest", json=body).status_code == 422


@pytest.mark.parametrize("changes", [{}, {"event_doc_id": "missing", "query": "ставка"}])
def test_generate_requires_exactly_one_event_selector(scenario_client, changes):
    api, client_id, _ = scenario_client
    assert api.post("/cards/generate", json=dict(client_id=client_id, **changes)).status_code == 422


@pytest.mark.parametrize("scenario,key,expected", [
    ({"scenario_type": "rate", "floating_debt_rub": 1e9, "delta_rate_pp": 1}, "delta_annual_rub", 1e7),
    ({"scenario_type": "fx", "fx_volume": 1e7, "spot_from": 82.85, "spot_to": 87.2, "direction": "payments"}, "delta_rub", -43500000),
    ({"scenario_type": "inflation", "cogs_rub": 1e8, "pass_through": .5, "delta_inflation_pp": 2}, "delta_cost_rub", 1e6),
])
def test_three_scenario_calculators_through_http(scenario_client, scenario, key, expected):
    card = create_card(scenario_client)
    response = scenario_client[0].post(f"/cards/{card['card_id']}/ask", json=dict(question="Посчитай сценарий", scenario=scenario))
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "deterministic_scenario"
    assert response.json()["result"][key] == expected


@pytest.mark.parametrize("scenario", [
    {}, {"scenario_type": "unknown"}, {"scenario_type": "rate"},
    {"scenario_type": "rate", "floating_debt_rub": -1, "delta_rate_pp": 1},
    {"scenario_type": "rate", "floating_debt_rub": 1, "delta_rate_pp": "NaN"},
    {"scenario_type": "rate", "floating_debt_rub": 1, "delta_rate_pp": "Infinity"},
    {"scenario_type": "rate", "floating_debt_rub": 1e308, "delta_rate_pp": 1e308},
])
def test_invalid_scenario_is_422_and_does_not_store_answer(scenario_client, conn, scenario):
    card = create_card(scenario_client)
    response = scenario_client[0].post(f"/cards/{card['card_id']}/ask", json=dict(question="Посчитай", scenario=scenario))
    assert response.status_code == 422, response.text
    assert conn.execute("SELECT COUNT(*) FROM card_questions").fetchone()[0] == 0


def test_bank_view_write_requires_admin(scenario_client):
    response = scenario_client[0].post("/bank-view", json=dict(indicator="key_rate", horizon="end_2026", value=14,
        unit="%", as_of="2026-09-11", extraction_method="text"))
    assert response.status_code == 401


def test_concurrent_health_requests_keep_database_connection_usable(scenario_client):
    api = scenario_client[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: api.get("/health").status_code, range(40)))
    assert codes == [200] * 40


def test_future_event_cannot_be_opened_in_historical_card(scenario_client):
    api, cid, doc = scenario_client
    response = api.post("/cards/generate", json=dict(client_id=cid, event_doc_id=doc, as_of="2020-01-01"))
    assert response.status_code == 404


def test_invalid_asof_is_rejected_not_silently_replaced_by_now(scenario_client):
    api, cid, doc = scenario_client
    assert api.get("/feed", params=dict(client_id=cid, as_of="not-a-date")).status_code == 422
    assert api.post("/cards/generate", json=dict(client_id=cid, event_doc_id=doc, as_of="not-a-date")).status_code == 422


def test_historical_card_does_not_use_newer_bank_view(scenario_client, conn):
    from app.bank_view import add_bank_view
    from app.models import BankViewIn
    api, cid, _ = scenario_client
    for day, value in [("2026-09-07", 13.5), ("2026-09-11", 14.0)]:
        add_bank_view(conn, BankViewIn(indicator="key_rate", horizon="end_2026", value=value, as_of=day))
    item = api.post("/events/ingest", json=dict(title="Ключевая ставка: учебный контекст", text="Регулятор опубликовал сообщение о ключевой ставке и условиях финансирования компаний.", published_at="2026-09-08T10:00:00Z")).json()
    response = api.post("/cards/generate", json=dict(client_id=cid, event_doc_id=item["doc_id"], as_of="2026-09-09"))
    assert response.status_code == 200, response.text
    records = response.json()["layers"]["bank_position"]["records"]
    assert records and all(row["record"]["active"]["value"] == 13.5 for row in records if "record" in row)


def test_explicit_baseline_card_remains_provisional(scenario_client):
    card = create_card(scenario_client)
    assert card.get("publishable") is False and card.get("status") == "provisional"


def test_explicit_baseline_question_does_not_claim_verified_generation(scenario_client):
    card = create_card(scenario_client)
    answer = scenario_client[0].post(f"/cards/{card['card_id']}/ask", json=dict(question="Как это касается компании?")).json()
    assert not answer.get("publishable") and not answer.get("claims")


def test_explicit_baseline_is_api_only_use_pe_factory_for_ui(scenario_client):
    response = scenario_client[0].get("/")
    assert response.status_code == 404
