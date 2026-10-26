from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pe.contracts import INPUT_SCHEMAS, OUTPUT_SCHEMAS, Chunk
from pe.integration import backend_payload, load_da_features, quality_trace
from pe.numbers import unsupported_numeric_tokens
from pe.pipeline import CaseInput, run_case
from pe.prompts import PROMPT_DIR, prepare_request
from pe.seed_cases import golden_cases, task_payloads
from pe.session import PromptSession
from pe.tasks import offer_gate

ROOT = Path(__file__).resolve().parent.parent


def test_evaluation_wall_deadline_interrupts_a_stalled_call_and_restores_alarm():
    import signal
    import time
    if not hasattr(signal,'SIGALRM'):pytest.skip('POSIX evaluation CLI')
    from pe.acceptance import EvaluationGateway
    gateway=EvaluationGateway.__new__(EvaluationGateway)
    gateway.max_call_seconds=0.01;gateway.blocked=False;gateway.errors=()
    gateway.gateway=SimpleNamespace(chat=lambda **kwargs:time.sleep(1))
    previous=signal.getsignal(signal.SIGALRM)
    with pytest.raises(RuntimeError,match='wall deadline'):
        gateway.chat()
    assert gateway.blocked
    assert signal.getsignal(signal.SIGALRM)==previous
    assert signal.getitimer(signal.ITIMER_REAL)[0]==0


def test_task_repairs_source_ids_once_and_keeps_original_response():
    from pe.tasks import run_task
    class Gateway:
        mode="evaluation"
        def __init__(self):self.messages=[]
        def chat(self,**kwargs):
            self.messages.append(kwargs['messages'])
            source="NEWS_EV-01" if len(self.messages)==1 else "N1"
            response=dict(claims=[dict(id="c",text="Ставка 14%",source_ids=[source],layer="fact")],limitations=[],questions=[])
            return SimpleNamespace(text=json.dumps(response),model="GLM-5.3-Flash",tokens_in=1,tokens_out=1,cost_rub=None,cache_hit=False)
    gateway=Gateway();payload=task_payloads(golden_cases()['GS-01'])['PRM-09']
    result=run_task('PRM-09',payload,gateway)
    assert len(gateway.messages)==2 and result['status']=='checked'
    assert len(result['raw_outputs'])==2 and len(result['repairs'])==1
    assert result['raw_outputs'][0]['claims'][0]['source_ids']==['NEWS_EV-01']
    assert result['output']['claims'][0]['source_ids']==['N1']
    assert not result['publishable']


def test_dialogue_critic_repair_preserves_rejected_answer_in_audit():
    from pe.tasks import run_task
    from pe.pipeline import QUESTIONS
    class Gateway:
        mode="evaluation"
        def __init__(self):self.operations=[]
        def chat(self,**kwargs):
            self.operations.append(kwargs['operation'])
            if kwargs['operation']=='PRM-07':
                result=dict(claims=[dict(id='c',text='',layer='fact',verdict='unsupported',evidence_ids=['N1'])],
                    numbers_check=[],coverage_6_questions={q:True for q in QUESTIONS},overall='fix',fix_instructions=['unsupported'])
            else:
                first=len(self.operations)==1
                result=dict(answer='Ставка 14%' if first else 'Нет данных',layers=['fact'] if first else [],
                    citations=['N1'] if first else [],out_of_scope=not first,follow_up_suggestions=[],
                    claims=[dict(id='c',text='Ставка 14%',source_ids=['N1'],layer='fact')] if first else [])
            return SimpleNamespace(text=json.dumps(result),model='Qwen3.6 35B A3B',tokens_in=1,tokens_out=1,cost_rub=None,cache_hit=False)
    gateway=Gateway();result=run_task('PRM-08',task_payloads(golden_cases()['GS-01'])['PRM-08'],gateway)
    assert gateway.operations==['PRM-08','PRM-07','PRM-08']
    assert result['status']=='checked' and result['output']['out_of_scope']
    assert len(result['raw_outputs'])==2 and result['raw_outputs'][0]['answer']=='Ставка 14%'
    assert [r['kind'] for r in result['repairs']]==['critic']


