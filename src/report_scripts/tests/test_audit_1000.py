import pytest

from pe.audit_cases import make_cases
from pe.benchmark import rule_audit, summarize

CASES = make_cases()


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_rule_audit_1000(case):
    assert rule_audit(case["input"]) is case["expected_accept"]


def test_related_events_do_not_leak_across_splits():
    memberships = {}
    for case in CASES:
        memberships.setdefault(case["group_id"], set()).add(case["split"])
    assert all(len(splits) == 1 for splits in memberships.values())
    assert len(CASES) == len({case["id"] for case in CASES}) == 1000
    assert sum(case["expected_accept"] for case in CASES) == 500
    assert make_cases() == CASES


def test_report_does_not_credit_missing_answers():
    report = summarize(CASES, {}, {"engine": "not_run"})
    assert report["metrics"]["missing"] == 1000
    assert report["metrics"]["accuracy"] == 0
    assert len(report["failures"]) == 1000


def test_report_rejects_duplicate_cases():
    with pytest.raises(ValueError, match="duplicate"):
        summarize([CASES[0], CASES[0]], {}, {})
