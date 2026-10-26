"""New arbitrary news flows through extraction + DA + PE without a prepared case.
Model responses are controlled; these tests do not measure model quality.
"""
import csv
import html
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PE_ROOT = Path(__file__).resolve().parents[2] / "report_scripts"
sys.path.insert(0, str(PE_ROOT))
from pe.backend_bridge import BackendBridge
from pe.production import ProductionProvider, import_profiles, da_functions
from pe.evidence import EvidenceLedger
from app.search import search_news
from app.util import pseudonym


class Models:
    mode = "evaluation"

    def __init__(self):
        self.calls = []

    def close(self):
        pass

    def chat(self, *, operation, role, messages, **kwargs):
        text = messages[1]["content"]
        payload = json.loads(html.unescape(text.split('<untrusted_source id="request_data">\n')[1].rsplit('\n</untrusted_source>', 1)[0]))
        self.calls.append((operation, role, payload))
        if operation == "PRM-01":
            source = payload["document"]
            sentence = source["text"].split(".")[0]+"."
            output = dict(event_type="ET-01", entities=[], facts=[dict(value="сохранил", unit=None, date=None, subject="ставка",
                source_id=source["id"], source_span=sentence, certainty="confirmed")], iota={"RATE":1.,"FX":.5},
                direction={"RATE":0,"FX":1}, severity=3, geo_scope="national", horizon="unknown", rationale="test fixture")
        elif operation == "PRM-05":
            entry = next(e for e in payload["entries"] if e["kind"] == "NEWS_FACT")
            output = dict(confirmed_facts=[dict(id="core_fact", text=entry["source"]["quote"], source_ids=[entry["id"]])],
                bank_view_items=[], common_effects=[], unknowns=["Позиция банка отсутствует"])
        elif operation == "PRM-06":
            ref = payload["selected_news_ids"][0]
            output = dict(why_you=[dict(id="personal_why", text="Событие связано с известными продуктами компании.", source_ids=[ref,"C_PRODUCTS"])],
                impact_chain=[dict(id="personal_impact",factor="RATE",effect=payload["computed"]["effect_signs"]["RATE"],
                    text="Сохранение ставки не меняет её текущий уровень; условия договоров нужно уточнить.", source_ids=[ref,"C_PRODUCTS","SIGN_RATE"])],
                risks=[], opportunities=[], actions_or_questions=[dict(id="personal_question",text="Какие условия ставки заданы в договорах?", source_ids=["C_LIMITS"])], unknowns=[])
        elif operation == "PRM-08":
            entry=next(e for e in payload["ledger"]["entries"] if e["kind"]=="NEWS_FACT")
            claim=dict(id="chat_fact",text=entry["source"]["quote"],source_ids=[entry["id"]],layer="fact")
            output=dict(answer=claim["text"],claims=[claim],citations=claim["source_ids"],layers=["fact"],out_of_scope=False,follow_up_suggestions=[])
        else:
            card = payload["card"]
            claims = [(c,"fact") for c in card["confirmed_facts"]]+[(c,"bank_view") for c in card["bank_view_items"]]
            claims += [(c,"ai_inference") for c in card["why_you"]]
            claims += [(c,"ai_inference") for key in ("impact_chain","risks","opportunities","actions_or_questions") for c in card["model_inference"][key]]
            output = dict(claims=[dict(id=c["id"],text=c["text"],layer=layer,verdict="supported",evidence_ids=c["source_ids"]) for c,layer in claims],
                numbers_check=[],coverage_6_questions={k:True for k in ("what_happened","why_you","implications","actions","bank_view","basis")}, overall="pass",fix_instructions=[])
        model = {"extract":"Gemma 4 26B A4B","reasoning":"GLM-5.3-Flash","general":"Qwen3.6 35B A3B"}[role]
        return SimpleNamespace(text=json.dumps(output,ensure_ascii=False),model=model,tokens_in=10,tokens_out=20,cost_rub=None,cache_hit=False)


