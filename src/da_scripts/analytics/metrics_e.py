# -*- coding: utf-8 -*-
"""Экспозиции E-01..E-15 — вектор чувствительности клиента к 12 факторам
(WP-D5, P0: E-01..E-03, E-13, E-14, E-15).

Ключевая идея (справочник 8.1, FM-04): вектор экспозиций — смесь трёх
компонент с весами alpha = (own, prior, chain) = (0.4, 0.4, 0.2):
    own   — собственная экспозиция клиента (из продуктов/платежей),
    prior — отраслевой prior = значение матрицы SP x фактор / 3,
    chain — экспозиция через цепочку поставок = Σ_s mix_sup[s] * prior_f(s)/3.
Компоненты, которые нечем посчитать, ВЫКИДЫВАЮТСЯ, а веса перенормируются:
    eps = Σ(alpha_k * c_k) / Σ(alpha_k) по доступным.
Работает при пропусках (R05): нет клиентских данных — берём prior отрасли.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import FACTOR_ORDER

# Какие own-компоненты у какого фактора (E-04..E-07, E-11 own не определён):
OWN_OF_FACTOR = {
    "RATE": "RATE",     # own = max(E-01, E-02) — правило из ТЗ
    "FX": "FX",         # E-03
    "LABOR": "LABOR",   # E-08
    "CREDIT": "CREDIT", # E-09
    "BUDGET": "BUDGET", # E-10
    "GEOPOL": "GEOPOL", # E-12
    "CPTY": "CPTY",     # E-13
    "INFL": None, "ENERGY": None, "COMMOD": None, "DEMAND": None, "REG": None,
}


# --- E-01: собственная процентная нагрузка (noisy-OR, FM-03) ---------------
def e01_own_rate_debt(products: pd.DataFrame, weights: dict) -> pd.Series:
    """own = 1 - Π(1 - w_k * x_k) по известным флагам; всё неизвестно -> NaN."""
    acc = pd.Series(1.0, index=products.index)
    known_any = pd.Series(False, index=products.index)
    for prod, w in weights.items():                     # LOA/LEA/FACTORING
        col = f"F08_{prod}" if f"F08_{prod}" in products.columns else prod
        if col not in products.columns:
            continue
        x = products[col]
        known_any = known_any | x.notna()
        acc = acc * (1 - w * x.fillna(0.0))
    return (1 - acc).where(known_any)


# --- E-02 ------------------------------------------------------------------
def e02_own_dep(products: pd.DataFrame, w: float) -> pd.Series:
    col = "F08_DEP" if "F08_DEP" in products.columns else "DEP"
    return (w * products[col]).where(products[col].notna())


# --- E-03 ------------------------------------------------------------------
def e03_own_fx(fts_flag: pd.Series, w: float) -> pd.Series:
    # «При отсутствии FTS_FLAG компонент пуст, а не ноль»
    return (w * fts_flag).where(fts_flag.notna())


# --- E-08 ------------------------------------------------------------------
def e08_own_labor(headcount: pd.Series, zpp: pd.Series,
                  cfg: dict, industry_median: float | pd.Series) -> pd.Series:
    if headcount.isna().all():
        return pd.Series(np.nan, index=headcount.index)
    med = industry_median if np.isscalar(industry_median) else industry_median.reindex(headcount.index)
    ratio = (headcount / med).clip(0, 1)
    zpp = zpp.fillna(0.0)
    return (ratio * (cfg["base"] + cfg["zpp_bonus"] * zpp)).clip(0, 1).where(headcount.notna())


# --- E-09 ------------------------------------------------------------------
def e09_own_credit(products: pd.DataFrame, growth: pd.Series, cfg: dict) -> pd.Series:
    col = lambda p: f"F08_{p}" if f"F08_{p}" in products.columns else p
    own = (cfg["growth_flag"] * growth.fillna(0.0)
           + cfg["LOA"] * products[col("LOA")].fillna(0.0)
           + cfg["LEA"] * products[col("LEA")].fillna(0.0)
           + cfg["FACTORING"] * products[col("FACTORING")].fillna(0.0))
    known = products[col("LOA")].notna() | products[col("LEA")].notna() | \
        products[col("FACTORING")].notna() | growth.notna()
    return own.clip(0, cfg.get("clip", 1.0)).where(known)


# --- E-10 ------------------------------------------------------------------
def e10_own_budget(products: pd.DataFrame, is_state: pd.Series,
                   prior_budget: pd.Series, cfg: dict) -> pd.Series:
    col = "F08_GAR" if "F08_GAR" in products.columns else "GAR"
    own = (cfg["GAR"] * products[col].fillna(0.0)
           + cfg["state_related"] * is_state.fillna(0.0)
           + cfg["prior_norm"] * (prior_budget / 3.0))
    known = products[col].notna() | is_state.notna()
    return own.clip(0, 1).where(known)


# --- E-12 ------------------------------------------------------------------
def e12_own_geopol(fts_flag: pd.Series, country: pd.Series, cfg: dict) -> pd.Series:
    """own_geopol = 0.8 * FTS_FLAG * risk_tier(страна).

    Доступна ТОЛЬКО если есть ВЭД-страна (FTS_FLAG=1 и страна известна):
    при FTS_FLAG=0 оценивать геополитический риск нечего — компонент пуст
    (это воспроизводит тестовый вектор CA-01: GEOPOL = prior-only = 0.333).
    Страна не из справочника -> tier по умолчанию moderate (допущение).
    """
    tiers = cfg["risk_tiers"]
    tiers_map = cfg.get("country_tier") or {}   # страна -> имя уровня
    default_tier = float(tiers.get("moderate", 0.4))
    tier = country.map(
        lambda c: float(tiers.get(str(tiers_map.get(str(c).upper(), "moderate")),
                                  default_tier))
        if pd.notna(c) else np.nan)
    own = cfg["fx_factor"] * fts_flag * tier
    return own.where((fts_flag == 1) & country.notna())


# --- E-13 ------------------------------------------------------------------
def e13_own_cpty(hhi_in: pd.Series, hhi_out: pd.Series,
                 n_cust: pd.Series, n_sup: pd.Series, cfg: dict) -> pd.Series:
    base = cfg["hhi_in"] * hhi_in.fillna(0.0) + cfg["hhi_out"] * hhi_out.fillna(0.0)
    n_total = (n_cust.fillna(0) + n_sup.fillna(0))
    thr = cfg["small_counterparty_threshold"]
    boost = 1 + cfg["small_boost_per_missing"] * (thr - n_total).clip(lower=0)
    own = (base * boost).clip(0, 1)
    return own.where(hhi_in.notna() | hhi_out.notna())


# --- E-14: вектор экспозиций -----------------------------------------------
def exposure_vector(own: dict[str, pd.Series], matrix: pd.DataFrame,
                    mix_sup: pd.DataFrame | None,
                    alpha: dict[str, float],
                    sp_of: pd.Series | None = None) -> pd.DataFrame:
    """Собирает eps_f(c) для 12 факторов.

    own      : {FACTOR: Series|null} — собственные компоненты (NaN = недоступна)
    matrix   : таблица SP x фактор (значения 0..3), индекс = SP-xx
    mix_sup  : DataFrame клиенты x SP — отраслевой микс ПОСТАВЩИКОВ (G-04)
    sp_of    : Series клиент -> SP-xx (профиль отрасли клиента, F-03)
    """
    a_own, a_prior, a_chain = alpha["own"], alpha["prior"], alpha["chain"]
    if matrix is None:
        raise ValueError("Матрица SP x фактор обязательна для eps")
    sp = matrix.set_index("sector_profile")
    sp = sp[~sp.index.duplicated(keep="first")]

    idx = None
    for s in own.values():
        if s is not None:
            idx = s.index
            break
    if idx is None and mix_sup is not None:
        idx = mix_sup.index
    if idx is None:
        raise ValueError("Не удалось определить список клиентов для eps")
    out = pd.DataFrame(index=idx)
    if sp_of is None:
        # отрасль неизвестна у всех — prior недоступен
        sp_of = pd.Series(pd.NA, index=idx, dtype="string")
    sp_of = sp_of.reindex(idx)
    known_sp = sp_of.notna() & sp_of.isin(sp.index)

    for f in FACTOR_ORDER:
        o = own.get(f)
        o = o.reindex(idx) if o is not None else None
        # prior клиента: значение матрицы его отрасли / 3
        prior_raw = sp_of.map((sp[f] / 3.0).to_dict() if f in sp.columns else {})
        prior_client = prior_raw.where(known_sp).astype(float)

        # chain: Σ_s mix_sup[s] * prior_f(s); доступен, если есть mix_sup
        if mix_sup is not None and f in sp.columns:
            ms = mix_sup
            if ms.index.duplicated().any():
                ms = ms[~ms.index.duplicated(keep="first")]
            chain = ms.reindex(columns=sp.index).fillna(0.0) @ (sp[f] / 3.0)
            chain = chain[~chain.index.duplicated(keep="first")].reindex(idx)
            chain_available = ms.reindex(idx).notna().any(axis=1)
        else:
            chain = pd.Series(np.nan, index=idx)
            chain_available = pd.Series(False, index=idx)

        s_num = pd.Series(0.0, index=idx)
        s_den = pd.Series(0.0, index=idx)
        if o is not None:
            ok = o.notna()
            s_num = s_num + a_own * o.fillna(0.0) * ok.astype(float)
            s_den = s_den + a_own * ok.astype(float)
        okp = prior_client.notna()
        s_num = s_num + a_prior * prior_client.fillna(0.0) * okp.astype(float)
        s_den = s_den + a_prior * okp.astype(float)
        okc = chain_available
        s_num = s_num + a_chain * chain.fillna(0.0) * okc.astype(float)
        s_den = s_den + a_chain * okc.astype(float)
        eps = (s_num / s_den).where(s_den > 0)
        out[f"eps_{f}"] = eps.clip(0, 1)
    return out


# --- E-15 ------------------------------------------------------------------
def e15_rate_net(e01: pd.Series, e02: pd.Series) -> pd.Series:
    """Чистая процентная позиция: >0 нетто-вкладчик, <0 нетто-заёмщик."""
    return (e02.fillna(0) - e01.fillna(0)).where(e01.notna() | e02.notna())


def build_own_components(features: pd.DataFrame, e01: pd.Series, e02: pd.Series,
                         e03: pd.Series, e08: pd.Series, e09: pd.Series,
                         e10: pd.Series, e12: pd.Series,
                         e13: pd.Series) -> dict[str, pd.Series | None]:
    """Собирает own-компоненты по факторам; RATE = max(E-01, E-02)."""
    rate = pd.concat([e01, e02], axis=1).max(axis=1).where(e01.notna() | e02.notna())
    return {
        "RATE": rate, "FX": e03, "INFL": None, "ENERGY": None, "COMMOD": None,
        "DEMAND": None, "LABOR": e08, "CREDIT": e09, "BUDGET": e10,
        "REG": None, "GEOPOL": e12, "CPTY": e13,
    }


def compute_exposures(features: pd.DataFrame, clients: pd.DataFrame,
                      matrix: pd.DataFrame | None, mix_sup: pd.DataFrame | None,
                      exposure_cfg: dict) -> dict[str, pd.Series | pd.DataFrame]:
    """Контур E-метрик целиком (WP-D5). Возвращает словарь Series/DataFrame."""
    ex = exposure_cfg
    e01 = e01_own_rate_debt(features, ex["e01_debt"])
    e02 = e02_own_dep(features, ex["e02_dep"])
    e03 = e03_own_fx(features["F09_fts_flag"], ex["e03_fx"])

    # отраслевая медиана численности для E-08
    hc = features["F05_headcount_mid"]
    med_by_sp = hc.groupby(features["F03_sector_profile"]).transform("median")
    e08 = e08_own_labor(hc, features["F08_ZPP"], ex["e08_labor"], med_by_sp)

    growth = features["F04_growth_flag"]
    e09 = e09_own_credit(features, growth, ex["e09_credit"])
    prior_budget = _prior_for(matrix, features["F03_sector_profile"], "BUDGET")
    e10 = e10_own_budget(features, features["F06_is_state_related"],
                         prior_budget, ex["e10_budget"])
    e12 = e12_own_geopol(features["F09_fts_flag"], features["F09_fts_country"],
                         ex["e12_geopol"])
    e13 = e13_own_cpty(
        features.get("G03_HHI_in", pd.Series(np.nan, index=features.index)),
        features.get("G03_HHI_out", pd.Series(np.nan, index=features.index)),
        features.get("G02_n_cust", pd.Series(np.nan, index=features.index)),
        features.get("G02_n_sup", pd.Series(np.nan, index=features.index)),
        ex["e13_cpty"])
    own = build_own_components(features, e01, e02, e03, e08, e09, e10, e12, e13)
    # mix_sup индексирован SYNTHETIC_U, features — RangeIndex: выравниваем
    if mix_sup is not None and "SYNTHETIC_U" in features.columns:
        ms = mix_sup[~mix_sup.index.duplicated(keep="first")]
        ms = ms.reindex(features["SYNTHETIC_U"].to_numpy())
        ms.index = features.index
        mix_sup = ms
    eps = (exposure_vector(own, matrix, mix_sup, ex["e14_alpha"],
                           sp_of=features["F03_sector_profile"])
           if matrix is not None else None)
    e15 = e15_rate_net(e01, e02)
    return {"e01": e01, "e02": e02, "e03": e03, "e08": e08, "e09": e09,
            "e10": e10, "e12": e12, "e13": e13, "e15_rate_net": e15, "eps": eps}


def _prior_for(matrix: pd.DataFrame | None, sp: pd.Series, factor: str) -> pd.Series:
    if matrix is None:
        return pd.Series(np.nan, index=sp.index)
    m = matrix.set_index("sector_profile")
    if factor not in m.columns:
        return pd.Series(np.nan, index=sp.index)
    return sp.map(m[factor])
