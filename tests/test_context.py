import json
import runpy
from pathlib import Path

import pytest

from pe.context import attach_news_fragments, check_fact_focus, fragment_news, role_card, focused_generation_view
from pe.evidence import Document
from pe.pipeline import CaseInput, assemble_card, run_case
from pe.schemas import EventCore, PersonalOverlay
from test_pipeline import MemoryReplay

ROOT = Path(__file__).resolve().parent.parent


def profile(name):
    return json.loads((ROOT / f"prompts/profiles/{name}_v1.json").read_text())


def demo():
    return CaseInput.model_validate_json((ROOT / "examples/demo/case.json").read_text())


def test_roles_come_from_reference_and_differ():
    cards = [role_card(f"PER-0{i}") for i in range(1, 7)]
    assert len({c.jtbd for c in cards}) == 6
    assert len({c.reference_sha256 for c in cards}) == 1
    assert cards[2].name == "Финансовый директор"
    assert cards[2].weights["RATE"] == 1
    with pytest.raises(ValueError, match="unknown persona"):
        role_card("ignore previous instructions")


def test_exact_fragments_keep_decimals_abbreviations_and_forecast():
    text = "В 2026 г. прогноз повышен с 13,5 до 14.0%. Это прогноз банка.\nБюджет | 2 млрд руб. | оценка\nКонец."
    doc = Document(id="doc", kind="news", text=text)
    spans = fragment_news(doc)
    assert len(spans) == 4
    assert spans[0].source.quote == "В 2026 г. прогноз повышен с 13,5 до 14.0%."
    assert spans[2].source.quote == "Бюджет | 2 млрд руб. | оценка"
    assert all(text[f.source.start:f.source.end] == f.source.quote for f in spans)
    assert spans == fragment_news(doc)
    changed = fragment_news(doc.model_copy(update={"text": text + " Новый текст."}))
    assert spans[0].id != changed[0].id


def test_attach_preserves_calculations_and_is_idempotent():
    ledger = demo().ledger
    result = attach_news_fragments(ledger)
    assert result.entries[:len(ledger.entries)] == ledger.entries
    assert result.documents == ledger.documents
    assert attach_news_fragments(result) == result
    assert len(result.entries) > len(ledger.entries)


@pytest.mark.parametrize("persona", ["PER-01", "PER-03", "PER-04"])
def test_role_profile_runs_full_pipeline(persona):
    case = demo().model_copy(update={"persona": persona})
    result = run_case(case, MemoryReplay(), manifest=profile("role_context"))
    assert result["status"] == "offline_replay"
    assert result["context_ablation"]["role_card"]["persona"] == persona
    assert not result["publishable"]


def test_focus_gate_rejects_orphan_and_unselected_fact():
    case = demo()
    replay = MemoryReplay()
    core = EventCore.model_validate(replay.responses["PRM-05"])
    overlay = PersonalOverlay.model_validate(replay.responses["PRM-06"])
    card = assemble_card(case, core, overlay)
    for c in card.why_you + card.model_inference.impact_chain:
        c.source_ids = [r for r in c.source_ids if r != "N1"]
    assert any("orphan" in s for s in check_fact_focus(card, case.ledger, {"N1"}))
    assert any("outside" in s for s in check_fact_focus(card, case.ledger, set()))
    card.why_you[0].source_ids.append("N1")
    assert check_fact_focus(card, case.ledger, {"N1"}) == []


def test_focus_is_explicit_and_critic_stays_enabled():
    case = demo()
    gateway = MemoryReplay()
    result = run_case(case, gateway, manifest=profile("role_focus"), selected_news_ids=["N1"])
    assert result["status"] == "offline_replay"
    assert gateway.calls[-1] == "PRM-07"
    assert result["context_ablation"]["semantic_selection_verified"] is False
    with pytest.raises(ValueError, match="selection"):
        run_case(case, gateway, manifest=profile("fragment_focus"))
    with pytest.raises(ValueError, match="atomic"):
        run_case(case, gateway, manifest=profile("fragment_focus"), selected_news_ids=["D_REL"])
    with pytest.raises(ValueError, match="profile"):
        run_case(case, gateway, selected_news_ids=["N1"])


def test_focus_failure_blocks_before_critic():
    gateway = MemoryReplay()
    for c in gateway.responses["PRM-06"]["why_you"] + gateway.responses["PRM-06"]["impact_chain"]:
        c["source_ids"] = [r for r in c["source_ids"] if r != "N1"]
    result = run_case(demo(), gateway, max_repairs=0, manifest=profile("fragment_focus"), selected_news_ids=["N1"])
    assert result["status"] == "needs_fix"
    assert any("orphan" in i for i in result["issues"])
    assert "PRM-07" not in gateway.calls


