"""Write visibly synthetic replay fixtures; no model or DA computation runs here."""
import json
from pathlib import Path


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    root = Path(__file__).resolve().parent / "demo"
    news = "Учебное событие: регулятор повысил ключевую ставку."
    event = dict(event_type="ET-01", entities=["учебный регулятор"], facts=[],
                 iota={"RATE": 1.0}, direction={"RATE": 1}, severity=3,
                 geo_scope="unknown", horizon="unknown", rationale="Синтетический учебный пример")
    ledger = dict(
        trace_id="demo_trace", client_id="demo_client", event_id="demo_event",
        documents=[dict(id="demo_news", kind="news", text=news, is_mock=True)],
        entries=[
            dict(id="N1", kind="NEWS_FACT", source=dict(document_id="demo_news", start=0, end=len(news), quote=news)),
            dict(id="C1", kind="COMPANY_FEATURE", client_id="demo_client", feature_id="F-08",
                 value="Кредитный продукт отмечен как действующий; размер и тип ставки неизвестны",
                 source_record_id="synthetic_row"),
            dict(id="D_REL", kind="COMPUTED_VALUE", formula_id="FM-06", value=0.5,
                 input_ids=["C1", "N1"], calculation_ref="synthetic_not_executed/FM-06"),
            dict(id="D_CONF", kind="COMPUTED_VALUE", formula_id="FM-20", value=0.5,
                 input_ids=["C1", "N1"], calculation_ref="synthetic_not_executed/FM-20"),
            dict(id="D_SIGN", kind="COMPUTED_VALUE", formula_id="FM-14", value="negative",
                 input_ids=["C1", "N1"], calculation_ref="synthetic_not_executed/FM-14"),
        ],
    )
    core = dict(
        confirmed_facts=[dict(id="core_fact", text=news, source_ids=["N1"])],
        bank_view_items=[], common_effects=[], unknowns=["Позиция банка в примере отсутствует"])
    overlay = dict(
        why_you=[dict(id="personal_why", text="В учебном профиле отмечен кредитный продукт; событие может затрагивать условия финансирования.", source_ids=["N1", "C1"])],
        impact_chain=[dict(id="personal_impact", factor="RATE", effect="negative",
                           text="Если стоимость кредита зависит от ключевой ставки, её рост может увеличить расходы; условия договора неизвестны.",
                           source_ids=["N1", "C1", "D_SIGN"])],
        risks=[], opportunities=[], actions_or_questions=[dict(
            id="personal_question", text="Стоит проверить тип ставки и дату её пересмотра в кредитном договоре.", source_ids=["C1", "N1"])],
        unknowns=["Размер долга, вид ставки и сроки пересмотра неизвестны"])
    all_claims = ([(c, "fact") for c in core["confirmed_facts"]]
                  + [(c, "ai_inference") for c in overlay["why_you"] + overlay["impact_chain"] + overlay["actions_or_questions"]])
    critic = dict(
        claims=[dict(id=c["id"], text=c["text"], layer=layer, verdict="supported", evidence_ids=c["source_ids"])
                for c, layer in all_claims],
        numbers_check=[], coverage_6_questions={k: True for k in (
            "what_happened", "why_you", "implications", "actions", "bank_view", "basis")},
        overall="pass", fix_instructions=[])
    write(root / "case.json", dict(ledger=ledger, event=event, persona="PER-03", bank_view_status="missing",
                                  relevance_id="D_REL", confidence_id="D_CONF", effect_source_ids={"RATE": "D_SIGN"}))
    for name, data in (("PRM-05", core), ("PRM-06", overlay), ("PRM-07", critic)):
        write(root / "responses" / f"{name}.json", data)
    print(root)


if __name__ == "__main__":
    main()