@pytest.mark.parametrize("gs", ["GS-01","GS-04","GS-06"])
def test_all_seventeen_prompts_have_executable_contracts(gs):
    manifest = json.loads((PROMPT_DIR / "manifest.json").read_text())
    payloads = task_payloads(golden_cases()[gs])
    assert set(manifest["prompts"]) == {f"PRM-{n:02}" for n in range(1,18)}
    for prompt_id, spec in manifest["prompts"].items():
        assert spec["schema"] in OUTPUT_SCHEMAS
        INPUT_SCHEMAS[spec["input_schema"]].model_validate(payloads[prompt_id])
        request = prepare_request(prompt_id, payloads[prompt_id], validate_input=True)
        assert "<untrusted_source" in request["messages"][1]["content"]


@pytest.mark.parametrize("text,evidence,ok", [
    ("Ставка 14,0%", "Ставка 14.0%", True),
    ("Инфляция 0,01%", "Инфляция -0,01%", False),
    ("Ставка 14 руб.", "Ставка 14%", False),
    ("Изменение 1 п.п.", "Изменение 1%", False),
    ("На 11.09.2026", "2026-09-11", True),
    ("На 12.09.2026", "2026-09-11", False),
    ("1000 руб.", "1 000 руб.", True),
    ("Источник DOC-07_p2_0", "", True),
    ("Позиция от 2 сентября 2026 года", "2026-09-02", True),
])
def test_sign_unit_date_normalization(text,evidence,ok):
    assert (not unsupported_numeric_tokens(text,evidence)) is ok


def test_chart_estimates_cannot_become_confirmed_numbers():
    with pytest.raises(ValidationError):
        Chunk.model_validate(dict(doc_id="D",page=1,title="chart",as_of_date=None,needs_manual_check=True,
            blocks=[dict(block_type="chart_estimate",text="estimate",numbers=[dict(value=12,unit="%",subject="x")])]))


def test_vlm_bounds_do_not_truncate_existing_backend_source_chunks():
    page=dict(doc_id="D",page=1,title="source",as_of_date=None,needs_manual_check=True,
              blocks=[dict(block_type="text",text="x"*301,numbers=[])])
    from pe.contracts import SourceChunk
    assert len(SourceChunk.model_validate(page).blocks[0].text)==301
    with pytest.raises(ValidationError):Chunk.model_validate(page)
    page["blocks"]=[dict(block_type="text",text="short",numbers=[])]*7
    with pytest.raises(ValidationError):Chunk.model_validate(page)
    page["blocks"]=[dict(block_type="table",text="short",numbers=[dict(value=1,unit="%",subject="x")]*13)]
    with pytest.raises(ValidationError):Chunk.model_validate(page)


def test_vlm_normalization_never_invents_a_day_or_promotes_chart_estimates():
    from pe.vlm import normalize_page
    page=dict(doc_id="D",page=1,title="report",as_of_date="август 2026",needs_manual_check=False,
        blocks=[dict(block_type="table",text="a "*200,numbers=[dict(value=14,unit="%",subject="ставка",date="конец 2026")]),
                dict(block_type="chart_estimate",text="estimate",numbers=[dict(value=9,unit="%",subject="x")])])
    data,changes=normalize_page(json.dumps(page));result=Chunk.model_validate(data)
    assert changes and result.needs_manual_check
    assert result.as_of_date is None and result.blocks[0].numbers[0].date is None
    assert "конец 2026" in result.blocks[0].numbers[0].subject
    assert result.blocks[0].needs_manual_check and len(result.blocks[0].text)<=300
    assert result.blocks[1].numbers==[] and result.blocks[1].needs_manual_check
    from pe.contracts import BankInput
    with pytest.raises(ValidationError):BankInput.model_validate(dict(chunks=[data]))


def test_bank_table_units_apply_to_year_value_columns():
    from pe.evidence import QuotedEvidence,Document,quoted_text
    quote="Ставка, конец года, % — 2026П 14,0 | 2027П 12,0"
    entry=QuotedEvidence(id="B",kind="BANK_VIEW",source=dict(document_id="DOC-07_p2_0",start=0,end=len(quote),quote=quote),number=14,unit="% годовых")
    doc=Document(id="DOC-07_p2_0",kind="bank",text=quote,page=2,as_of="2026-09-11")
    assert not unsupported_numeric_tokens("Прогноз на 2027 год — 12,0%.",quoted_text(entry,doc))


