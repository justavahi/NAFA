from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent import tools
from app.agent.orchestrator import generate_card
from app.api import app
from app.ingest.clients import load_all
from app.synth import generate_clients, load_synthetic_news


def _seed(conn, tmp_path):
    paths = generate_clients(tmp_path / "synthetic")
    load_all(conn, paths["clients"], paths["links"], paths["payments"])
    load_synthetic_news(conn)
    client = conn.execute("SELECT synthetic_u FROM clients ORDER BY synthetic_u LIMIT 1").fetchone()[0]
    doc = conn.execute("SELECT doc_id FROM documents WHERE origin='synthetic' ORDER BY published_at_utc DESC LIMIT 1").fetchone()[0]
    return client, doc


def test_scenario_calculator_all_three_fm15_formulas():
    a = tools.compute_scenario({"scenario_type": "rate", "floating_debt_rub": 1_000_000_000, "delta_rate_pp": 1})
    assert a["delta_annual_rub"] == 10_000_000
    b = tools.compute_scenario({"scenario_type": "fx", "fx_volume": 10_000_000, "spot_from": 82.85, "spot_to": 87.2, "direction": "payments"})
    assert b["delta_rub"] == -43_500_000
    c = tools.compute_scenario({"scenario_type": "inflation", "cogs_rub": 100_000_000, "pass_through": 0.5, "delta_inflation_pp": 2})
    assert c["delta_cost_rub"] == 1_000_000


def test_tool_registry_is_allowlisted_and_payment_features_redacted(conn, tmp_path):
    client, _ = _seed(conn, tmp_path)
    result = tools.call_tool(conn, "get_payment_features", {"client_id": client})
    assert "counterparty_hhi" in result and "client_pseudonym" in result
    assert all("counterparty" not in key.lower() or key in {"counterparty_count", "counterparty_hhi"} for key in result)
    try:
        tools.call_tool(conn, "os.system", {"command": "echo unsafe"})
    except ValueError as exc:
        assert "not allow-listed" in str(exc)
    else:
        raise AssertionError("unknown tool unexpectedly dispatched")


def test_card_generation_persists_full_trace_and_feedback(conn, tmp_path):
    client, doc_id = _seed(conn, tmp_path)
    card = generate_card(conn, client_id=client, event_doc_id=doc_id, persona="PER-03")
    assert card["card_id"] and card["trace_id"] and card["layers"]["fact"]["doc_id"] == doc_id
    assert card["layers"]["fact"]["url"].startswith("synthetic://")
    row = conn.execute("SELECT payload_json FROM cards WHERE card_id=?", (card["card_id"],)).fetchone()
    assert row and "provisional" in row["payload_json"]
    trace = conn.execute("SELECT COUNT(*) FROM trace_events WHERE trace_id=? AND card_id=?", (card["trace_id"], card["card_id"])).fetchone()[0]
    assert trace >= 2
    from app.agent.orchestrator import add_feedback, ask_card, card_trace, get_card
    assert get_card(conn, card["card_id"])["card_id"] == card["card_id"]
    ask = ask_card(conn, card["card_id"], "Что произошло?")
    assert ask["mode"] == "grounded_context"
    assert add_feedback(conn, card["card_id"], True, "Полезная ссылка")["stored"] is True
    assert len(card_trace(conn, card["card_id"])) >= 3


def test_required_api_routes_are_registered():
    paths = {route.path for route in app.routes}
    assert {"/feed", "/cards/generate", "/cards/{card_id}", "/cards/{card_id}/ask",
            "/cards/{card_id}/feedback", "/cards/{card_id}/trace", "/quality/summary"} <= paths
