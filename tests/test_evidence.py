from copy import deepcopy

import pytest
from pydantic import ValidationError

from pe.evidence import EvidenceLedger, check_card_provenance
from pe.schemas import InsightCard


@pytest.fixture
def ledger_data():
    # Unit-test text only; this fixture is not a benchmark observation.
    text = "Тестовая новость о спросе."
    return dict(
        trace_id="t1", client_id="client-1", event_id="event-1",
        documents=[dict(id="news-1", kind="news", text=text)],
        entries=[
            dict(id="N1", kind="NEWS_FACT", source=dict(
                document_id="news-1", start=0, end=len(text), quote=text)),
            dict(id="C1", kind="COMPANY_FEATURE", client_id="client-1",
                 feature_id="F-03", value="SP-01", source_record_id="row-1"),
            dict(id="D1", kind="COMPUTED_VALUE", formula_id="FM-06", value=0.4,
                 input_ids=["C1", "N1"], calculation_ref="run-1/FM-06"),
        ],
    )


@pytest.fixture
def card_data():
    return dict(
        trace_id="t1", confirmed_facts=[dict(id="f1", text="Тестовый факт", source_ids=["N1"])],
        bank_view_status="missing", bank_view_items=[],
        model_inference=dict(impact_chain=[dict(id="i1", text="Тестовый вывод", source_ids=["D1"])],
                             risks=[], opportunities=[], actions_or_questions=[], confidence=0.5),
        provenance_labels={"f1": "fact", "i1": "ai_inference"},
        relevance=0.4, disclaimer="Тестовый пример", offer=None,
    )


def test_resolves_quote_and_calculation_without_certifying_truth(ledger_data, card_data):
    report = check_card_provenance(
        InsightCard.model_validate(card_data), EvidenceLedger.model_validate(ledger_data))
    assert report.ok
    assert report.semantic_support == "not_checked"


@pytest.mark.parametrize("failure", ["invented_quote", "foreign_client", "cycle", "missing_input", "duplicate"])
def test_invalid_ledger_is_rejected(ledger_data, failure):
    if failure == "invented_quote":
        ledger_data["entries"][0]["source"]["quote"] = "Несуществующий фрагмент"
    elif failure == "foreign_client":
        ledger_data["entries"][1]["client_id"] = "client-2"
    elif failure == "cycle":
        ledger_data["entries"][2]["input_ids"] = ["D1"]
    elif failure == "missing_input":
        ledger_data["entries"][2]["input_ids"] = ["unknown"]
    else:
        ledger_data["entries"].append(deepcopy(ledger_data["entries"][0]))
    with pytest.raises(ValidationError):
        EvidenceLedger.model_validate(ledger_data)


@pytest.mark.parametrize("failure", ["missing_ref", "bank_from_news", "trace", "mock_input"])
def test_card_provenance_failures(ledger_data, card_data, failure):
    if failure == "missing_ref":
        card_data["confirmed_facts"][0]["source_ids"] = ["unknown"]
    elif failure == "bank_from_news":
        card_data["bank_view_status"] = "available"
        card_data["bank_view_items"] = [dict(id="b1", text="Банк считает", source_ids=["N1"])]
        card_data["provenance_labels"]["b1"] = "bank_view"
    elif failure == "trace":
        card_data["trace_id"] = "another-trace"
    else:
        # Mock evidence is also detected through the computed D1 entry.
        ledger_data["documents"][0]["is_mock"] = True
        card_data["confirmed_facts"] = []
        del card_data["provenance_labels"]["f1"]
    report = check_card_provenance(
        InsightCard.model_validate(card_data), EvidenceLedger.model_validate(ledger_data))
    assert not report.ok
    assert report.issues


def test_bank_view_needs_document_date_and_page(ledger_data):
    ledger_data["documents"][0]["kind"] = "bank"
    ledger_data["entries"][0]["kind"] = "BANK_VIEW"
    with pytest.raises(ValidationError):
        EvidenceLedger.model_validate(ledger_data)
    ledger_data["documents"][0].update(page=2, as_of="2026-10-25")
    assert EvidenceLedger.model_validate(ledger_data).documents[0].page == 2
