# -*- coding: utf-8 -*-
"""Метрики F-01..F-13 — признаки клиента (WP-D3, P0).

Все функции принимают clients DataFrame с каноническими колонками
(см. columns.CLIENT_COLUMNS) и возвращают DataFrame с тем же индексом.
Пропуск данных -> NaN (по правилу WP-D4: «пусто, а не ноль»).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

# ОКВЭД-дивизион -> буква секции A..U (общедоступная структура ОКВЭД-2)
SECTION_RANGES = [
    (1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"),
    (41, 43, "F"), (45, 47, "G"), (49, 53, "H"), (55, 56, "I"), (58, 63, "J"),
    (64, 66, "K"), (68, 68, "L"), (69, 75, "M"), (77, 82, "N"), (84, 84, "O"),
    (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"), (94, 96, "S"), (97, 98, "T"),
    (99, 99, "U"),
]
# Стадии, считающиеся "ростом/запуском" для growth_flag (словарь проверить в WP-D1)
GROWTH_STAGES = ("рост", "запуск", "стартап", "молод")
STATE_KEYWORDS = ("гос", "муницип", "федеральн", "state")


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _flag01(s: pd.Series) -> pd.Series:
    """Флаг 0/1 из '1/0, Y/N, ДА/НЕТ'; неизвестное значение -> NaN."""
    v = s.astype("string").str.strip().str.upper()
    out = pd.to_numeric(v, errors="coerce").map(lambda x: np.nan if pd.isna(x) else float(x))
    out = out.where(out.isin([0.0, 1.0]))
    mapped = v.map({"Y": 1.0, "YES": 1.0, "ДА": 1.0, "N": 0.0, "NO": 0.0, "НЕТ": 0.0})
    return out.fillna(mapped)


# --- F-01 ------------------------------------------------------------------
def f01_scale(df: pd.DataFrame, large_threshold: float = 15e9) -> pd.DataFrame:
    """is_large = 1[выручка >= 15 млрд]; rev_log10. Единицы проверить в WP-D1!"""
    rev = _to_num(df["REVENUE_COMPANY"])
    return pd.DataFrame({
        "F01_is_large": (rev >= large_threshold).astype(float).where(rev.notna()),
        "F01_rev_log10": np.log10(rev.where(rev > 0)),
    }, index=df.index)


# --- F-02 ------------------------------------------------------------------
def f02_position(df: pd.DataFrame) -> pd.DataFrame:
    """Доля в выборке по 2-значному ОКВЭД + перцентили внутри отрасли/региона.

    Внимание: это ДОЛЯ В ВЫБОРКЕ БАНКА, а не рыночная доля (паспорт метрики).
    """
    rev = _to_num(df["REVENUE_COMPANY"])
    okved2 = df["MAINOKVED_CODE"].astype("string").str.strip().str[:2]
    grp = rev.groupby(okved2)
    share_okved = rev / grp.transform("sum").replace(0, np.nan)
    pctl_ind = grp.rank(pct=True)
    pctl_reg = rev.groupby(df["REGION"]).rank(pct=True)
    return pd.DataFrame({
        "F02_share_okved": share_okved, "F02_pctl_industry": pctl_ind,
        "F02_pctl_region": pctl_reg, "F02_okved2": okved2,
    }, index=df.index)


# --- F-03 ------------------------------------------------------------------
def f03_sector(df: pd.DataFrame, okved_map: pd.DataFrame | None = None) -> pd.DataFrame:
    """Отрасль: дивизион, секция A..U, профиль SP-01..SP-15 по справочнику."""
    code = df["MAINOKVED_CODE"].astype("string").str.strip()
    div = pd.to_numeric(code.str[:2], errors="coerce")
    section = pd.Series(pd.NA, index=df.index, dtype="string")
    for lo, hi, letter in SECTION_RANGES:
        section = section.mask(div.between(lo, hi), letter)

    sp = pd.Series(pd.NA, index=df.index, dtype="string")
    if okved_map is not None:
        m = okved_map.copy()
        m["okved_division"] = pd.to_numeric(m["okved_division"], errors="coerce")
        lut = m.dropna().set_index("okved_division")["sector_profile"]
        sp = div.map(lut)
    return pd.DataFrame({"F03_okved_div": div, "F03_section": section,
                         "F03_sector_profile": sp}, index=df.index)


# --- F-04 ------------------------------------------------------------------
def f04_age_stage(df: pd.DataFrame,
                  growth_stages: tuple[str, ...] = GROWTH_STAGES) -> pd.DataFrame:
    """Возраст (градация), стадия ECO_MARK_LABEL, growth_flag для роста/запуска."""
    stage = df["ECO_MARK_LABEL"].astype("string").str.strip()
    low = stage.str.lower().fillna("")
    growth = pd.Series(np.nan, index=df.index)
    growth = growth.mask(low.eq(""), np.nan)
    growth = growth.mask(low.str.contains("|".join(growth_stages), na=False), 1.0)
    growth = growth.mask((low != "") & ~low.str.contains("|".join(growth_stages), na=False), 0.0)
    return pd.DataFrame({
        "F04_age_bucket": df["COMPANY_AGE_YEARS_GRADATION"],
        "F04_stage": stage, "F04_growth_flag": growth,
    }, index=df.index)


# --- F-05 ------------------------------------------------------------------
def _parse_gradation(s: str) -> tuple[float, float] | None:
    """'от 101 до 250' -> (101, 250); 'более 500' -> (500, 500); мусор -> None."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:[.,]\d+)?", str(s).replace(",", "."))]
    if not nums:
        return None
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (min(nums), max(nums))


