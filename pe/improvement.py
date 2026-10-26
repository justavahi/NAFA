"""Frozen baseline/candidate experiments with guarded, blinded evaluation."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import statistics
from types import SimpleNamespace

from pydantic import Field
from .acceptance import EvaluationGateway,ROOT,save
from .improvement_data import new_cases
from .pipeline import CaseInput,run_case
from .prompts import PROMPT_DIR,prepare_request
from .schemas import StrictModel
from .session import MODEL_ROLES,PromptSession,family

PILOT=['NX-00-00','NX-01-01','NX-02-02']
PARTITIONS={'dev':PILOT,'validation':['NX-04-00','NX-05-01','NX-06-02'],'test':['NX-07-00','NX-08-01','NX-09-02']}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def fingerprint():
    paths=sorted((ROOT/'pe').glob('*.py'))+sorted(PROMPT_DIR.glob('*.txt'))+sorted((PROMPT_DIR/'profiles').glob('*.json'))
    return digest({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def audit_partitions(records):
    seen_ids=set();groups={};clients={};news={}
    for row in records:
        case=CaseInput.model_validate(row['case']);split=row['split']
        if split not in PARTITIONS:raise ValueError('unknown split')
        if row['case_id'] in seen_ids:raise ValueError('duplicate case')
        seen_ids.add(row['case_id'])
        if row['source_group']!=case.ledger.event_id or row['client_group']!=case.ledger.client_id:raise ValueError('group metadata differs from case')
        for registry,key in [(groups,row['source_group']),(clients,row['client_group'])]:
            if key in registry and registry[key]!=split:raise ValueError('group leakage across partitions')
            registry[key]=split
        for doc in case.ledger.documents:
            if doc.kind!='news':continue
            key=digest(' '.join(doc.text.casefold().split()))
            if key in news and news[key]!=split:raise ValueError('news duplicate across partitions')
            news[key]=split
    return dict(cases=len(records),source_groups=len(groups),clients=len(clients),
        partitions={s:sum(r['split']==s for r in records) for s in PARTITIONS})


def prepare(out:Path,baseline_run:Path|None=None,candidate_profile='candidate_v1.json'):
    if out.exists():raise ValueError('new experiment directory required')
    records=new_cases();audit=audit_partitions(records)
    baseline=json.loads((PROMPT_DIR/'profiles/baseline_v1.json').read_text())
    if candidate_profile not in {'candidate_v1.json','candidate_v2.json'}:raise ValueError('unknown candidate profile')
    candidate=json.loads((PROMPT_DIR/'profiles'/candidate_profile).read_text())
    rubric=deepcopy(baseline);rubric['prompts']['PRM-16'].update(version='2',file='PRM-16_v2.txt')
    protocol=dict(schema_version=1,code_and_prompt_hash=fingerprint(),dataset_hash=digest(records),
        baseline=baseline,candidate=candidate,rubric=rubric,pre_registered_cases=PARTITIONS,
        absolute_judges=['DeepSeek-V4.1-Flash','Kimi-K2.6'],pair_judge='DeepSeek-V4.1-Flash',
        score='arithmetic mean of five integer axes; raw overall retained; never compared to old v1 judge scale',
        limits=dict(gateway_calls=48,additional_tokens=180000,shared_daily_tokens=3000000,call_wall_seconds=120),
        target=dict(median=4.5,meaning='aspirational, never passed to generator/judge'),
        test_policy='validation complete, then seal; test results used once, never for tuning',
        provenance='All new cases are synthetic. 60 variants in 10 story families, not 60 independent real observations.',audit=audit)
    out.mkdir(parents=True);save(out/'protocol.json',protocol);save(out/'cases.json',records)
    conn=sqlite3.connect(out/'budget.sqlite')
    conn.execute('create table allowance (id integer primary key, calls integer, tokens integer, reserved integer, stopped text)')
    conn.execute('insert into allowance values (1,0,0,0,null)');conn.commit();conn.close()
    if baseline_run:
        old=json.loads((baseline_run/'protocol.json').read_text())
        if old['dataset_hash']!=protocol['dataset_hash'] or old['baseline']!=baseline:raise ValueError('baseline reuse requires identical cases and baseline manifest')
        cases={r['case_id']:r for r in records}
        for cid in PILOT:
            source=baseline_run/'cards'/f'{cid}_baseline.json'
            row=json.loads(source.read_text())
            if row['case_hash']!=digest(cases[cid]['case']) or row['protocol_hash']!=digest(old) or row['status']!='evaluated':raise ValueError('unverified baseline reuse')
            for call in row['calls']:
                request=prepare_request(call['prompt_id'],{},manifest=baseline)
                if call['prompt_hash']!=request['prompt_hash']:raise ValueError('baseline prompt changed')
            row.update(original_protocol_hash=row['protocol_hash'],protocol_hash=digest(protocol),reused_from=str(source))
            save(out/'cards'/source.name,row)
    print(json.dumps(audit,ensure_ascii=False));print('Frozen:',out/'protocol.json')


def load(out):
    protocol=json.loads((out/'protocol.json').read_text());records=json.loads((out/'cases.json').read_text())
    if protocol['code_and_prompt_hash']!=fingerprint() or protocol['dataset_hash']!=digest(records):
        raise ValueError('frozen experiment changed; use a new experiment directory')
    audit_partitions(records)
    return protocol,{r['case_id']:r for r in records}


class Allowance:
    mode='evaluation'
    def __init__(self,out,limits):
        self.conn=sqlite3.connect(out/'budget.sqlite',timeout=30);self.limits=limits
        self.base=EvaluationGateway(max_tokens=limits['shared_daily_tokens'],max_call_seconds=limits['call_wall_seconds'])
    def chat(self,**kwargs):
        # UTF-8 bytes are a conservative text-token reservation, plus response cap.
        reserve=len(json.dumps(kwargs['messages'],ensure_ascii=False).encode())+kwargs['max_tokens']+512
        self.conn.execute('begin immediate')
        calls,tokens,reserved,stopped=self.conn.execute('select calls,tokens,reserved,stopped from allowance where id=1').fetchone()
        if stopped or calls>=self.limits['gateway_calls'] or tokens+reserved+reserve>self.limits['additional_tokens']:
            self.conn.rollback();raise RuntimeError('experiment allowance reached; no automatic increase')
        self.conn.execute('update allowance set calls=calls+1,reserved=reserved+? where id=1',(reserve,));self.conn.commit()
        try:result=self.base.chat(**kwargs)
        except Exception:
            self.conn.execute('update allowance set reserved=reserved-?,stopped=? where id=1',(reserve,'gateway error; pending usage may be unknown'))
            self.conn.commit();raise
        self.conn.execute('update allowance set reserved=reserved-?,tokens=tokens+? where id=1',(reserve,result.tokens_in+result.tokens_out));self.conn.commit()
        return result
    def close(self):self.base.close();self.conn.close()


def assert_access(out,protocol,split):
    if split=='test':
        seal=json.loads((out/'sealed.json').read_text()) if (out/'sealed.json').exists() else None
        if not seal or seal['protocol_hash']!=digest(protocol):raise ValueError('test locked; complete validation and seal first')
        for name,expected in seal['validation_hashes'].items():
            if hashlib.sha256((out/'scores'/name).read_bytes()).hexdigest()!=expected:raise ValueError('sealed validation results changed')


def generate(out,split,variant,case_id):
    protocol,records=load(out);assert_access(out,protocol,split)
    ids=[case_id] if case_id else protocol['pre_registered_cases'][split]
    if any(cid not in protocol['pre_registered_cases'][split] for cid in ids):raise ValueError('case outside pre-registered subset')
    gateway=Allowance(out,protocol['limits'])
    try:
        for cid in ids:
            path=out/'cards'/f'{cid}_{variant}.json'
            if path.exists():continue
            case=CaseInput.model_validate(records[cid]['case'])
            result=run_case(case,gateway,manifest=protocol[variant])
            result.pop('requests',None)
            result.update(case_id=cid,variant=variant,split=split,case_hash=digest(records[cid]['case']),protocol_hash=digest(protocol))
            save(path,result);print(cid,variant,result['status'],flush=True)
            if gateway.base.blocked:break
    finally:gateway.close()


AXES=('coverage','mechanisms','personalization','actions','grounding')


class PairDecision(StrictModel):
    winner: str=Field(pattern='^(A|B|tie)$')
    reasons: list[str]
    critical_errors_a: list[str]
    critical_errors_b: list[str]


def evaluate(out,split,case_id,judge,pairs=False):
    protocol,records=load(out);assert_access(out,protocol,split)
    if judge not in protocol['absolute_judges'] or family(judge)=='glm':raise ValueError('independent pre-registered judge required')
    if pairs and judge!=protocol['pair_judge']:raise ValueError('pair judge is pre-registered')
    ids=[case_id] if case_id else protocol['pre_registered_cases'][split]
    if any(cid not in protocol['pre_registered_cases'][split] for cid in ids):raise ValueError('case outside pre-registered subset')
    gateway=Allowance(out,protocol['limits'])
    try:
        for cid in ids:
            case=CaseInput.model_validate(records[cid]['case'])
            cards={v:json.loads((out/'cards'/f'{cid}_{v}.json').read_text()) for v in ('baseline','candidate')}
            # Refuse to give high-quality scores to failed/withheld generations.
            if any(r['status']!='evaluated' for r in cards.values()):
                save(out/'scores'/f'{cid}_{judge}_unavailable.json',dict(case_id=cid,split=split,judge=judge,status='generation_failed',protocol_hash=digest(protocol)))
                continue
            orders=[('A','B'),('B','A')] if pairs else [('baseline','candidate')]
            # Mapping is stable and hidden from the evaluator's messages.
            variants=['baseline','candidate'];random.Random(int(hashlib.sha256(cid.encode()).hexdigest(),16)).shuffle(variants)
            for index,order in enumerate(orders):
                if pairs:
                    path=out/'pairs'/f'{cid}_{judge}_{index}.json'
                    if path.exists():continue
                    mapping={'A':variants[index],'B':variants[1-index]}
                    payload=dict(A=cards[mapping['A']]['card'],B=cards[mapping['B']]['card'],ledger=case.ledger.model_dump(mode='json'))
                    row=dict(case_id=cid,split=split,judge=judge,order=index,mapping=mapping,status='error',protocol_hash=digest(protocol))
                    try:
                        result=gateway.chat(operation='PE_PAIR_V1',role=MODEL_ROLES[judge],messages=[dict(role='system',content=(PROMPT_DIR/'pairwise_v1.txt').read_text()),
                            dict(role='user',content=json.dumps(payload,ensure_ascii=False))],trace_id='pair_'+cid,max_tokens=4096,temperature=0,response_format={'type':'json_object'},request_options={'reasoning':{'effort':'low'}})
                        decision=PairDecision.model_validate_json(result.text)
                        row.update(status='scored',output=decision.model_dump(),winner_variant=mapping.get(decision.winner,'tie'),tokens_in=result.tokens_in,tokens_out=result.tokens_out,cache_hit=result.cache_hit)
                    except Exception as exc:row['error']=str(exc)
                    save(path,row);print('pair',cid,index,row['status'],flush=True)
                else:
                    for variant in variants:
                        path=out/'scores'/f'{cid}_{judge}_{variant}.json'
                        if path.exists():continue
                        row=dict(case_id=cid,variant=variant,split=split,judge=judge,status='error',protocol_hash=digest(protocol))
                        session=PromptSession(gateway,'score_'+cid,max_calls=2,manifest=protocol['rubric'],overrides={'PRM-16':judge})
                        try:
                            output=session.call('PRM-16',dict(card=cards[variant]['card'],ledger=case.ledger.model_dump(mode='json')))
                            row.update(status='scored',output=output.model_dump(),score=sum(getattr(output,k) for k in AXES)/len(AXES))
                        except Exception as exc:row['error']=str(exc)
                        row['calls']=session.records;save(path,row);print('score',cid,judge,variant,row['status'],flush=True)
                if gateway.base.blocked:return
    finally:gateway.close()


def seal(out):
    protocol,_=load(out)
    if (out/'sealed.json').exists():raise ValueError('already sealed')
    expected=protocol['pre_registered_cases']['validation']
    paths=[out/'scores'/f'{cid}_{j}_{v}.json' for cid in expected for j in protocol['absolute_judges'] for v in ('baseline','candidate')]
    if not all(p.exists() and json.loads(p.read_text())['status']=='scored' for p in paths):raise ValueError('complete pre-registered validation with both judges before opening test')
    save(out/'sealed.json',dict(protocol_hash=digest(protocol),validation_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        policy='Generator and rubric frozen; test consumed as final assessment, never a tuning signal'))


def report(out):
    protocol,records=load(out)
    scores=[json.loads(p.read_text()) for p in (out/'scores').glob('*.json')]
    cards=[json.loads(p.read_text()) for p in (out/'cards').glob('*.json')]
    pairs=[json.loads(p.read_text()) for p in (out/'pairs').glob('*.json')]
    summary=dict(protocol_hash=digest(protocol),dataset=protocol['audit'],scope=protocol['provenance'],production_promoted=False,
        test_opened=(out/'sealed.json').exists(),target_achieved_on_independent_real_data=False,partitions={})
    for split,ids in protocol['pre_registered_cases'].items():
        part={'expected_cases':len(ids),'judges':{},'generation':{},'paired':{}}
        for variant in ('baseline','candidate'):
            found=[r for r in cards if r['variant']==variant and r['split']==split]
            part['generation'][variant]=dict(attempted=len(found),accepted=sum(r['status']=='evaluated' for r in found),
                partial=sum(bool(r.get('partial')) for r in found),unknown_count=sum(len(r.get('unknowns',[])) for r in found))
        for judge in protocol['absolute_judges']:
            part['judges'][judge]={}
            for variant in ('baseline','candidate'):
                rows=[r for r in scores if r.get('variant')==variant and r['split']==split and r['judge']==judge]
                values=[r['score'] for r in rows if r['status']=='scored']
                part['judges'][judge][variant]=dict(scored=len(values),expected=len(ids),failed=sum(r['status']!='scored' for r in rows),
                    median=statistics.median(values) if values else None,mean=statistics.mean(values) if values else None,
                    share_ge_45=sum(v>=4.5 for v in values)/len(values) if values else None)
        for cid in ids:
            rows=[r for r in pairs if r['case_id']==cid and r['status']=='scored']
            votes=[r['winner_variant'] for r in rows]
            part['paired'][cid]=dict(completed_orders=len(rows),winner=votes[0] if len(votes)==2 and votes[0]==votes[1] else 'inconclusive')
        summary['partitions'][split]=part
    db=sqlite3.connect(out/'budget.sqlite');row=db.execute('select calls,tokens,reserved,stopped from allowance').fetchone();db.close()
    summary['budget']=dict(gateway_calls=row[0],measured_tokens=row[1],pending_reservation=row[2],stopped=row[3],limits=protocol['limits'],cost_rub=None)
    save(out/'summary.json',summary)
    lines=['# Пилот улучшения интерпретации','',protocol['provenance'],'',
        'Промпты и рубрика зафиксированы до вызовов. Старый балл 4.125 получен по другой рубрике и напрямую с новыми числами не сравнивается.',
        '', '| Набор | Судья | Версия | Оценено | Медиана | Среднее |','|---|---|---|---|---|---|']
    for split,part in summary['partitions'].items():
        for judge,variants in part['judges'].items():
            for variant,value in variants.items():lines.append(f"| {split} | {judge} | {variant} | {value['scored']}/{value['expected']} | {value['median']} | {value['mean']} |")
    lines+=['',f"Вызовов шлюза: {row[0]}; учтено токенов: {row[1]}; стоимость ₽ неизвестна.",'',
        'Закрытый тест открыт: '+str(summary['test_opened'])+'. Продвижения в production нет. Синтетический пилот не доказывает медиану 4.5 на реальных данных.',
        '', 'Покрытие, ошибки, оба порядка парного сравнения и результаты каждой версии сохранены в summary.json и файлах запуска. Оценки людей не имитируются.','']
    (out/'summary.md').write_text('\n'.join(lines));print(out/'summary.md')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','generate','evaluate','pair','seal','report'])
    p.add_argument('--out',type=Path,required=True);p.add_argument('--split',choices=list(PARTITIONS),default='dev')
    p.add_argument('--variant',choices=['baseline','candidate'],default='candidate');p.add_argument('--case-id')
    p.add_argument('--baseline-run',type=Path)
    p.add_argument('--candidate-profile',choices=['candidate_v1.json','candidate_v2.json'],default='candidate_v1.json')
    p.add_argument('--judge',choices=['DeepSeek-V4.1-Flash','Kimi-K2.6'],default='DeepSeek-V4.1-Flash');a=p.parse_args()
    if a.action=='prepare':prepare(a.out,a.baseline_run,a.candidate_profile)
    elif a.action=='generate':generate(a.out,a.split,a.variant,a.case_id)
    elif a.action in {'evaluate','pair'}:evaluate(a.out,a.split,a.case_id,a.judge,pairs=a.action=='pair')
    elif a.action=='seal':seal(a.out)
    else:report(a.out)


if __name__=='__main__':main()
