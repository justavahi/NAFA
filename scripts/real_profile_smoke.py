"""One bounded A/B smoke test with participant aggregates and provided bank corpus.

It evaluates interpretation of a supplied publication, not independent truth of
its economic forecasts, and is not a generalization/holdout quality claim.
"""
import argparse
import ast
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(ROOT))
from pe.improvement import Allowance,AXES,digest,fingerprint
from pe.participant import products_text
from pe.pipeline import CaseInput,run_case
from pe.session import PromptSession


def prepare(out):
    if out.exists():raise ValueError('new output directory required')
    import pandas as pd
    source=ROOT/'.local/da_snapshot/da_scripts';sys.path.insert(0,str(source))
    from analytics import formulas as F
    data=ROOT/'.local/participant_da_safe'
    frame=pd.read_csv(data/'client_features.csv',dtype={'SYNTHETIC_U':'string'})
    eps=pd.read_csv(data/'exposures.csv',dtype={'SYNTHETIC_U':'string'})
    frame=frame.merge(eps,on='SYNTHETIC_U',validate='one_to_one')
    ambiguous=set(json.loads((data/'ambiguous_ids.json').read_text()));frame=frame[~frame.SYNTHETIC_U.isin(ambiguous)].copy()
    raw_dir=ROOT/'.local/participant_data'
    sep=ast.literal_eval(json.loads((raw_dir/'validation.json').read_text())['df_Uid_data_hack.csv']['separator'])
    grouping=pd.read_csv(raw_dir/'df_Uid_data_hack.csv',sep=sep,dtype='string',keep_default_na=False,usecols=['SYNTHETIC_U','GROUP_ID','MAINOKVED_NAME'])
    grouping=grouping[~grouping.SYNTHETIC_U.isin(ambiguous)]
    frame=frame.merge(grouping,on='SYNTHETIC_U',validate='one_to_one')
    frame['selection_key']=frame.SYNTHETIC_U.map(lambda v:hashlib.sha256(('participant-smoke-v1:'+str(v)).encode()).hexdigest())
    frame=frame.sort_values('selection_key')
    predicates={'borrower':frame.E15_rate_net<=-.15,'depositor':frame.E15_rate_net>=.15,
                'mixed':(frame.E15_rate_net.abs()<.15)&(frame.F08_LOA==1)&(frame.F08_DEP==1)}
    page=json.loads((ROOT/'reference/corpus/DOC-07_p1.json').read_text())
    quote=next(b['text'] for b in page['blocks'] if '13,5' in b['text'] or '13.5' in b['text'])
    # This is an attributed fact of publication, not an independently verified CBR announcement.
    news='В предоставленном отчёте Альфа-Банка от 11.09.2026, стр. 1, сказано: '+quote
    with (ROOT/'reference/bank_view_manual.csv').open(encoding='utf-8-sig') as stream:
        bank=[r for r in csv.DictReader(stream) if r['doc_id']=='DOC-07' and r['indicator']=='key_rate']
    cases={};mapping={};groups=set()
    for index,(segment,mask) in enumerate(predicates.items(),1):
        candidates=frame[mask]
        row=next(r for _,r in candidates.iterrows() if str(r.GROUP_ID) not in groups)
        groups.add(str(row.GROUP_ID));alias=f'PARTICIPANT_{index:02}';mapping[alias]=str(row.SYNTHETIC_U)
        def feature(eid,fid,value):return dict(id=eid,kind='COMPANY_FEATURE',client_id=alias,feature_id=fid,value=value,source_record_id=f'participant_da:{alias}:{fid}')
        def scalar(v):return None if pd.isna(v) else str(v)
        products={k:None if pd.isna(row['F08_'+k]) else float(row['F08_'+k]) for k in ('RKO','GAR','LOA','LEA','DEP','ZPP','FACTORING')}
        entries=[dict(id='N1',kind='NEWS_FACT',source=dict(document_id='PUBLICATION',start=0,end=len(news),quote=news)),
            feature('C_PRODUCTS','F-08',products_text(products)),feature('C_SECTOR','F-03','Отраслевой профиль DA: '+str(row.F03_sector_profile)+'; основной вид деятельности в выгрузке: '+str(row.MAINOKVED_NAME)),
            feature('C_TYPE','F-10',scalar(row.F10_cl_type)),feature('C_COMPLETE','F-13',float(row.F13_completeness)),
            feature('C_NET','E-15',float(row.E15_rate_net)),feature('C_EXPOSURE','E-14',float(row.eps_RATE)),
            feature('C_LIMITS','F-13','Не заданы суммы обязательств, доля плавающей ставки, сроки и графики договоров. Единицы выручки не подтверждены, выручка исключена. Неизвестный CL_TYPE не заменяется догадкой.')]
        docs=[dict(id='PUBLICATION',kind='news',text=news,as_of='2026-09-11',is_mock=False)]
        for i,r in enumerate(bank,1):
            text=r['quote_span'];did=f'BANK_{i}'
            docs.append(dict(id=did,kind='bank',text=text,as_of=r['as_of'],page=int(r['page'])))
            entries.append(dict(id=f'B{i}',kind='BANK_VIEW',source=dict(document_id=did,start=0,end=len(text),quote=text),number=r['value'],unit=r['unit']))
        _,rel=F.fm06_relevance({'RATE':float(row.eps_RATE)},{'RATE':1.0},match=0.0)
        signed=F.fm14_sign_effect(float(row.E15_rate_net),d_rate=1.0)
        effect='two_branches' if pd.isna(signed['effect']) else 'negative' if signed['effect']<0 else 'positive'
        # Provisional critic prior bounds confidence before actual criticism.
        conf,_=F.fm20_confidence(.85,float(row.F13_completeness),.5,dict(high=.75,mid=.5))
        for eid,fid,value,inputs in [('D_REL','FM-06',float(rel),['N1','C_EXPOSURE']),('D_CONF','FM-20',float(conf),['N1','C_COMPLETE','C_LIMITS']),('D_RATE','FM-14',effect,['N1','C_NET','C_PRODUCTS'])]:
            entries.append(dict(id=eid,kind='COMPUTED_VALUE',formula_id=fid,value=value,input_ids=inputs,calculation_ref=f'pinned_DA/{fid}; study policy: forecast_direction=+1, cred=.85, precritic=.5; amounts unknown'))
        case=CaseInput.model_validate(dict(ledger=dict(trace_id='real_smoke_'+alias,client_id=alias,event_id='DOC07_FORECAST_REVISION',documents=docs,entries=entries),
            event=dict(event_type='ET-02',entities=[],facts=[],iota={'RATE':1.0},direction={'RATE':1},severity=3,geo_scope='national',horizon='конец 2026 года',
                rationale='Пересмотр прогноза банка в сторону более высокой ставки; не факт повышения текущей ставки. Последствия условны.'),
            persona='PER-03',bank_view_status='available',relevance_id='D_REL',confidence_id='D_CONF',effect_source_ids={'RATE':'D_RATE'}))
        cases[alias]=dict(segment=segment,case=case.model_dump(mode='json'))
    baseline=json.loads((ROOT/'prompts/profiles/baseline_v1.json').read_text());candidate=json.loads((ROOT/'prompts/profiles/candidate_v2.json').read_text())
    rubric=deepcopy(baseline);rubric['prompts']['PRM-16'].update(file='PRM-16_v2.txt',version='2')
    protocol=dict(case_hash=digest(cases),code_hash=fingerprint(),runner_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        baseline=baseline,candidate=candidate,rubric=rubric,selected_case='PARTICIPANT_01',
        limits=dict(gateway_calls=16,additional_tokens=80000,shared_daily_tokens=3000000,call_wall_seconds=180),
        inputs='Real provided participant aggregates + attributed publication from case bank corpus; not independent confirmation of forecast. Only borrower chosen for this smoke before any scores.',
        confidence_policy='FM20: declared cred=.85, provisional critic prior=.5; not calibrated business probability.',production_promoted=False)
    out.mkdir(parents=True);(out/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
    (out/'private_id_mapping.json').write_text(json.dumps(mapping,indent=2)+'\n');(out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n')
    db=sqlite3.connect(out/'budget.sqlite');db.execute('create table allowance (id integer primary key,calls integer,tokens integer,reserved integer,stopped text)');db.execute('insert into allowance values(1,0,0,0,null)');db.commit();db.close()
    print('Prepared 3 disjoint-group participant profiles; registered borrower-only live smoke. Raw IDs remain local.')


def run(out):
    protocol=json.loads((out/'protocol.json').read_text());cases=json.loads((out/'cases.json').read_text())
    if protocol['code_hash']!=fingerprint() or protocol['case_hash']!=digest(cases) or protocol['runner_hash']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('frozen smoke changed')
    alias=protocol['selected_case'];case=CaseInput.model_validate(cases[alias]['case']);gateway=Allowance(out,protocol['limits'])
    summary=dict(case_id=alias,scope=protocol['inputs'],independent_cases=1,production_promoted=False,variants={},scores={})
    try:
        for variant in ('baseline','candidate'):
            path=out/f'{variant}.json'
            if path.exists():result=json.loads(path.read_text())
            else:
                result=run_case(case,gateway,manifest=protocol[variant]);result.pop('requests',None)
                path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            summary['variants'][variant]=dict(status=result['status'],partial=result.get('partial',False),issues=result['issues'])
            print(variant,result['status'],flush=True)
            if gateway.base.blocked:break
        if len(summary['variants'])==2 and all(v['status']=='evaluated' for v in summary['variants'].values()):
            for variant in ('baseline','candidate'):
                path=out/f'{variant}_score.json'
                if path.exists():score=json.loads(path.read_text())
                else:
                    card=json.loads((out/f'{variant}.json').read_text())['card']
                    session=PromptSession(gateway,'real_score_'+variant,max_calls=2,manifest=protocol['rubric'],overrides={'PRM-16':'DeepSeek-V4.1-Flash'})
                    try:
                        output=session.call('PRM-16',dict(card=card,ledger=case.ledger.model_dump(mode='json')))
                        score=dict(status='scored',axes=output.model_dump(),mean=sum(getattr(output,k) for k in AXES)/5,calls=session.records)
                    except Exception as exc:score=dict(status='error',error=str(exc),calls=session.records)
                    path.write_text(json.dumps(score,ensure_ascii=False,indent=2)+'\n')
                summary['scores'][variant]=score
                print('score',variant,score.get('mean'),flush=True)
                if gateway.base.blocked:break
    finally:
        summary['budget']=dict(zip(('id','calls','tokens','reserved','stopped'),gateway.conn.execute('select * from allowance').fetchone()))
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');gateway.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run']);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    prepare(a.out) if a.action=='prepare' else run(a.out)
