import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from pe.improvement import audit_partitions,assert_access,digest,load,prepare,seal
from pe.improvement_data import new_cases
from pe.interpretation import InterpretationPlan,check_plan,to_overlay
from pe.pipeline import CaseInput,check_overlay
from pe.prompts import PROMPT_DIR,prepare_request
from pe.session import PromptSession
from types import SimpleNamespace


def plan_fixture():
    def step(text,refs):return dict(text=text,source_ids=refs)
    return dict(why_you=[dict(id='personal_w',text='Оптовый профиль связан с отсрочками.',source_ids=['N1','C2'])],
        impact_chain=[dict(id='personal_m',factor='REG',effect='negative',
            event=step('Порядок подтверждения счетов изменён.',['N1']),
            channel=step('Неподтверждённые счета требуют дополнительной проверки.',['N1']),
            company=step('У компании оптовый профиль с отсрочками.',['C2']),
            consequence=step('Возможна задержка расчётов.',['D_REG','C2']),
            changes_if=step('Вывод требует уточнения после проверки договоров.',['C3']))],
        risks=[],opportunities=[],actions_or_questions=[dict(id='personal_a',resolves=['personal_m'],
            check=step('Какие счета требуют подтверждения?',['N1','C3']),
            decision_use=step('Ответ покажет, какие договоры надо проверить.',['C3']))],unknowns=[])


def test_structured_plan_renders_only_checked_parts_and_complete_sources():
    plan=InterpretationPlan.model_validate(plan_fixture());case=CaseInput.model_validate(new_cases()[0]['case'])
    assert check_plan(case,plan)==[]
    overlay=to_overlay(plan)
    assert check_overlay(case,overlay)==[]
    assert 'после проверки договоров' in overlay.impact_chain[0].text
    assert set(overlay.impact_chain[0].source_ids)=={'N1','C2','C3','D_REG'}
    assert 'Ответ покажет' in overlay.actions_or_questions[0].text


def test_step_cannot_borrow_a_number_from_another_step():
    data=plan_fixture();data['impact_chain'][0]['company']['text']='У компании долг 900 руб.'
    case=CaseInput.model_validate(new_cases()[0]['case'])
    assert any('this step evidence' in issue for issue in check_plan(case,InterpretationPlan.model_validate(data)))


def test_actions_require_existing_mechanism_and_no_duplicated_factors():
    data=plan_fixture();data['actions_or_questions'][0]['resolves']=['invented']
    with pytest.raises(ValidationError):InterpretationPlan.model_validate(data)
    data=plan_fixture();other=deepcopy(data['impact_chain'][0]);other['id']='other';other['consequence']['text']='Another consequence'
    data['impact_chain'].append(other)
    with pytest.raises(ValidationError):InterpretationPlan.model_validate(data)


def test_synthetic_partitions_are_group_disjoint_and_reject_story_and_client_leakage():
    rows=new_cases();assert audit_partitions(rows)==dict(cases=60,source_groups=10,clients=60,partitions=dict(dev=24,validation=18,test=18))
    leaked=deepcopy(rows);leaked[0]['split']='test'
    with pytest.raises(ValueError,match='leakage|duplicate'):audit_partitions(leaked)


def test_baseline_and_candidate_are_explicit_profiles_and_global_default_stays_baseline():
    payload={'event_core':{},'ledger':{},'persona':'PER-03','computed':{}}
    baseline=json.loads((PROMPT_DIR/'profiles/baseline_v1.json').read_text())
    candidate=json.loads((PROMPT_DIR/'profiles/candidate_v1.json').read_text())
    assert prepare_request('PRM-06',payload)['prompt_version']=='4'
    assert prepare_request('PRM-06',payload,manifest=baseline)['schema']=='PersonalOverlay'
    assert prepare_request('PRM-06',payload,manifest=candidate)['schema']=='InterpretationPlan'
    assert baseline['prompts']['PRM-07']==candidate['prompts']['PRM-07']


def test_session_freezes_profile_instead_of_following_mutated_manifest():
    manifest=json.loads((PROMPT_DIR/'profiles/baseline_v1.json').read_text())
    session=PromptSession(None,'test',manifest=manifest)
    manifest['prompts']['PRM-06']['version']='999'
    assert session.manifest['prompts']['PRM-06']['version']=='4'


def test_test_partition_locked_until_validation_and_frozen_state_protected(tmp_path):
    out=tmp_path/'study';prepare(out);protocol,_=load(out)
    with pytest.raises(ValueError,match='test locked'):assert_access(out,protocol,'test')
    with pytest.raises(ValueError,match='validation'):seal(out)
    raw=json.loads((out/'cases.json').read_text());raw[0]['segment']='tampered'
    (out/'cases.json').write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='changed'):load(out)


def test_candidate_full_pipeline_checks_rendered_claims_with_independent_critic():
    import html
    from pe.pipeline import run_case,QUESTIONS
    class Gateway:
        mode='evaluation'
        def chat(self,operation,messages,**kwargs):
            text=messages[1]['content'];payload=json.loads(html.unescape(text.split('>\n',1)[1].rsplit('\n</',1)[0]))
            if operation=='PRM-05':output=dict(confirmed_facts=[dict(id='core_f',text='Порядок подтверждения счетов изменён.',source_ids=['N1'])],bank_view_items=[],common_effects=[],unknowns=['Нет позиции банка'])
            elif operation=='PRM-06':output=plan_fixture()
            else:
                card=payload['card'];claims=card['confirmed_facts']+card['bank_view_items']+card['why_you']
                for field in ('impact_chain','risks','opportunities','actions_or_questions'):claims+=card['model_inference'][field]
                output=dict(claims=[dict(id=c['id'],text='',layer=card['provenance_labels'][c['id']],verdict='supported',evidence_ids=c['source_ids']) for c in claims],
                    numbers_check=[],coverage_6_questions={q:True for q in QUESTIONS},overall='pass',fix_instructions=[])
            return SimpleNamespace(text=json.dumps(output),model='Qwen3.6 35B A3B' if operation=='PRM-07' else 'GLM-5.3-Flash',tokens_in=10,tokens_out=10,cost_rub=None,cache_hit=False)
    profile=json.loads((PROMPT_DIR/'profiles/candidate_v1.json').read_text())
    result=run_case(CaseInput.model_validate(new_cases()[0]['case']),Gateway(),manifest=profile)
    assert result['status']=='evaluated' and not result['publishable']
    assert len(result['calls'])==3 and result['interpretation_plan']
    assert 'после проверки договоров' in result['card']['model_inference']['impact_chain'][0]['text']


def test_experiment_allowance_is_global_and_refuses_extra_calls(tmp_path,monkeypatch):
    from pe import improvement
    out=tmp_path/'study';prepare(out)
    class Gateway:
        blocked=False
        def __init__(self,**kwargs):pass
        def chat(self,**kwargs):return SimpleNamespace(tokens_in=10,tokens_out=20)
        def close(self):pass
    monkeypatch.setattr(improvement,'EvaluationGateway',Gateway)
    limits=dict(gateway_calls=1,additional_tokens=10000,shared_daily_tokens=3000000,call_wall_seconds=120)
    gateway=improvement.Allowance(out,limits)
    gateway.chat(messages=[],max_tokens=100)
    gateway.close();other=improvement.Allowance(out,limits)
    with pytest.raises(RuntimeError,match='allowance'):other.chat(messages=[],max_tokens=100)
    other.close()
