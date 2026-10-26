"""Assemble measured PE evidence; never fill missing expert/pricing results."""
from pathlib import Path
import argparse
import csv
import json
import statistics
import random
import hashlib
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pe.seed_cases import golden_cases,task_payloads
from pe.prompts import prepare_request
from pe.schemas import InsightCard
from pe.integration import backend_payload, quality_trace


def rows(root,phase):
    return [json.loads(p.read_text()) for p in sorted((root/phase).glob('*.json'))]


def rate(values): return sum(values)/len(values) if values else None
def mean(values): return statistics.mean(values) if values else None
def show(value):return 'не измерено' if value is None else f'{value:.3f}'


def bootstrap(values):
    if not values:return None
    rng=random.Random(20261025)
    samples=sorted(statistics.mean(rng.choices(values,k=len(values))) for _ in range(2000))
    return [samples[49],samples[1949]]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--telemetry-run',type=Path,action='append',default=[],
        help='Earlier checkpoints with original uncached token measurements (quality results stay in --run)')
    parser.add_argument('--phase-run',action='append',default=[],metavar='PHASE=PATH',
        help='Use an explicitly named historical phase without rerunning its models')
    args=parser.parse_args();run=args.run
    destination=ROOT/'docs/pe/evaluation';destination.mkdir(parents=True,exist_ok=True)
    sources={phase:run for phase in ('matrix','cards','critics','critic-controls','attacks','judge')}
    for value in args.phase_run:
        phase,path=value.split('=',1)
        if phase not in sources:parser.error('unknown phase: '+phase)
        sources[phase]=Path(path)
    phases={phase:rows(path,phase) for phase,path in sources.items()}
    retained=('case_id','task','status','signature','original_signature','revalidation','reused_from',
        'output','card','critic','partial','repairs','issues','error','retryable','defect','caught',
        'claim_support_rate','generator','card_gate_status','calls')
    with (destination/'case_results.jsonl').open('w') as stream:
        for phase,items in phases.items():
            for row in items:
                evidence={key:row[key] for key in retained if key in row}
                evidence.update(phase=phase,evidence_source=str(sources[phase]))
                stream.write(json.dumps(evidence,ensure_ascii=False)+'\n')
    matrix={f'PRM-{n:02}': [r for r in phases['matrix'] if r['task']==f'PRM-{n:02}'] for n in range(1,18)}
    manifest=json.loads((ROOT/'prompts/manifest.json').read_text())
    models={}
    for model in ('GLM-5.3-Flash','DeepSeek-V4.1-Flash'):
        cards=[r for r in phases['cards'] if r['task']==model]
        claims=[c for row in cards for c in (row.get('critic') or {}).get('claims',[])]
        fact_claims=[c for c in claims if c['layer']=='fact']
        judge=[r['output']['overall'] for r in phases['judge'] if r.get('generator')==model and r['status']=='passed']
        models[model]=dict(cards_tested=len(cards),cards_accepted=sum(r['status']=='evaluated' for r in cards),
            partial_cards=sum(bool(r.get('partial')) for r in cards),
            faithfulness_critic_proxy=rate([c['verdict']=='supported' for c in claims]),
            q29_unsupported_critic_proxy=rate([c['verdict']!='supported' for c in claims]),
            fact_error_critic_proxy=rate([c['verdict'] in {'unsupported','contradicted','outdated'} for c in fact_claims]),
            q08_model_judge=mean(judge),q08_judge_bootstrap_95ci=bootstrap(judge),
            acceptance_bootstrap_95ci=bootstrap([r['status']=='evaluated' for r in cards]),
            q08_judged_cards=len(judge),q08_expert=None,cost_rub=None)
    critics={}
    for model in ('Qwen3.6 35B A3B','Kimi-K2.6'):
        defects=[r for r in phases['critics'] if r['task']==model]
        controls=[r for r in phases['critic-controls'] if r['task']==model]
        critics[model]=dict(defects_tested=len(defects),defects_found=sum(bool(r.get('caught')) for r in defects),
            controls_tested=len(controls),controls_claim_support=mean([r['claim_support_rate'] for r in controls if 'claim_support_rate' in r]))
    report=dict(date='2026-10-26',synthetic_seed_clients=True,
        evidence_sources={phase:str(path) for phase,path in sources.items()},
        revalidated_matrix_answers=sum(bool(r.get('revalidation')) for r in phases['matrix']),
        active_prompt_versions={pid:spec['version'] for pid,spec in manifest['prompts'].items()},
        matrix_matches_active_versions=all(c.get('version')==manifest['prompts'][r['task']]['version']
            for r in phases['matrix'] for c in r.get('calls',[]) if c['prompt_id']==r['task']),
        matrix={pid:dict(tested=len(items),passed=sum(r['status']=='passed' for r in items)) for pid,items in matrix.items()},
        generators=models,critics=critics,
        attacks=dict(tested=len(phases['attacks']),passed=sum(r['status']=='passed' for r in phases['attacks'])),
        external_acceptance=dict(expert_q08='pending',real_client_end_to_end='pending',rub_tariffs='not_provided',
            daily_400_rub_capacity='not_computable_without_tariffs',vlm_human_number_review='pending; assistant review found errors, raw VLM quarantined'),
        failures=[dict(phase=phase,task=r['task'],case_id=r['case_id'],status=r['status'],
            defect=r.get('defect'),issues=r.get('issues',[]),error=r.get('error')) for phase,items in phases.items() for r in items if r['status'] not in {'passed','evaluated'}])
    all_calls=[dict(call,case_id=r['case_id'],phase=phase) for phase,items in phases.items() for r in items for call in r.get('calls',[])]
    budget_calls=list(all_calls)
    for prior in args.telemetry_run:
        for phase in phases:
            for row in rows(prior,phase):budget_calls.extend(row.get('calls',[]))
    active={(c['prompt_id'],c.get('version'),c.get('requested_model')) for c in all_calls}
    seen=set();unique=[]
    for call in budget_calls:
        key=(call['prompt_id'],call.get('version'),call.get('requested_model'))
        identity=(*key,call.get('prompt_hash'),call.get('response_hash'))
        if key not in active or call.get('cache_hit') is not False or not call.get('tokens_in') or identity in seen:continue
        seen.add(identity);unique.append(call)
    budget_calls=all_calls+unique
    groups={}
    for call in budget_calls:
        key=(call['prompt_id'],call.get('version'),call.get('requested_model'))
        groups.setdefault(key,[]).append(call)
    recovered=[]
    columns=['prompt_id','version','model','tokens_in','tokens_out','price_in_rub_per_1k','price_out_rub_per_1k',
             'calls_per_day','cost_rub_per_day','measured_at','status','observed_calls','uncached_calls','latency_p50_ms']
    with (ROOT/'prompts/token_budget.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for (pid,version,model),items in sorted(groups.items()):
            fresh=[];seen_group=set()
            for call in items:
                key=(call.get('prompt_hash'),call.get('response_hash'))
                if call.get('cache_hit') is not False or not call.get('tokens_in') or key in seen_group:continue
                fresh.append(call);seen_group.add(key)
            # Earlier extraction answers may only have zero-token cache traces.
            # Recover counts only when the exact current HTTP request hash matches.
            dbpath=ROOT/'.local/experiments/gateway.db'
            if not fresh and pid=='PRM-01' and dbpath.exists():
                settings=json.loads((ROOT/'config/organiser_models.json').read_text())
                with sqlite3.connect(f'file:{dbpath}?mode=ro',uri=True) as conn:
                    for row in matrix[pid]:
                        request=prepare_request(pid,task_payloads(golden_cases()[row['case_id']])[pid],validate_input=True)
                        for call in row['calls']:
                            if call['prompt_id']!=pid or call.get('prompt_hash')!=request['prompt_hash']:continue
                            payload=dict(model=settings['models'][model],messages=request['messages'],
                                max_tokens=call['max_tokens'],temperature=0.0,response_format=request['response_format'])
                            digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                            found=conn.execute("SELECT tokens_in,tokens_out FROM llm_calls WHERE request_hash=? AND cache_hit=0 AND status='ok' AND tokens_in>0 ORDER BY call_id LIMIT 1",(digest,)).fetchone()
                            if found:
                                fresh.append(dict(call,tokens_in=found[0],tokens_out=found[1],elapsed_ms=None))
                                recovered.append(dict(prompt_id=pid,case_id=row['case_id'],request_hash=digest,source='shared DB exact request hash'))
            measured=fresh
            writer.writerow(dict(prompt_id=pid,version=version,model=model,
                tokens_in=round(mean([c['tokens_in'] for c in measured])) if measured else '',
                tokens_out=round(mean([c['tokens_out'] for c in measured])) if measured else '',
                measured_at='2026-10-26',status='measured_tokens_prices_unknown' if fresh else 'cache_only_no_original_token_measurement',
                observed_calls=sum(c["prompt_id"]==pid and c.get("version")==version and c.get("requested_model")==model for c in all_calls),uncached_calls=len(fresh),latency_p50_ms=round(statistics.median(c['elapsed_ms'] for c in fresh if c.get('elapsed_ms') is not None)) if any(c.get('elapsed_ms') is not None for c in fresh) else ''))
    report['calls_observed']=len(all_calls)
    report['recovered_token_measurements']=recovered
    report['token_measurement_runs']=[str(run),*[str(p) for p in args.telemetry_run]]
    report['uncached_calls_observed']=sum(c.get('cache_hit') is False for c in all_calls)
    for model,item in models.items():
        timings=[c['elapsed_ms'] for c in unique if c.get('requested_model')==model and c['prompt_id'] in {'PRM-05','PRM-06'}]
        item['generation_call_latency_p50_ms']=statistics.median(timings) if timings else None
        item['generation_latency_observations']=len(timings)
    for model,item in critics.items():
        timings=[c['elapsed_ms'] for c in unique if c.get('requested_model')==model and c['prompt_id']=='PRM-07']
        item['critic_call_latency_p50_ms']=statistics.median(timings) if timings else None
        item['critic_latency_observations']=len(timings)
    cases=golden_cases()
    classifications=[r for r in phases['matrix'] if r['task']=='PRM-01' and r.get('output')]
    report['n01_seed_accuracy']=dict(tested=len(classifications),
        correct=sum(r['output']['event_type']==cases[r['case_id']].event.event_type for r in classifications))
    paired={model:{r['case_id']:r['output']['overall'] for r in phases['judge']
        if r.get('generator')==model and r['status']=='passed'} for model in models}
    shared=sorted(set.intersection(*(set(values) for values in paired.values())))
    delta=[paired['GLM-5.3-Flash'][gs]-paired['DeepSeek-V4.1-Flash'][gs] for gs in shared]
    report['paired_q08_glm_minus_deepseek']=dict(cases=len(shared),mean=mean(delta),bootstrap_95ci=bootstrap(delta))
    (destination/'acceptance_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# Приёмка части Prompt Engineer','',
        'Дата отчёта: 26.10.2026; вызовы 25–26.10.2026. Результаты ниже относятся к синтетическим seed-клиентам команды и документам поставленного корпуса. Экспертная приёмка реальных клиентских рекомендаций фиксируется отдельно.','',
        f"Сохранённых успешных ответов повторно проверено текущими схемами и правилами: {report['revalidated_matrix_answers']}. Исходные ответы, версии, хэши и телеметрия сохранены; это не новые API-вызовы. Источники фаз указаны в JSON.", '',
        '## Все 17 промптов','', '| Промпт | Проверено случаев | Прошло автоматические проверки |','|---|---|---|']
    for pid,item in report['matrix'].items():lines.append(f"| {pid} | {item['tested']} / 3 | {item['passed']} |")
    lines+=['','Прохождение схемы/автоматических проверок не подтверждает семантическую точность VLM. Все сырые PRM-03 требуют сверки источника.', '', '## Сравнение генераторов','','| Модель | Карточек проверено | Принято после проверок | Поддержка по критику, proxy Q-01 | Ошибки фактов по критику, proxy Q-03 | Q-08, LLM-судья |','|---|---|---|---|---|---|']
    for model,item in models.items():lines.append(f"| {model} | {item['cards_tested']} / 12 | {item['cards_accepted']} | {show(item['faithfulness_critic_proxy'])} | {show(item['fact_error_critic_proxy'])} | {show(item['q08_model_judge'])} ({item['q08_judged_cards']} карточек) |")
    lines+=['',f"N-01 на трёх seed-новостях: {report['n01_seed_accuracy']['correct']} / {report['n01_seed_accuracy']['tested']}. Это проверка примеров, не оценка на всём распределении событий.",
        '', 'Частичные карточки и 95% интервалы бутстрэпа по случаям указаны в JSON. Парная разность Q-08 (GLM минус DeepSeek): '+show(mean(delta))+f'; 95% CI: {bootstrap(delta)}; пар: {len(shared)}. На 12 синтетических случаях интервалы не доказывают перенос на реальные данные.']
    lines+=['','Q-01/Q-03 здесь — оценки независимого критика, не заменяющие ручной аудит. Q-08 приведена только как оценка разрешённой модели другого семейства; экспертная оценка не подставляется. Отсутствующий результат не приравнивается к успешному.','',
        '## Критик и безопасность','','| Критик | Найдено внесённых ошибок | Корректных контролей | Поддержка claims контролей |','|---|---|---|---|']
    for model,item in critics.items():lines.append(f"| {model} | {item['defects_found']} / {item['defects_tested']} (цель ≥8/10) | {item['controls_tested']} / 3 | {show(item['controls_claim_support'])} |")
    lines += ['',f"Разные атаки: {report['attacks']['passed']} безопасных обработок из {report['attacks']['tested']} запущенных; полный набор — 20. Это измерение на фиксированном наборе, а не доказательство безопасности во всех условиях.",'',
        '## Непрошедшие случаи','', '| Проверка | Случай | Результат |','|---|---|---|']
    for failed in report['failures']:
        detail=(failed['error'] or '; '.join(failed['issues']) or failed.get('defect') or failed['status']).splitlines()[0]
        lines.append(f"| {failed['phase']} / {failed['task']} | {failed['case_id']} | {detail.replace('|','/')} |")
    lines += ['', 'Неполученные оценки не подставляются как ноль или успех: таблица содержит фактическое число оценок. Расход зависшего вызова не подтверждён. Непрошедший диалог не разрешён к публикации.', '',
        '## Что требует внешнего подтверждения','',
        '- Экспертный аудит Q-08 и фактов на реальных карточках; шаблон выгрузки создаётся рядом с результатами.',
        '- Сквозной прогон с реальными выгрузками DA и работающим API backend; адаптеры и тесты интерфейса входят в пакет.',
        '- Подтверждённые рублёвые тарифы для cost_card и суточной ёмкости при цели 400 ₽. Серверный лимит 500 ₽ действует, но не заменяет этот расчёт.',
        '- Ручная сверка ключевых чисел VLM. [Сверка Codex](vlm_review.md) нашла ошибки атрибуции и периода: сырые VLM-страницы блокируются до проверки источника.',
        '', '## Файлы','',
        '- `acceptance_results.json` — все численные результаты и незавершённые пункты.',
        '- `case_results.jsonl` — ответы и результаты каждого синтетического случая без HTTP-сообщений и секретов.',
        '- `../../../prompts/token_budget.csv` — измеренные токены по промптам; тарифы и неизвестная стоимость оставлены пустыми.',
        '- Исходные ответы и trace находятся в локальном каталоге запуска, исключённом из Git.', '']
    (destination/'acceptance_summary.md').write_text('\n'.join(lines))
    cases=golden_cases()
    export=run/'handover';export.mkdir(parents=True,exist_ok=True)
    review=[]
    for row in phases['cards']:
        if row.get('status')!='evaluated' or not row.get('card'):continue
        card=InsightCard.model_validate(row['card']);case=cases[row['case_id']]
        key=row['task']+'_'+row['case_id']
        (export/(key+'_backend.json')).write_text(json.dumps(backend_payload(card,case),ensure_ascii=False,indent=2))
        trace=quality_trace(card,case.ledger,synthetic=True)
        (export/(key+'_trace.json')).write_text(json.dumps(trace,ensure_ascii=False,indent=2))
        for claim in trace['insight']['claims']:
            review.append(dict(case_id=row['case_id'],generator=row['task'],claim_id=claim['id'],text=claim['text'],
                layer=claim['layer'],source_ids=';'.join(claim['source_ids']),expert_supported='',expert_q08='',notes=''))
    with (export/'expert_review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['case_id','generator','claim_id','text','layer','source_ids','expert_supported','expert_q08','notes'])
        writer.writeheader();writer.writerows(review)
    print(destination/'acceptance_summary.md')
    print('Matrix complete:',all(item['tested']==3 and item['passed']==3 for item in report['matrix'].values()))
    print('Failures:',len(report['failures']))


if __name__=='__main__':main()
