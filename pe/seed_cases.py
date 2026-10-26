"""Executable fixtures from the team's 16 seed cases, explicitly synthetic clients.

News summaries come from the BA seed, bank quotes from the pinned Backend CSV.
These fixtures do not replace expert annotation or the real participant CSV.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path

from .contracts import EventCore
from .pipeline import CaseInput, assemble_card
from .schemas import Claim, PersonalOverlay

ROOT = Path(__file__).resolve().parent.parent
EVENTS = {
    "EV-01": ("ET-01", "ЦБ сохранил ключевую ставку 14,0%. Риторика стала жёстче; вариант снижения не рассматривался.", "2026-09-11", {"RATE": 1., "CREDIT": .5, "INFL": .5}),
    "EV-02": ("ET-08", "Золото выросло на 9,7% в августе. Затем произошла коррекция после выступления главы ФРС.", "2026-09-01", {"COMMOD": 1., "FX": .5, "RATE": .5}),
    "EV-03": ("ET-03", "Инфляция 25–31 августа составила -0,01% за неделю и 6,32% год к году. Бензин подорожал на 0,91% за неделю.", "2026-09-02", {"INFL": 1., "ENERGY": .5, "RATE": .5}),
    "EV-04": ("ET-05", "Доходности ОФЗ выросли после паузы ЦБ. Минфин вернулся к размещению ОФЗ-ПД.", "2026-09-14", {"RATE": 1., "BUDGET": 1., "CREDIT": .5}),
    "EV-05": ("ET-14", "Эксперт РА подтвердил рейтинг Полюса ruAAA, прогноз стабильный. Связь эмитента с клиентом не установлена.", "2026-09-04", {"CPTY": 1., "CREDIT": .5}),
    "EV-06": ("ET-11", "Розничные продажи выросли на 5,3% год к году в июле. Продажи легковых автомобилей в августе снизились на 7%.", "2026-09-07", {"DEMAND": 1., "LABOR": .5}),
    "EV-07": ("ET-04", "Официальный курс на 20.10.2026: USD 82,85 руб., CNY 12,39 руб. Направление изменения в этом фрагменте не указано.", "2026-10-20", {"FX": 1., "GEOPOL": .5, "INFL": .5}),
    "EV-08": ("ET-20", "В Москве в ночь на среду ожидается температура ниже нуля. Связь с бизнесом клиента не установлена.", "2026-10-20", {}),
}


def csv_rows(name):
    with (ROOT / "reference" / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def golden_cases() -> dict[str, CaseInput]:
    archetypes = {row["archetype"]: row for row in csv_rows("synthetic_archetypes.csv")}
    bank_rows = csv_rows("bank_view_manual.csv")
    result = {}
    for seed in csv_rows("golden_set_seed.csv"):
        gs, client, event_id = seed["id"], seed["client"], seed["event"]
        if gs in {"GS-15", "GS-16"}: event_id = "EV-01"
        et, news, published, factors = EVENTS[event_id]
        profile = archetypes[client]
        entry = lambda id, kind, **kw: dict(id=id, kind=kind, **kw)
        documents = [dict(id="NEWS_" + event_id, kind="news", text=news, as_of=published, is_mock=True)]
        entries = [entry("N1", "NEWS_FACT", source=dict(document_id=documents[0]["id"], start=0, end=len(news), quote=news)),
                   entry("C1", "COMPANY_FEATURE", client_id=client, feature_id="F-08", value=profile["products"], source_record_id="reference/synthetic_archetypes.csv:" + client),
                   entry("C2", "COMPANY_FEATURE", client_id=client, feature_id="F-03", value=profile["sector_profile"], source_record_id="reference/synthetic_archetypes.csv:" + client),
                   entry("C3", "COMPANY_FEATURE", client_id=client, feature_id="F-13", value=profile["notes"], source_record_id="reference/synthetic_archetypes.csv:" + client)]
        bank_status = "available"
        if event_id == "EV-01": selected = [r for r in bank_rows if r["indicator"] == "key_rate" and r["doc_id"] == "DOC-07"]
        elif event_id == "EV-07": selected = [r for r in bank_rows if r["indicator"] == "fx_cny_rub" and r["doc_id"] == "DOC-07"]
        else:
            doc = {"EV-02":"DOC-06", "EV-03":"DOC-04", "EV-04":"DOC-08", "EV-05":"DOC-08", "EV-06":"DOC-03"}.get(event_id)
            selected = [r for r in bank_rows if r["doc_id"] == doc][:2]
            # Narrative events require the bank's actual reasoning, not an unrelated
            # numeric forecast merely because it comes from the same report.
            if event_id in {"EV-03","EV-06"}:
                page=json_load(ROOT/f"reference/corpus/{doc}_p1.json")
                blocks=page["blocks"][1:2] if event_id=="EV-03" else page["blocks"][:2]
                selected=[dict(quote_span=b["text"],doc_id=doc,page="1",as_of=page["as_of_date"]) for b in blocks]
        if gs == "GS-15":
            selected = [dict(quote_span="Отчёт по стали за апрель 2021 года. Историческая справка, не текущий прогноз.", doc_id="DOC-09", page="1", as_of="2021-04-15")]
            bank_status = "outdated"
        if gs == "GS-16":
            selected = [r for r in bank_rows if r["indicator"] == "key_rate" and r["horizon"] == "end_2026"]
        if not selected: bank_status = "missing"
        for i, row in enumerate(selected):
            did = f"{row['doc_id']}_p{row['page']}_{i}"
            text = row["quote_span"]
            documents.append(dict(id=did, kind="bank", text=text, as_of=row["as_of"], page=int(row["page"])))
            entries.append(entry(f"B{i+1}", "BANK_VIEW", source=dict(document_id=did, start=0, end=len(text), quote=text),
                                 number=row.get("value"),unit=row.get("unit")))
        rel = .2 if seed["expected_relevance"] in {"Low", "Drop"} else .7
        confidence = .4 if client == "CA-08" else .8
        entries += [entry("D_REL", "COMPUTED_VALUE", formula_id="FM-06", value=rel, input_ids=["C2", "N1"], calculation_ref="seed_fixture/FM-06"),
                    entry("D_CONF", "COMPUTED_VALUE", formula_id="FM-20", value=confidence, input_ids=["C3", "N1"], calculation_ref="seed_fixture/FM-20")]
        signs = {}
        for factor in factors:
            effect = "two_branches"
            if factor == "RATE": effect = "positive" if client in {"CA-03", "CA-07"} else "two_branches" if client in {"CA-02", "CA-08"} else "negative"
            elif factor in {"INFL", "ENERGY", "GEOPOL", "LABOR"}: effect = "negative"
            elif factor in {"DEMAND", "BUDGET", "CPTY"}: effect = "positive"
            elif factor == "CREDIT": effect = "negative" if client not in {"CA-03","CA-07","CA-08"} else "two_branches"
            elif factor == "COMMOD": effect = "positive" if client == "CA-03" else "two_branches"
            if event_id in {"EV-01","EV-07"} and factor in {"INFL","GEOPOL"}: effect = "two_branches"
            if event_id == "EV-03" and factor in {"INFL","RATE"}: effect = "two_branches"
            ref = "D_" + factor
            entries.append(entry(ref, "COMPUTED_VALUE", formula_id="FM-14", value=effect,
                                 input_ids=["C1", "C2", "N1"], calculation_ref="seed_fixture/effect_rule/" + factor))
            signs[factor] = ref
        result[gs] = CaseInput.model_validate(dict(
            ledger=dict(trace_id="seed_" + gs, client_id=client, event_id=event_id, documents=documents, entries=entries),
            event=dict(event_type=et, entities=[], facts=[], iota=factors,
                       direction={f:0 if event_id == "EV-07" else -1 if event_id == "EV-03" and f == "INFL" else 1 for f in factors},
                       severity=3, geo_scope="national", horizon="не указан", rationale="Исполняемый синтетический seed команды"),
            persona="PER-03", bank_view_status=bank_status, relevance_id="D_REL", confidence_id="D_CONF", effect_source_ids=signs))
    return result


def task_payloads(case: CaseInput) -> dict[str, dict]:
    ledger = case.ledger.model_dump(mode="json")
    event = case.event.model_dump(mode="json")
    news = next(d for d in ledger["documents"] if d["kind"] == "news")
    docs = {d["id"]:d for d in ledger["documents"]}
    bank_claims=[]
    for e in ledger["entries"]:
        if e["kind"]=="BANK_VIEW":
            doc=docs[e["source"]["document_id"]]
            bank_claims.append(Claim(id="bank_"+e["id"],text=f"Позиция банка на {doc['as_of']}, стр. {doc['page']}: {e['source']['quote']}",source_ids=[e["id"]]))
    core = EventCore(confirmed_facts=[Claim(id="fact", text=news["text"], source_ids=["N1"])],
                     bank_view_items=bank_claims, common_effects=[], unknowns=[] if bank_claims else ["Позиция банка отсутствует"])
    factor = next(iter(case.effect_source_ids), None)
    impacts = [] if factor is None else [dict(id="impact", text="Влияние зависит от подтверждённых условий компании.",
        source_ids=["N1", "C1", case.effect_source_ids[factor]], factor=factor, effect=case.computed()["effect_signs"][factor])]
    overlay = PersonalOverlay(why_you=[Claim(id="why", text="Профиль компании указан в исходном наборе; условия договоров требуют уточнения.", source_ids=["C1","C2"])],
        impact_chain=impacts, risks=[], opportunities=[], actions_or_questions=[Claim(id="question", text="Какие условия договоров определяют влияние события?", source_ids=["C1","N1"])], unknowns=[])
    # This fixture card is an input for critic/dialogue tests, not a model result.
    card = assemble_card(case, core, overlay).model_dump(mode="json")
    bank_doc = next((d for d in ledger["documents"] if d["kind"] == "bank"), None)
    chunk = dict(doc_id=bank_doc["id"] if bank_doc else "MISSING", page=bank_doc["page"] if bank_doc else 1,
                 title="Фрагмент корпуса", as_of_date=bank_doc["as_of"] if bank_doc else None,
                 blocks=[dict(block_type="text", text=bank_doc["text"], numbers=[], needs_manual_check=False)] if bank_doc else [], needs_manual_check=not bool(bank_doc))
    computed = case.computed()
    context = dict(chart=dict(id="CH-06", title="Экспозиции", takeaway="Сравнение чувствительности", is_demo=True),
                   offer_gate=dict(allowed=False), active_version="B1", graph=dict(depth=2, nodes=[case.ledger.client_id]))
    common = dict(ledger=ledger, computed=computed, context=context)
    payloads = {
        "PRM-01": dict(document=news, event_types=csv_rows("event_types.csv")),
        "PRM-02": dict(event=event, prototype=event["iota"]),
        "PRM-03": dict(doc_id=chunk["doc_id"], page=chunk["page"], image_description="Без изображения требуется отказ от извлечения"),
        "PRM-04": dict(chunks=[chunk]),
        "PRM-05": dict(event=event, entries=[e for e in ledger["entries"] if e["kind"] in {"NEWS_FACT","BANK_VIEW"}], documents=ledger["documents"], bank_view_status=case.bank_view_status),
        "PRM-06": dict(event_core=core.model_dump(mode="json"), ledger=ledger, persona=case.persona, computed=computed),
        "PRM-07": dict(card=card, impacts=impacts, ledger=ledger, computed=computed),
        "PRM-08": dict(card=card, ledger=ledger, question="Почему событие может быть важно компании и что нужно уточнить?", computed=computed),
        **{f"PRM-{i:02}": deepcopy(common) for i in range(9,16)},
        "PRM-16": dict(card=card, ledger=ledger), "PRM-17": deepcopy(common),
    }
    scenario=payloads["PRM-11"]
    scenario["ledger"]["entries"] += [dict(id="USER_SCENARIO",kind="COMPANY_FEATURE",client_id=case.ledger.client_id,
        feature_id="F-08",value="Сценарные вводные пользователя: 1000 млн руб. плавающего долга, рост ставки на 1 п.п.",source_record_id="seed_user_scenario"),
        dict(id="SCENARIO_RESULT",kind="COMPUTED_VALUE",formula_id="FM-15",value="10 млн руб. дополнительных процентных расходов",
             input_ids=["USER_SCENARIO"],calculation_ref="seed_fixture/1000*1/100")]
    scenario["computed"]["scenario_result"]="10 млн руб. дополнительных процентных расходов"
    scenario["context"]["scenario_source_id"]="SCENARIO_RESULT"
    chart=payloads["PRM-12"]
    metadata=json_load(ROOT/"reference/charts/CH-14.json")
    chart["context"]["chart"]=dict(metadata,is_demo=True)
    chart["ledger"]["entries"].append(dict(id="CHART_RESULT",kind="COMPUTED_VALUE",formula_id="FM-13",
        value=str(metadata),input_ids=["D_REL"],calculation_ref="reference/charts/CH-14.json"))
    versions=payloads["PRM-13"]
    selected=[r for r in csv_rows("bank_view_manual.csv") if r["indicator"]=="key_rate" and r["horizon"]=="end_2026"]
    for i,row in enumerate(selected):
        did="VERSION_DOC_"+str(i);text=row["quote_span"]
        versions["ledger"]["documents"].append(dict(id=did,kind="bank",text=text,as_of=row["as_of"],page=int(row["page"]),is_mock=False))
        versions["ledger"]["entries"].append(dict(id="V"+str(i),kind="BANK_VIEW",source=dict(document_id=did,start=0,end=len(text),quote=text),
                                                  number=row["value"],unit=row["unit"]))
    versions["context"]["versions"]=selected
    versions["computed"]["active_version_source_id"]="V"+str(max(range(len(selected)),key=lambda i:selected[i]["as_of"]))
    exposure=payloads["PRM-09"]
    exposure["ledger"]["entries"].append(dict(id="EXPOSURE_VECTOR",kind="COMPUTED_VALUE",formula_id="FM-04",
        value="Учебный вектор чувствительности: RATE=0.8, FX=0.4; own/prior/chain не разделены в этом входе",
        input_ids=["C1","C2"],calculation_ref="seed_fixture/vector"))
    exposure["computed"]["exposures"]={"RATE":.8,"FX":.4}
    offer=payloads["PRM-15"]
    offer["context"]["offer_gate"]=dict(allowed=True,product="Обсуждение условий кредитного финансирования",reason="Синтетический тест: хост подтвердил релевантность и допустимость расширения продукта")
    graph=payloads["PRM-17"]
    graph["ledger"]["entries"].append(dict(id="GRAPH_INPUT",kind="COMPANY_FEATURE",client_id=case.ledger.client_id,
        feature_id="G-04",value=f"Учебная агрегированная связь с поставщиками; глубина графа {graph['context']['graph']['depth']}; исходные компании не раскрыты",source_record_id="seed_graph"))
    graph["ledger"]["entries"].append(dict(id="GRAPH_RESULT",kind="COMPUTED_VALUE",formula_id="FM-22",value=.38,input_ids=["GRAPH_INPUT"],calculation_ref="seed_fixture/FM-22"))
    graph["computed"]["propagated_exposure"] = .38
    return payloads


def json_load(path):
    import json
    return json.loads(path.read_text())