def f05_headcount(df: pd.DataFrame) -> pd.DataFrame:
    """Согласованная численность: середины градаций ФНС и СПАРК + конфликт."""
    fns = df["AMOUNT_WORKERS_FNS_GRADATION"].map(_parse_gradation)
    spark = df["AMOUNT_WORKERS_SPARK_GRADATION"].map(_parse_gradation)

    def mid(t):
        return np.nan if t is None else (t[0] + t[1]) / 2.0

    mid_f = fns.map(mid).astype(float)
    mid_s = spark.map(mid).astype(float)

    def row_mid(a, b):
        vals = [v for v in (a, b) if pd.notna(v)]
        return float(np.mean(vals)) if vals else np.nan

    headcount_mid = pd.Series(
        [row_mid(a, b) for a, b in zip(mid_f, mid_s)], index=df.index)

    def conflict(fns_t, spark_t):
        if fns_t is None or spark_t is None:
            return np.nan
        return float(not (fns_t[0] <= spark_t[1] and spark_t[0] <= fns_t[1]))

    conflicts = pd.Series([conflict(a, b) for a, b in zip(fns, spark)], index=df.index)
    rev = _to_num(df["REVENUE_COMPANY"])
    rev_per_emp = rev / headcount_mid.replace(0, np.nan)
    return pd.DataFrame({
        "F05_headcount_mid": headcount_mid, "F05_conflict": conflicts,
        "F05_rev_per_emp": rev_per_emp,
    }, index=df.index)


# --- F-06 ------------------------------------------------------------------
def f06_ownership(df: pd.DataFrame) -> pd.DataFrame:
    """Форма собственности; is_state_related по ключевым словам словаря."""
    okfs = df["OKFS_NAME"].astype("string").str.strip()
    org = df["ORG_TYPE"].astype("string").str.strip()
    both = (okfs.fillna("") + " " + org.fillna("")).str.lower()
    is_state = pd.Series(np.nan, index=df.index)
    known = okfs.notna() | org.notna()
    is_state = is_state.mask(known, 0.0)
    is_state = is_state.mask(known & both.str.contains("|".join(STATE_KEYWORDS), na=False), 1.0)
    return pd.DataFrame({"F06_okfs": okfs, "F06_org_type": org,
                         "F06_is_state_related": is_state}, index=df.index)


