"""Versioned evaluation metric for claim auditing, separate from BIQ/card quality."""
from __future__ import annotations

import math
import random


def binary_report(expected: list[bool], predicted: list[bool | None]) -> dict:
    """Missing outputs are errors for their class and never count as correct refusals."""
    if not expected or len(expected) != len(predicted):
        raise ValueError("nonempty aligned label arrays required")
    if any(type(value) is not bool for value in expected):
        raise ValueError("expected labels must be bool")
    if any(value is not None and type(value) is not bool for value in predicted):
        raise ValueError("predictions must be bool or None")
    positives = sum(expected)
    negatives = len(expected) - positives
    tp = sum(t is True and p is True for t, p in zip(expected, predicted))
    tn = sum(t is False and p is False for t, p in zip(expected, predicted))
    fp = sum(t is False and p is True for t, p in zip(expected, predicted))
    fn = sum(t is True and p is False for t, p in zip(expected, predicted))
    tpr = tp / positives if positives else None
    tnr = tn / negatives if negatives else None
    return dict(n=len(expected), accuracy=(tp + tn) / len(expected),
                balanced_accuracy=(tpr + tnr) / 2 if tpr is not None and tnr is not None else None,
                coverage=sum(p is not None for p in predicted) / len(expected),
                critical_false_accept_rate=fp / negatives if negatives else None,
                false_reject_rate=(positives - tp) / positives if positives else None,
                tp=tp, tn=tn, fp=fp, fn=fn, missing=sum(p is None for p in predicted))


def audit_quality(expected: list[bool], predicted: list[bool | None]) -> dict:
    """NAFA Audit Score v1 = coverage * max(0, balanced_accuracy - false_accept_rate).

    Diagnostic metric for synthetic auditing only. Report all components too.
    Both classes are required; rejection/absence alone cannot maximize score.
    """
    result = binary_report(expected, predicted)
    ba, far = result["balanced_accuracy"], result["critical_false_accept_rate"]
    result["audit_score_v1"] = (result["coverage"] * max(0.0, ba - far)
                                 if ba is not None and far is not None else None)
    return result


def weighted_binary_log_loss(expected: list[bool], probabilities: list[float], *, false_accept_weight=3.0) -> float:
    """Training objective for an audit classifier; not a loss on generated text.

    Positive label = valid claim. Invalid claims have weight 3 by default.
    Probabilities are clipped to [1e-7,1-1e-7] for numerical stability.
    """
    if not expected or len(expected) != len(probabilities):
        raise ValueError("nonempty aligned arrays required")
    if not math.isfinite(false_accept_weight) or false_accept_weight <= 0:
        raise ValueError("positive finite class weight required")
    if any(type(t) is not bool for t in expected):
        raise ValueError("expected labels must be bool")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("probabilities must be finite and in [0,1]")
    numerator = denominator = 0.0
    for valid, prob in zip(expected, probabilities):
        prob = min(1 - 1e-7, max(1e-7, prob))
        weight = 1.0 if valid else false_accept_weight
        numerator -= weight * (math.log(prob) if valid else math.log1p(-prob))
        denominator += weight
    return numerator / denominator


def group_bootstrap_interval(expected: list[bool], predicted: list[bool | None], groups: list[str], *, repeats=300, seed=42) -> dict:
    """Percentile intervals resample base events, preserving their related variants."""
    if not expected or not len(expected) == len(predicted) == len(groups) or repeats < 20:
        raise ValueError("aligned nonempty arrays and at least 20 repeats required")
    indices = {}
    for i, group in enumerate(groups):
        indices.setdefault(group, []).append(i)
    keys, rng = sorted(indices), random.Random(seed)
    estimates = {"accuracy": [], "audit_score_v1": []}
    for _ in range(repeats):
        sample = [i for group in rng.choices(keys, k=len(keys)) for i in indices[group]]
        score = audit_quality([expected[i] for i in sample], [predicted[i] for i in sample])
        for metric in estimates:
            if score[metric] is not None:
                estimates[metric].append(score[metric])
    result = {}
    for metric, values in estimates.items():
        values.sort()
        result[metric] = ([values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]
                          if values else None)
    return result
