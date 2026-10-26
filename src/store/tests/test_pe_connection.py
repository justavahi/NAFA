"""Real API + shared SQLite + native LLMGateway; model HTTP is controlled fiction.

These tests establish connectivity/publication gates, never LLM quality.
"""
import html
import json
from pathlib import Path
import sys

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PE_ROOT = Path(__file__).resolve().parents[2] / "report_scripts"
sys.path.insert(0, str(PE_ROOT))

from pe.backend_bridge import BackendBridge, PreparedCase, install_backend_bridge, register_prepared_case
from pe.backend_app import SharedGateway
from pe.pipeline import CaseInput
from app import api, config
from app.agent import orchestrator
from app.llm_gateway import LLMGateway
from app.search import search_news
from app.util import pseudonym


@pytest.fixture
def connected(conn):
    client_id = "test-only-client"
    conn.execute("INSERT INTO clients(synthetic_u) VALUES(?)", (client_id,))
    case = CaseInput.model_validate_json((PE_ROOT / "examples/demo/case.json").read_text())
    case.ledger.client_id = pseudonym(client_id)
    for entry in case.ledger.entries:
        if entry.kind == "COMPANY_FEATURE":
            entry.client_id = case.ledger.client_id
    # False only to exercise production publication gates with synthetic fixtures.
    # No actual model API or actual participant data is involved.
    case.ledger.documents[0].is_mock = False
    text = case.ledger.documents[0].text
    conn.execute("""INSERT INTO documents(doc_id,source_id,title,url,canonical_url,published_at,published_at_utc,fetched_at,
        text,snippet,tier_weight,content_hash,ready_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("demo_news", "fixture", "Fixture", "synthetic://pe", "synthetic://pe", "2026-10-01T00:00:00Z",
         "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", text, text, .5, "fixture", "ready"))
    register_prepared_case(conn, client_id=client_id, event_doc_id="demo_news", prepared=PreparedCase(case=case), pseudonym=pseudonym)
    controls = dict(bad_generation=False, bad_chat=False, gateway_error=False, mode="live", calls=[], closed=[])
    responses = {p.stem: json.loads(p.read_text()) for p in (PE_ROOT / "examples/demo/responses").glob("*.json")}

    def handle(request):
        data = json.loads(request.content)
        controls["calls"].append(data)
        assert client_id not in json.dumps(data["messages"]), "raw client ID leaked to model"
        if controls["gateway_error"]:
            return httpx.Response(503, text="fixture unavailable")
        system = data["messages"][0]["content"]
        schema = json.loads(system.split("OUTPUT_JSON_SCHEMA:\n", 1)[1])["title"]
        user = data["messages"][1]["content"]
        payload = json.loads(html.unescape(user.split('<untrusted_source id="request_data">\n')[1].rsplit('\n</untrusted_source>', 1)[0]))
        if schema == "EventCore":
            output = responses["PRM-05"]
        elif schema == "PersonalOverlay":
            output = json.loads(json.dumps(responses["PRM-06"]))
            if controls["bad_generation"]:
                output["impact_chain"][0]["text"] += " Расходы вырастут на 999%."
        elif schema == "ChatAnswer":
            claim = dict(id="chat_fact", text="Регулятор повысил ключевую ставку.", source_ids=["N1"], layer="fact")
            if controls["bad_chat"]:
                claim["text"] += " На 999%."
            output = dict(answer=claim["text"], layers=["fact"], citations=["N1"], out_of_scope=False,
                          follow_up_suggestions=[], claims=[claim])
        elif schema == "CriticVerdict":
            card = payload["card"]
            claims = [(c, "fact") for c in card["confirmed_facts"]] + [(c, "bank_view") for c in card["bank_view_items"]]
            claims += [(c, "ai_inference") for c in card["why_you"]]
            claims += [(c, "ai_inference") for field in ("impact_chain", "risks", "opportunities", "actions_or_questions") for c in card["model_inference"][field]]
            output = dict(claims=[dict(id=c["id"], text=c["text"], layer=layer, verdict="supported", evidence_ids=c["source_ids"]) for c, layer in claims],
                numbers_check=[], coverage_6_questions={key: True for key in ("what_happened", "why_you", "implications", "actions", "bank_view", "basis")},
                overall="pass", fix_instructions=[])
        else:
            raise AssertionError(schema)
        return httpx.Response(200, json=dict(choices=[dict(message=dict(content=json.dumps(output, ensure_ascii=False)), finish_reason="stop")],
            usage=dict(prompt_tokens=10, completion_tokens=20)))

    def factory(db):
        assert db.execute("SELECT 1").fetchone()[0] == 1
        native = LLMGateway(db, transport=httpx.MockTransport(handle), base_url="https://unit-test.invalid/v1", api_key="fixture", sleep=lambda _: None)
        wrapped = SharedGateway(native, cost_unknown=True)
        wrapped.mode = controls["mode"]
        controls["closed"].append(wrapped)
        return wrapped

    application = FastAPI(lifespan=api.lifespan)
    application.include_router(api.app.router)
    bridge = BackendBridge(gateway_factory=factory, pseudonym=pseudonym, search_news=search_news)
    uninstall = install_backend_bridge(application, orchestrator, bridge)
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            yield client, client_id, controls
    finally:
        uninstall()


def generate(connected):
    client, cid, _ = connected
    return client.post("/cards/generate", json=dict(client_id=cid, event_doc_id="demo_news", persona="PER-03"))


def test_pe_generate_get_ask_feedback_trace_share_one_database(connected, conn):
    client, _, controls = connected
    response = generate(connected)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["status"] == "verified" and card["publishable"] and not card["partial"]
    assert client.get(f"/cards/{card['card_id']}").json() == card
    answer = client.post(f"/cards/{card['card_id']}/ask", json=dict(question="Что произошло?"))
    assert answer.status_code == 200, answer.text
    assert answer.json()["publishable"] and answer.json()["claims"]
    assert client.post(f"/cards/{card['card_id']}/feedback", json=dict(helpful=True)).json()["stored"]
    trace = client.get(f"/cards/{card['card_id']}/trace").json()
    assert len(trace) >= 6
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 5
    assert len(controls["calls"]) == 5
    # Chat generator was explicitly routed through GLM, critic through Qwen.
    assert controls["calls"][3]["model"] == config.get_models()["reasoning"]["api_name"]
    assert controls["calls"][4]["model"] == config.get_models()["general"]["api_name"]
    assert conn.execute("SELECT COUNT(*) FROM card_questions").fetchone()[0] == 1


def test_invalid_generation_is_409_and_never_saved(connected, conn):
    client, _, controls = connected
    controls["bad_generation"] = True
    response = generate(connected)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["card"] is None
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pe_card_contexts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pe_runs").fetchone()[0] == 1


def test_invalid_dialogue_is_not_published_or_stored(connected, conn):
    client, _, controls = connected
    card = generate(connected).json()
    controls["bad_chat"] = True
    response = client.post(f"/cards/{card['card_id']}/ask", json=dict(question="Сколько?"))
    assert response.status_code == 409, response.text
    assert conn.execute("SELECT COUNT(*) FROM card_questions").fetchone()[0] == 0


def test_absent_da_case_does_not_fall_back_to_unverified_card(connected, conn):
    client, cid, controls = connected
    conn.execute("DELETE FROM pe_prepared_cases")
    response = generate(connected)
    assert response.status_code == 422 and "PE_CASE_NOT_READY" in response.text
    assert not controls["calls"]


def test_changed_news_invalidates_prepared_case_before_model(connected, conn):
    conn.execute("UPDATE documents SET text='Changed'")
    response = generate(connected)
    assert response.status_code == 422
    assert not connected[2]["calls"]


@pytest.mark.parametrize("mode", ["replay", "evaluation"])
def test_nonlive_results_never_become_public_cards(connected, conn, mode):
    connected[2]["mode"] = mode
    assert generate(connected).status_code == 409
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_model_service_failure_does_not_publish_card(connected, conn):
    connected[2]["gateway_error"] = True
    response = generate(connected)
    assert response.status_code == 409, response.text
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_scenario_stays_deterministic_after_pe_connection(connected, conn):
    client, _, controls = connected
    card = generate(connected).json()
    request = dict(question="Что если ставка вырастет?", scenario=dict(scenario_type="rate", floating_debt_rub=1e9, delta_rate_pp=1))
    answer = client.post(f"/cards/{card['card_id']}/ask", json=request)
    assert answer.status_code == 200, answer.text
    assert answer.json()["result"]["delta_annual_rub"] == 1e7
    assert len(controls["calls"]) == 3
    request["scenario"] = {}
    assert client.post(f"/cards/{card['card_id']}/ask", json=request).status_code == 422
    assert len(controls["calls"]) == 3


def test_context_and_card_are_saved_atomically(connected, conn):
    conn.execute("""CREATE TRIGGER fail_context BEFORE INSERT ON pe_card_contexts
        BEGIN SELECT RAISE(ABORT, 'fixture storage error'); END""")
    response = generate(connected)
    assert response.status_code == 500
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pe_runs").fetchone()[0] == 0


@pytest.mark.parametrize("extra", [{"persona": "PER-04"}, {"as_of": "2026-10-01"}])
def test_prepared_context_cannot_cross_role_or_asof(connected, extra):
    client, cid, controls = connected
    response = client.post("/cards/generate", json=dict(client_id=cid, event_doc_id="demo_news", **extra))
    assert response.status_code == 422
    assert not controls["calls"]