# --- F-07 ------------------------------------------------------------------
def f07_geography(df: pd.DataFrame, link: pd.DataFrame | None = None) -> pd.DataFrame:
    """География: домашний регион/город + регионы присутствия через физлиц."""
    home_region = df["REGION"].astype("string").str.strip()
    home_city = df["CITY"].astype("string").str.strip()
    regions_set: dict[str, set] = {u: ({home_region.iloc[i]} if pd.notna(home_region.iloc[i]) else set())
                                   for i, u in enumerate(df["SYNTHETIC_U"])}
    if link is not None and {"SYNTHETIC_F", "SYNTHETIC_U"} <= set(link.columns):
        lk = link.copy()
        # регион физлица: колонка REGION или RC_NAME (город -> не регион, не добавляем)
        reg_col = "REGION" if "REGION" in lk.columns else None
        if reg_col is not None:
            for u, r in zip(lk["SYNTHETIC_U"], lk[reg_col]):
                if pd.notna(r) and str(r).strip():
                    regions_set.setdefault(str(u), set()).add(str(r).strip())
    n_regions = pd.Series([len(regions_set.get(u, set())) for u in df["SYNTHETIC_U"]],
                          index=df.index)
    # энтропия по долям регионов связанных физлиц (простая, по числу связей)
    entropy = pd.Series(np.nan, index=df.index)
    if link is not None and {"SYNTHETIC_F", "SYNTHETIC_U"} <= set(link.columns) and reg_col:
        cnt = (link.assign(r=link[reg_col].astype("string").str.strip())
               .dropna(subset=["r"]).groupby(["SYNTHETIC_U", "r"]).size())
        for u, grp in cnt.groupby(level=0):
            p = grp / grp.sum()
            entropy.loc[u if u in entropy.index else u] = float(-(p * np.log(p)).sum())
    return pd.DataFrame({
        "F07_home_region": home_region, "F07_home_city": home_city,
        "F07_n_presence_regions": n_regions.where(n_regions > 0),
        "F07_geo_entropy": entropy,
    }, index=df.index)


# --- F-08 ------------------------------------------------------------------
PRODUCT_COLS = {
    "RKO": "CL_PRODUCT_RKO_MARK", "GAR": "CL_PRODUCT_GAR_MARK",
    "LOA": "CL_PRODUCT_LOA_MARK", "LEA": "CL_PRODUCT_LEA_MARK",
    "DEP": "CL_PRODUCT_DEP_MARK", "ZPP": "CL_PRODUCT_ZPP_MARK",
    "FACTORING": "CL_PRODUCT_FACTORING_MARK",
}


def f08_products(df: pd.DataFrame) -> pd.DataFrame:
    """Вектор из 7 продуктовых флагов (0/1/NaN) + breadth = число продуктов."""
    out = {}
    for short, col in PRODUCT_COLS.items():
        out[f"F08_{short}"] = _flag01(df[col])
    res = pd.DataFrame(out, index=df.index)
    res["F08_breadth"] = res[list(out)].sum(axis=1, min_count=1)
    return res


# --- F-09 ------------------------------------------------------------------
def f09_fts(df: pd.DataFrame, country_currency: dict[str, str] | None = None) -> pd.DataFrame:
    """Внешнеторговая вовлечённость: флаг, страна, подсказка валюты."""
    flag = _flag01(df["FTS_FLAG"])
    country = df["FTS_COUNTRY_NAME"].astype("string").str.strip().str.upper()
    fx_hint = country.map(country_currency or {})
    return pd.DataFrame({"F09_fts_flag": flag, "F09_fts_country": country,
                         "F09_fx_hint": fx_hint}, index=df.index)


