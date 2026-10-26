import math

import pytest

from pe.metrics import audit_quality, group_bootstrap_interval, weighted_binary_log_loss


def test_hand_computed_metric():
    report = audit_quality([True, True, False, False], [True, False, True, False])
    assert report["balanced_accuracy"] == 0.5
    assert report["critical_false_accept_rate"] == 0.5
    assert report["audit_score_v1"] == 0


def test_perfect_and_constant_predictors():
    truth = [True, False]
    assert audit_quality(truth, truth)["audit_score_v1"] == 1
    assert audit_quality(truth, [False, False])["audit_score_v1"] == 0.5
    assert audit_quality(truth, [True, True])["audit_score_v1"] == 0
    assert audit_quality(truth, [None, None])["audit_score_v1"] == 0


def test_missing_response_is_not_a_correct_rejection():
    report = audit_quality([True, False], [True, None])
    assert report["accuracy"] == 0.5
    assert report["coverage"] == 0.5
    assert report["tn"] == 0
    assert audit_quality([True], [True])["audit_score_v1"] is None


def test_loss_matches_hand_computed_example():
    assert weighted_binary_log_loss([True, False], [0.5, 0.5]) == pytest.approx(math.log(2))
    unsafe = weighted_binary_log_loss([True, False], [0.9, 0.9])
    cautious = weighted_binary_log_loss([True, False], [0.1, 0.1])
    assert unsafe > cautious
    assert math.isfinite(weighted_binary_log_loss([True, False], [1, 0]))


@pytest.mark.parametrize("values", [[float('nan'), 0.5], [1.1, 0.5], [-0.1, 0.5]])
def test_loss_rejects_invalid_probabilities(values):
    with pytest.raises(ValueError):
        weighted_binary_log_loss([True, False], values)


def test_group_bootstrap_preserves_perfect_paired_events():
    result = group_bootstrap_interval([True, False, True, False], [True, False, True, False],
                                      ["event-1", "event-1", "event-2", "event-2"], repeats=30)
    assert result == {"accuracy": [1.0, 1.0], "audit_score_v1": [1.0, 1.0]}


@pytest.mark.parametrize("truth,pred", [([], []), ([True], []), ([1], [True]), ([True], [float('nan')])])
def test_metric_rejects_invalid_inputs(truth, pred):
    with pytest.raises(ValueError):
        audit_quality(truth, pred)