def test_uncomputed_extra_channel_is_discarded_not_published():
    case=CaseInput.model_validate_json((ROOT/"examples/demo/case.json").read_text())
    gateway=RepairReplay();gateway.counts["PRM-06"]=1
    gateway.responses["PRM-06"]["impact_chain"].append(dict(id="extra",factor="FX",effect="positive",text="Unsupported extra channel",source_ids=["N1"]))
    result=run_case(case,gateway)
    assert result["status"]=="offline_replay"
    assert result["partial"]
    assert result["discarded_claims"]==[dict(id="extra",reason="factor_not_computed")]
    assert all(claim["id"]!="extra" for claim in result["card"]["model_inference"]["impact_chain"])


class RepairReplay:
    mode="replay"
    def __init__(self):
        self.counts={}
        self.responses={p.stem:json.loads(p.read_text()) for p in (ROOT/"examples/demo/responses").glob("*.json")}
    def chat(self,*,operation,**kwargs):
        n=self.counts.get(operation,0);self.counts[operation]=n+1
        output=deepcopy(self.responses[operation])
        if operation=="PRM-06" and n==0:output["impact_chain"][0]["effect"]="positive"
        return SimpleNamespace(text=json.dumps(output),model=None,tokens_in=None,tokens_out=None,cost_rub=None,cache_hit=None)


def test_repair_is_bounded_and_corrects_only_failed_step():
    case=CaseInput.model_validate_json((ROOT/"examples/demo/case.json").read_text())
    gateway=RepairReplay()
    result=run_case(case,gateway)
    assert result["status"]=="offline_replay"
    assert result["repairs"]==1
    assert gateway.counts=={"PRM-05":1,"PRM-06":2,"PRM-07":1}
    assert not result["publishable"]
    card=result["card"]
    from pe.schemas import InsightCard
    typed=InsightCard.model_validate(card)
    mapped=backend_payload(typed,case)
    assert mapped["bank_view"]["status"]=="missing"
    assert mapped["trace_id"]==case.ledger.trace_id
    trace=quality_trace(typed,case.ledger,synthetic=True)
    assert trace["synthetic"]
    assert trace["insight"]["sources"][0]["is_mock"]


def test_same_family_judge_is_rejected():
    with pytest.raises(ValueError,match="different model families"):
        PromptSession(RepairReplay(),"x",overrides={"PRM-07":"GLM-5.3-Flash"})


def test_da_adapter_does_not_export_personal_fields(tmp_path):
    (tmp_path/"client_features.csv").write_text("SYNTHETIC_U,sector_profile,loan_flag,FULL_NAME\nu1,SP-09,,Secret Person\n")
    fields=load_da_features(tmp_path,"u1","alias")
    assert len(fields)==2
    assert any(field.value is None for field in fields)
    assert all("Secret Person" not in field.model_dump_json() for field in fields)


def test_unknown_product_does_not_permit_an_offer():
    assert not offer_gate(relevance_passed=True,relevant_product=True,active_client=True,
                          product_known=False,product_absent_or_expandable=True)["allowed"]


def test_da_actual_export_column_names_are_supported(tmp_path):
    (tmp_path/"client_features.csv").write_text("SYNTHETIC_U,F03_sector_profile,F08_LOA,F13_completeness\nu1,SP-09,1,0.8\n")
    (tmp_path/"exposures.csv").write_text("SYNTHETIC_U,eps_RATE,E15_rate_net\nu1,0.86,-0.72\n")
    fields=load_da_features(tmp_path,"u1","alias")
    assert {field.feature_id for field in fields}=={"F-03","F-08","F-13","E-14","E-15"}


def test_critic_text_is_bound_to_input_by_id():
    case=CaseInput.model_validate_json((ROOT/"examples/demo/case.json").read_text())
    gateway=RepairReplay()
    gateway.counts["PRM-06"]=1
    for claim in gateway.responses["PRM-07"]["claims"]:claim["text"]=""
    result=run_case(case,gateway)
    assert result["status"]=="offline_replay"
    assert all(c["text"] for c in result["critic"]["claims"])
    assert result["calls"][-1]["bound_claim_hashes"]
