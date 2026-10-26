import pytest
from pydantic import ValidationError

from pe.schemas import EventExtraction, InsightCard


def test_event_rejects_mismatched_factor_keys():
    data = dict(event_type="ET-01", entities=[], facts=[], iota={"RATE": 0.5},
                direction={}, severity=3, geo_scope="national", horizon="short", rationale="text")
    with pytest.raises(ValidationError):
        EventExtraction.model_validate(data)


def test_card_requires_claim_provenance():
    data = dict(confirmed_facts=[dict(id="f1", text="fact", source_ids=["news-1"])],
                bank_view_status="missing", bank_view_items=[],
                model_inference=dict(impact_chain=[], risks=[], opportunities=[],
                                     actions_or_questions=[], confidence=0.5),
                provenance_labels={}, relevance=0.3, disclaimer="Предварительная оценка",
                trace_id="trace-1")
    with pytest.raises(ValidationError):
        InsightCard.model_validate(data)
    data["provenance_labels"] = {"f1": "fact"}
    assert InsightCard.model_validate(data).trace_id == "trace-1"