@pytest.fixture
def production(conn, tmp_path):
    uid = "private-fixture-company"
    conn.execute("INSERT INTO clients(synthetic_u,mainokved_name) VALUES(?,?)",(uid,"Тестовая отрасль"))
    text = "Регулятор сохранил ключевую ставку. Новый текст вне готового набора."
    conn.execute("""INSERT INTO documents(doc_id,source_id,title,url,canonical_url,published_at,published_at_utc,fetched_at,
        text,snippet,tier_weight,content_hash,ready_at,origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("new-event","fixture","Новый текст","synthetic://new","synthetic://new","2026-10-01T00:00:00Z","2026-10-01T00:00:00Z",
         "2026-10-01T00:00:00Z",text,text,.5,"fixture","ready","synthetic"))
    data = tmp_path / "aggregates"; data.mkdir()
    rows = {"client_features.csv":dict(SYNTHETIC_U=uid,F03_sector_profile="SP-01",F10_cl_type="existing",F13_completeness=.8,
                 F08_LOA=1,F08_LEA=0,F08_DEP=0,F08_RKO=1,F08_GAR=0,F08_ZPP=0,F08_FACTORING=0),
            "exposures.csv":dict(SYNTHETIC_U=uid,eps_RATE=.8,eps_FX=.3,E15_rate_net=-.6),
            "payment_features.csv":dict(SYNTHETIC_U=uid,G03_HHI_in=.2,G03_HHI_out=.3)}
    for name,row in rows.items():
        with (data/name).open("w",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
    import_profiles(conn,data,synthetic=True)
    models=Models()
    provider=ProductionProvider(gateway_factory=lambda c:models,pseudonym=pseudonym,store_root=Path(__file__).resolve().parents[1])
    return uid, models, provider


def test_arbitrary_new_event_automatically_gets_da_and_pe_context(conn, production):
    uid, models, provider = production
    service=BackendBridge(gateway_factory=lambda c:models,pseudonym=pseudonym,search_news=search_news,case_provider=provider,demo=True)
    result=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert result["status"]=="demo" and result["publishable"] is False
    assert result["score"]["rank_available"]
    assert [c[0] for c in models.calls]==["PRM-01","PRM-05","PRM-06","PRM-07"]
    assert uid not in json.dumps([c[2] for c in models.calls],ensure_ascii=False)
    source=json.loads(conn.execute("SELECT case_json FROM pe_card_contexts").fetchone()[0])
    F,cfg=da_functions()
    expected=F.fm06_relevance({"RATE":.8,"FX":.3},{"RATE":1.,"FX":.5})[1]
    assert next(e["value"] for e in source["ledger"]["entries"] if e["id"]=="REL")==expected
    unknown=next(e for e in source["ledger"]["entries"] if e["id"]=="SIGN_FX")
    assert unknown["kind"]=="UNKNOWN_EFFECT" and unknown["value"]=="two_branches" and "formula_id" not in unknown


def test_event_extraction_reused_across_roles_but_not_after_source_changes(conn, production):
    uid, models, provider=production
    for persona in ("PER-03","PER-04"):
        provider(conn,client_id=uid,event_doc_id="new-event",persona=persona)
    assert [c[0] for c in models.calls]==["PRM-01"]
    conn.execute("UPDATE documents SET text=text || ' Дополнение.' WHERE doc_id='new-event'")
    provider(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert len(models.calls)==2


def test_changed_client_invalidates_old_da_profile_before_any_llm_call(conn, production):
    uid, models, provider=production
    conn.execute("UPDATE clients SET cl_type='changed' WHERE synthetic_u=?",(uid,))
    with pytest.raises(ValueError,match="PE_PROFILE_NOT_READY"):
        provider(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert not models.calls


def test_unknown_effect_cannot_be_relabelled_as_positive_or_hide_a_cycle(conn, production):
    uid, _, provider=production
    prepared=provider(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    payload=prepared.case.ledger.model_dump(mode="json")
    item=next(e for e in payload["entries"] if e["kind"]=="UNKNOWN_EFFECT")
    item["input_ids"]=[item["id"]]
    with pytest.raises(ValueError,match="cyclic"):
        EvidenceLedger.model_validate(payload)
    item["input_ids"]=["NEWS"];item["value"]="positive"
    with pytest.raises(ValueError):
        EvidenceLedger.model_validate(payload)


def test_invalid_role_is_rejected_before_extraction(conn, production):
    uid,models,provider=production
    with pytest.raises(ValueError,match="unknown persona"):
        provider(conn,client_id=uid,event_doc_id="new-event",persona="PER-99")
    assert models.calls==[]


def test_unconfigured_generation_and_chat_gateways_withhold_cleanly(conn, production):
    from pe.backend_bridge import Withheld
    uid,models,provider=production
    def unavailable(conn):
        raise RuntimeError("LLM_NOT_CONFIGURED")
    service=BackendBridge(gateway_factory=unavailable,pseudonym=pseudonym,search_news=search_news,case_provider=provider,demo=True)
    with pytest.raises(Withheld):
        service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]==0
    service.gateway_factory=lambda c:models
    card=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    service.gateway_factory=unavailable
    with pytest.raises(Withheld):
        service.ask(conn,card["card_id"],"Что известно?")
    assert conn.execute("SELECT COUNT(*) FROM card_questions").fetchone()[0]==0


def test_valid_automatic_card_reused_without_new_model_calls_but_source_change_invalidates(conn, production):
    uid,models,provider=production
    service=BackendBridge(gateway_factory=lambda c:models,pseudonym=pseudonym,search_news=search_news,case_provider=provider,demo=True)
    first=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    calls=len(models.calls)
    reused=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert reused["reused"] and reused["card_id"]==first["card_id"]
    assert len(models.calls)==calls
    conn.execute("UPDATE documents SET text=text || ' Новое уточнение источника.' WHERE doc_id='new-event'")
    updated=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert updated["card_id"]!=first["card_id"] and len(models.calls)>calls


def test_profile_revision_manifest_and_asof_remain_part_of_reuse_policy(conn, production):
    from pe.prompts import PROMPT_DIR
    uid,models,provider=production
    service=BackendBridge(gateway_factory=lambda c:models,pseudonym=pseudonym,search_news=search_news,case_provider=provider,demo=True)
    first=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    service.manifest=json.loads((PROMPT_DIR / "profiles/connected_v1.json").read_text())
    service.manifest["version"]="test-new-version"
    changed=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03")
    assert changed["card_id"]!=first["card_id"]
    historical=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03",as_of="2026-10-02")
    assert historical["card_id"]!=changed["card_id"]
    conn.execute("UPDATE pe_da_profiles SET revision='updated' WHERE client_id=?",(uid,))
    revised=service.generate(conn,client_id=uid,event_doc_id="new-event",persona="PER-03",as_of="2026-10-02")
    assert revised["card_id"]!=historical["card_id"]