# --- F-10 ------------------------------------------------------------------
def f10_client_type(df: pd.DataFrame) -> pd.DataFrame:
    """cl_type: действующий / потенциальный (нормализация словаря)."""
    t = df["CL_TYPE"].astype("string").str.strip().str.lower()
    norm = pd.Series(pd.NA, index=df.index, dtype="string")
    norm = norm.mask(t.str.contains("действ", na=False), "existing")
    norm = norm.mask(t.str.contains("потенц|новый|prospect", na=False), "potential")
    norm = norm.mask(t.str.contains("existing|current", na=False), "existing")
    norm = norm.mask(t.eq("client").fillna(False), "existing")
    return pd.DataFrame({"F10_cl_type_raw": df["CL_TYPE"],
                         "F10_cl_type": norm}, index=df.index)


# --- F-11 ------------------------------------------------------------------
def f11_group(df: pd.DataFrame) -> pd.DataFrame:
    """Группа: размер, суммарная выручка, HHI отраслей внутри группы."""
    g = df.assign(
        rev=_to_num(df["REVENUE_COMPANY"]),
        okv=df["MAINOKVED_CODE"].astype("string").str.strip().str[:2],
    ).groupby("GROUP_ID", dropna=True)
    size = g["rev"].transform("size").where(df["GROUP_ID"].notna())
    group_rev = g["rev"].transform("sum").where(df["GROUP_ID"].notna())
    share = (df.assign(sh=g["rev"].transform(lambda s: s / s.sum()))["sh"])
    hhi = share.pow(2).groupby(df["GROUP_ID"]).transform("sum").where(df["GROUP_ID"].notna())
    return pd.DataFrame({"F11_group_size": size, "F11_group_revenue": group_rev,
                         "F11_group_div_hhi": hhi}, index=df.index)


# --- F-12 ------------------------------------------------------------------
def f12_negativemark(df: pd.DataFrame) -> pd.DataFrame:
    """NEGATIVEMARK как есть (семантику поля уточнить у организаторов, OQ-03)."""
    return pd.DataFrame({"F12_negativemark": df["NEGATIVEMARK"]}, index=df.index)


# --- F-13 ------------------------------------------------------------------
def f13_completeness(df: pd.DataFrame, weights: dict[str, float],
                     f05_out: pd.DataFrame | None = None) -> pd.DataFrame:
    """Полнота данных (взвешенная доля непустых ключевых полей) + флаги шума."""
    wsum, acc = 0.0, pd.Series(0.0, index=df.index)
    for col, w in weights.items():
        if col not in df.columns:
            continue
        filled = df[col].notna() & (df[col].astype("string").str.strip().fillna("") != "")
        acc = acc + filled.astype(float) * float(w)
        wsum += float(w)
    completeness = (acc / wsum) if wsum else pd.Series(np.nan, index=df.index)

    rev = _to_num(df["REVENUE_COMPANY"])
    zero_rev = (rev == 0).astype(float).where(rev.notna())
    noise = zero_rev.fillna(0.0)
    if f05_out is not None and "F05_conflict" in f05_out:
        noise = noise + f05_out["F05_conflict"].fillna(0.0)
    return pd.DataFrame({"F13_completeness": completeness.clip(0, 1),
                         "F13_noise_flags": noise}, index=df.index)


def build_features(df: pd.DataFrame, okved_map=None, link=None,
                   country_currency=None, completeness_weights=None,
                   exposure_cfg: dict | None = None) -> pd.DataFrame:
    """Собирает все F-метрики в один DataFrame (индекс = клиенты)."""
    parts = [
        f01_scale(df), f02_position(df), f03_sector(df, okved_map),
        f04_age_stage(df), f05_headcount(df), f06_ownership(df),
        f07_geography(df, link), f08_products(df),
        f09_fts(df, country_currency), f10_client_type(df), f11_group(df),
        f12_negativemark(df),
    ]
    out = pd.concat(parts, axis=1)
    f05_out = out[["F05_headcount_mid", "F05_conflict", "F05_rev_per_emp"]]
    if completeness_weights:
        out = pd.concat([out, f13_completeness(df, completeness_weights, f05_out)],
                        axis=1)
    return out
