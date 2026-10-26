# -*- coding: utf-8 -*-
"""Книга формул FM-01..FM-24 (WP-D6) — чистые функции, БЕЗ обращения к LLM.

Каждая функция воспроизводит формулу из справочника (раздел 8.1) и проверяется
тестовыми векторами раздела 4.2 ТЗ (см. tests/test_vectors.py, допуск 0.001).
Если вектор не сошёлся — правим код или сообщаем BA об ошибке в формуле.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .config import FACTOR_ORDER


# --- FM-01..FM-05: профиль и концентрация (реализованы в metrics_f/g) -------
def fm01_is_large(revenue: float, threshold: float = 15e9) -> float:
    """is_large = 1[REVENUE >= 15 млрд]."""
    return float(revenue >= threshold) if pd.notna(revenue) else np.nan


def fm02_share(revenue_c: float, revenue_industry_total: float) -> float:
    return revenue_c / revenue_industry_total if revenue_industry_total else np.nan


def fm03_noisy_or(items: list[tuple[float, float]]) -> float:
    """own = 1 - Π(1 - w_k x_k); items = [(вес, значение), ...]."""
    known = [(w, x) for w, x in items if pd.notna(x)]
    if not known:
        return np.nan
    return 1.0 - math.prod(1.0 - w * x for w, x in known)


def fm05_hhi(shares) -> float:
    """HHI = Σ s_i²."""
    s = np.asarray([x for x in shares if pd.notna(x)], dtype=float)
    return float((s ** 2).sum()) if s.size else np.nan


# --- FM-06: релевантность ---------------------------------------------------
def fm06_relevance(eps: dict[str, float], iota: dict[str, float],
                   match: float = 0.0, w_cov: float = 0.7,
                   w_match: float = 0.3) -> tuple[float, float]:
    """Rel = 0.7·Cov + 0.3·Match; Cov = Σ ε_f·ι_f / Σ ι_f."""
    num = sum(eps.get(f, np.nan) * v for f, v in iota.items() if pd.notna(eps.get(f)))
    den = sum(v for v in iota.values())
    cov = num / den if den else np.nan
    rel = w_cov * cov + w_match * (match if pd.notna(match) else 0.0)
    return cov, rel


def match_score(m_sector: float = 0.0, m_region: float = 0.0,
                m_cpty: float = 0.0, m_country: float = 0.0) -> float:
    """Match = max по четырём совпадениям (событие без факторного вектора)."""
    return max(x for x in (m_sector, m_region, m_cpty, m_country) if pd.notna(x))


def m_cpty_overlap(iota_sectors: set[str], mix_cust: dict, mix_sup: dict,
                   threshold_turnover: float = 0.3) -> float:
    """m_cpty = min(1, Σ_{s∈sectors} (mix_cust[s] + mix_sup[s]) / 0.3)."""
    s_sum = sum((mix_cust.get(s) or 0.0) + (mix_sup.get(s) or 0.0)
                for s in iota_sectors)
    return min(1.0, s_sum / threshold_turnover)


# --- FM-07: интерес роли -----------------------------------------------------
def fm07_pers(persona_weights: dict[str, float], iota: dict[str, float]) -> float:
    """Pers = Σ π_{p,f}·ι_f / Σ ι_f."""
    num = sum(persona_weights.get(f, 0.0) * v for f, v in iota.items())
    den = sum(v for v in iota.values())
    return num / den if den else np.nan


# --- FM-08..FM-12: признаки события ------------------------------------------
def fm08_fresh(age_days: float, half_life_days: float) -> float:
    """Fresh = 0.5^(age / h); будущее событие (age<0) не затухает."""
    if pd.isna(age_days):
        return np.nan
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def fm09_urgency(t_decision: datetime | None, as_of: datetime,
                 tau_days: float = 7, window_days: int = 30) -> float:
    """Urg = exp(-max(0, t_decision - now)/7); дальше 30 дней -> 0."""
    if t_decision is None:
        return 0.0
    dt = (t_decision - as_of).total_seconds() / 86400.0
    if dt <= 0 or dt > window_days:
        return 0.0
    return math.exp(-dt / tau_days)


def fm10_credibility(tier_weight: float, n_independent: int,
                     indep_bonus: float = 0.1) -> float:
    """Cred = min(1, w_tier + 0.1·(n_indep - 1))."""
    return min(1.0, tier_weight + indep_bonus * max(0, n_independent - 1))


def fm11_novelty(sim_to_shown: float) -> float:
    """Nov = 1 - max sim к уже показанным за окно; без истории -> 1."""
    return 1.0 - sim_to_shown if pd.notna(sim_to_shown) else 1.0


def fm12_impact(fact: float | None, expectation: float | None,
                sigma_hist: float | None, severity_1_5: int | None = None) -> float:
    """S = |факт - ожидание|/σ; Imp = 1 - exp(-S/s0). Запасной вариант по sev."""
    if fact is not None and expectation is not None and sigma_hist not in (None, 0):
        s = abs(fact - expectation) / sigma_hist
        return 1.0 - math.exp(-s)
    if severity_1_5 is not None:
        return (severity_1_5 - 1) / 4.0
    return np.nan


# --- FM-13: итоговый скор и отбор --------------------------------------------
def fm13_score(fresh: float, rel: float, imp: float, urg: float, nov: float,
               cred: float, pers: float,
               weights: dict[str, float]) -> float:
    """Score = Fresh · [0.40·Rel + 0.25·Imp + 0.10·Urg + 0.10·Nov + 0.15·Cred]
    · (0.5 + 0.5·Pers)."""
    inner = (weights["Rel"] * rel + weights["Imp"] * imp + weights["Urg"] * urg
             + weights["Nov"] * nov + weights["Cred"] * cred)
    return fresh * inner * (0.5 + 0.5 * pers)


def fm13_filter_and_topk(events: list[dict], weights: dict[str, float],
                         rel_threshold: float, top_k: int = 5,
                         mmr_lambda: float = 0.7,
                         sim_fn=None) -> list[dict]:
    """Порог Rel -> скор -> жадный MMR-отбор топ-5 (MMR: 5 одинаковых — нельзя).

    events: [{event_id, fresh, rel, imp, urg, nov, cred, pers, [text для sim]}]
    sim_fn(a, b) -> похожесть 0..1; без неё MMR вырождается в обычный top-k.
    """
    passed = [e for e in events if e["rel"] >= rel_threshold]
    for e in passed:
        e["score"] = fm13_score(e["fresh"], e["rel"], e["imp"], e["urg"],
                                e["nov"], e["cred"], e["pers"], weights)
    selected: list[dict] = []
    while passed and len(selected) < top_k:
        if not selected:
            best = max(passed, key=lambda e: e["score"])
        else:
            def mmr_key(e):
                max_sim = max((sim_fn(e, s) if sim_fn else 0.0) for s in selected)
                return mmr_lambda * e["score"] - (1 - mmr_lambda) * max_sim
            best = max(passed, key=mmr_key)
        selected.append(best)
        passed.remove(best)
    return selected


# --- FM-14: знак эффекта ------------------------------------------------------
def fm14_sign_effect(rate_net: float, d_rate: float = 1.0,
                     threshold: float = 0.15) -> dict:
    """Для RATE знак нельзя «угадать»: при |rate_net| < порога — две ветви."""
    if pd.isna(rate_net):
        return {"effect": np.nan, "branches": ["negative", "positive"],
                "reason": "нет данных о долг/депозит позиции"}
    if rate_net <= -threshold:
        return {"effect": -d_rate, "branches": ["negative"], "reason": "нетто-заёмщик"}
    if rate_net >= threshold:
        return {"effect": +d_rate, "branches": ["positive"], "reason": "нетто-вкладчик"}
    return {"effect": np.nan, "branches": ["negative", "positive"],
            "reason": "|rate_net| ниже порога — обе ветви, уточнить у пользователя"}


# --- FM-15: сценарные калькуляторы (числа считаем кодом, не LLM!) -------------
def fm15_interest_expense(floating_debt: float, delta_rate_pp: float) -> float:
    """(а) ΔПроцентные расходы = D·Δr (Δr в п.п.)."""
    return floating_debt * (delta_rate_pp / 100.0)


def fm15_fx_result(value_currency: float, s0: float, s1: float,
                   k: int) -> float:
    """(б) ΔВалютный результат = V·(S1 - S0)·k; k=+1 выручка, -1 платежи."""
    return value_currency * (s1 - s0) * k


def fm15_cogs(cogs: float, pass_through: float, delta_infl_pp: float) -> float:
    """(в) ΔСебестоимость = COGS·π_пасс·Δπ."""
    return cogs * pass_through * (delta_infl_pp / 100.0)


# --- FM-16: версии позиции банка ----------------------------------------------
def fm16_versions(records: list[dict],
                  delta_by_indicator: dict[str, float]) -> dict[str, Any]:
    """records: [{indicator, horizon, value, as_of}]. Активна запись с max as_of;
    конфликт, если |Δ| между версиями >= порога показателя."""
    out: dict[str, Any] = {"active": None, "superseded": [],
                           "conflict": False, "delta": 0.0}
    if not records:
        return out
    def _key(r):
        return datetime.strptime(str(r["as_of"]), "%d.%m.%Y")
    recs = sorted(records, key=_key)
    latest = recs[-1]
    out["active"] = latest
    out["superseded"] = recs[:-1]
    if len(recs) > 1:
        delta = abs(float(latest["value"]) - float(recs[-2]["value"]))
        thr = delta_by_indicator.get(latest["indicator"], 0.0)
        out["delta"] = delta
        out["conflict"] = bool(delta >= thr and thr > 0)
    return out


# --- FM-17: дедупликация -------------------------------------------------------
def fm17_exact_key(url: str | None, body_hash: str | None) -> str:
    """Точный дубликат: канонический URL или хэш текста."""
    return hashlib.sha256(f"{url}|{body_hash}".encode()).hexdigest()


def _shingles(text: str, k: int = 5) -> set[str]:
    t = re.sub(r"\s+", " ", str(text).lower()).strip()
    if len(t) < k:
        return {t} if t else set()
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def fm17_jaccard(text_a: str, text_b: str, k: int = 5) -> float:
    """Почти-точные дубликаты: Jaccard по шинглам, порог 0.8."""
    a, b = _shingles(text_a, k), _shingles(text_b, k)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def fm17_tf_cosine(text_a: str, text_b: str) -> float:
    """Косинус TF-словарей (замена эмбеддингов, пока OQ-01 не закрыт)."""
    toks_a = re.findall(r"\w+", str(text_a).lower())
    toks_b = re.findall(r"\w+", str(text_b).lower())
    if not toks_a or not toks_b:
        return 0.0
    fa, fb = {}, {}
    for t in toks_a:
        fa[t] = fa.get(t, 0) + 1
    for t in toks_b:
        fb[t] = fb.get(t, 0) + 1
    dot = sum(v * fb.get(t, 0) for t, v in fa.items())
    na = math.sqrt(sum(v * v for v in fa.values()))
    nb = math.sqrt(sum(v * v for v in fb.values()))
    return dot / (na * nb) if na and nb else 0.0


def fm17_is_duplicate(text_a: str, text_b: str, dt_hours: float | None,
                      shared_entities: int, cfg: dict) -> tuple[bool, str]:
    """Решение о дубле: шинглы >= 0.8, либо TF-IDF >= 0.6 при >= 2 общих
    сущностях и |Δt| <= 48 ч."""
    if fm17_jaccard(text_a, text_b) >= cfg["jaccard_shingles"]:
        return True, "jaccard_shingles"
    cos = fm17_tf_cosine(text_a, text_b)
    if cos >= cfg["tfidf_cosine"]:
        dt_ok = dt_hours is None or abs(dt_hours) <= cfg["dt_hours"]
        ent_ok = shared_entities >= cfg["min_shared_entities"]
        if dt_ok and ent_ok:
            return True, "tfidf+entities+time"
    return False, ""


# --- FM-20: уверенность вывода --------------------------------------------------
def fm20_confidence(cred_agg: float, completeness: float,
                    critic_score: float, cfg: dict) -> tuple[float, str]:
    """conf = min(Cred, 0.5 + 0.5·completeness, critic); уровни high/mid/low."""
    conf = min(cred_agg, 0.5 + 0.5 * completeness, critic_score)
    level = "high" if conf >= cfg["high"] else ("mid" if conf >= cfg["mid"] else "low")
    return conf, level


# --- FM-21..FM-24: графовые формулы ---------------------------------------------
def fm21_v_rate(e01: float, pctl_rev: float | None) -> float:
    """Индекс уязвимости к ставке: V = E-01·(0.5+0.5·pctl); без выручки 0.75."""
    if pd.isna(e01):
        return np.nan
    mult = 0.75 if pctl_rev is None or pd.isna(pctl_rev) else 0.5 + 0.5 * pctl_rev
    return e01 * mult


def fm22_shock_propagation(edges: dict[str, list[tuple[str, float]]],
                           x: dict[str, float], gamma: float = 0.5,
                           depth: int = 2) -> dict[str, float]:
    """P_k(c) = min(1, Σ_j w_cj·[x_j + γ·P_{k-1}(j)]).

    edges: {клиент: [(поставщик, доля в платежах 0..1), ...]}
    x: {узел: поражение события 0..1}.
    """
    p = {u: 0.0 for u in set(edges) | set(x)}
    for _ in range(depth):
        nxt = dict(p)
        for c, suppliers in edges.items():
            s = 0.0
            for j, w in suppliers:
                s += w * (x.get(j, 0.0) + gamma * p.get(j, 0.0))
            nxt[c] = min(1.0, s)
        p = nxt
    return p


def fm23_group_spread(x_c: float, group_max_x: float, delta: float = 0.5) -> float:
    """x_group = max(x_c, δ·max по группе)."""
    return max(x_c, delta * group_max_x)


def fm24_linear_score(industry_match: float, pctl_rev: float,
                      pctl_workers: float, product_relevance: float,
                      supply_chain_risk: float,
                      weights: dict[str, float]) -> tuple[float, float]:
    """Линейная базовая линия: ScaleFactor = 0.5·pctl_rev + 0.5·pctl_workers."""
    scale = 0.5 * pctl_rev + 0.5 * pctl_workers
    score = (weights["IndustryMatch"] * industry_match
             + weights["ScaleFactor"] * scale
             + weights["ProductRelevance"] * product_relevance
             + weights["SupplyChainRisk"] * supply_chain_risk)
    return scale, score


# --- Вспомогательное: вектор события из event_types.csv --------------------------
def event_vector(primary: str, secondary: list[str]) -> dict[str, float]:
    """ι: первичный фактор = 1.0, вторичные = 0.5 (соглашение справочника 5.4)."""
    vec = {f: 0.0 for f in FACTOR_ORDER}
    if primary in vec:
        vec[primary] = 1.0
    for f in secondary:
        if f in vec:
            vec[f] = max(vec[f], 0.5)
    return vec