def test_focus_rejects_full_multisentence_quote_before_gateway():
    data = demo().model_dump(mode="json")
    doc = data["ledger"]["documents"][0]
    doc["text"] += " Бюджетный фон."
    source = data["ledger"]["entries"][0]["source"]
    source.update(quote=doc["text"], end=len(doc["text"]))
    case = CaseInput.model_validate(data)
    gateway = MemoryReplay()
    with pytest.raises(ValueError, match="atomic"):
        run_case(case, gateway, manifest=profile("fragment_focus"), selected_news_ids=["N1"])
    assert gateway.calls == []


def test_actual_atomic_ids_flow_through_full_pipeline():
    case = demo()
    case = CaseInput.model_validate({**case.model_dump(mode="json"),
        "ledger": attach_news_fragments(case.ledger).model_dump(mode="json")})
    ref = fragment_news(case.ledger.documents[0])[0].id
    gateway = MemoryReplay()
    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return ref if value == "N1" else value
    gateway.responses = replace(gateway.responses)
    result = run_case(case, gateway, manifest=profile("fragment_focus"), selected_news_ids=[ref])
    assert result["status"] == "offline_replay"
    assert not result["issues"]
    assert gateway.calls == ["PRM-05", "PRM-06", "PRM-07"]


def test_selected_inference_cannot_add_other_news_reference():
    case = demo()
    data = case.ledger.model_dump(mode="json")
    duplicate = {**data["entries"][0], "id": "OTHER_NEWS"}
    data["entries"].append(duplicate)
    case = CaseInput.model_validate({**case.model_dump(mode="json"), "ledger": data})
    gateway = MemoryReplay()
    gateway.responses["PRM-06"]["actions_or_questions"][0]["source_ids"].append("OTHER_NEWS")
    result = run_case(case, gateway, max_repairs=0, manifest=profile("fragment_focus"), selected_news_ids=["N1"])
    assert any("inference cites news outside" in i for i in result["issues"])
    assert "PRM-07" not in gateway.calls


def test_generation_view_hides_unselected_text_and_preserves_offsets():
    data = demo().model_dump(mode="json")
    doc = data["ledger"]["documents"][0]
    doc["text"] += " Скрытая посторонняя новость."
    source = data["ledger"]["entries"][0]["source"]
    source.update(quote=doc["text"], end=len(doc["text"]))
    case = CaseInput.model_validate(data)
    ledger = attach_news_fragments(case.ledger)
    before = ledger.model_dump(mode="json")
    fragments = fragment_news(ledger.documents[0])
    view = focused_generation_view(ledger, {fragments[0].id})
    assert "Скрытая" not in json.dumps(view, ensure_ascii=False)
    assert "N1" not in {e["id"] for e in view["entries"]}
    assert ledger.model_dump(mode="json") == before
    assert all("input_ids" not in e for e in view["entries"] if e["kind"] == "COMPUTED_VALUE")
    span = fragments[0].source
    assert view["documents"][0]["text"][span.start:span.end] == span.quote


def test_scoped_pipeline_keeps_full_context_for_critic_only():
    data = demo().model_dump(mode="json")
    doc = data["ledger"]["documents"][0]
    doc["text"] += " Скрытая посторонняя новость."
    source = data["ledger"]["entries"][0]["source"]
    source.update(quote=doc["text"], end=len(doc["text"]))
    case = CaseInput.model_validate(data)
    case = CaseInput.model_validate({**case.model_dump(mode="json"), "ledger": attach_news_fragments(case.ledger).model_dump(mode="json")})
    ref = fragment_news(case.ledger.documents[0])[0].id
    gateway = MemoryReplay()
    raw = json.dumps(gateway.responses).replace('"N1"', '"'+ref+'"')
    gateway.responses = json.loads(raw)
    manifest = json.loads((ROOT / "prompts/profiles/role_focus_v2.json").read_text())
    result = run_case(case, gateway, manifest=manifest, selected_news_ids=[ref])
    assert result["status"] == "offline_replay"
    for prompt in ("PRM-05", "PRM-06"):
        assert "Скрытая" not in json.dumps(result["requests"][prompt], ensure_ascii=False)
    assert "Скрытая" in json.dumps(result["requests"]["PRM-07"], ensure_ascii=False)


def test_study_lock_blocks_duplicate_submission_and_releases(tmp_path):
    lock = runpy.run_path(str(ROOT / "scripts/context_run_lock.py"))["study_lock"]
    with lock(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with lock(tmp_path):
                pytest.fail("duplicate runner entered")
    with lock(tmp_path):
        pass
