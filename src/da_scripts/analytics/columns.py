# -*- coding: utf-8 -*-
"""Канонические имена колонок и устойчивое сопоставление с реальными файлами.

ТЗ (раздел 2) прямо предупреждает: реальные файлы до написания ТЗ не
рассматривались, имена и словари значений надо проверить (WP-D1). Поэтому
везде используется resolve_columns(): точное совпадение -> нормализация
(регистр/пробелы/подчёркивания) -> подстрока. Если колонка не найдена —
функция вернёт её как None, а profiling.py явно перечислит пропуски.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# --- Приложение 1 кейса: df_Uid_data_hack.csv (24 поля) -------------------
CLIENT_COLUMNS = [
    "SYNTHETIC_U", "REVENUE_COMPANY", "MAINOKVED_CODE", "MAINOKVED_NAME",
    "REGION", "CITY", "ECO_MARK_LABEL", "COMPANY_AGE_YEARS_GRADATION",
    "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION",
    "OKFS_NAME", "ORG_TYPE",
    "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK", "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK",
    "CL_PRODUCT_FACTORING_MARK",
    "FTS_FLAG", "FTS_COUNTRY_NAME", "CL_TYPE", "GROUP_ID", "NEGATIVEMARK",
]

# --- Платежи (имя файла уточнить у организаторов, RSK-02) -----------------
PAYMENT_COLUMNS = ["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT", "PAY_DATE"]

# --- Связи юрлиц с физлицами: df_Fid_Uid_link_hack.csv --------------------
LINK_COLUMNS = ["SYNTHETIC_F", "SYNTHETIC_U", "RC_NAME", "CITY_NAME", "REGION"]

# Ключевые поля, без которых расчёт невозможен (остальные — мягко в None)
REQUIRED_CLIENT = ["SYNTHETIC_U", "REVENUE_COMPANY", "MAINOKVED_CODE"]


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9А-ЯЁ]", "", str(name).upper().replace("Ё", "Е"))


def resolve_columns(df: pd.DataFrame, wanted: list[str]) -> dict[str, str | None]:
    """Возвращает {каноническое_имя: реальное_имя_колонки или None}."""
    norm_map: dict[str, str] = {}
    for col in df.columns:
        norm_map.setdefault(_norm(col), col)  # первое имя выигрывает

    resolved: dict[str, str | None] = {}
    for want in wanted:
        if want in df.columns:
            resolved[want] = want
            continue
        nw = _norm(want)
        if nw in norm_map:
            resolved[want] = norm_map[nw]
            continue
        # подстрочный поиск: реальная колонка короче канонической —
        # FACTORING_MARK -> CL_PRODUCT_FACTORING_MARK, DEP_MARK -> CL_PRODUCT_DEP_MARK
        cands = [(len(n), real) for n, real in norm_map.items()
                 if n and n in nw]
        resolved[want] = max(cands)[1] if cands else None
    return resolved


def rename_canonical(df: pd.DataFrame, wanted: list[str]) -> pd.DataFrame:
    """Переименовывает найденные колонки в канонические имена; отсутствующие
    оставляет как NaN-колонки, чтобы последующий код не падал на KeyError."""
    mapping = resolve_columns(df, wanted)
    out = pd.DataFrame(index=df.index)
    for w in wanted:
        real = mapping.get(w)
        out[w] = df[real] if real is not None else pd.NA
    return out


def find_file(directory: str | Path, candidates: list[str]) -> Path | None:
    """Ищет файл по списку возможных имён (без учёта регистра)."""
    d = Path(directory)
    if not d.is_dir():
        return None
    lower = {p.name.lower(): p for p in d.iterdir() if p.is_file()}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    # неточное совпадение по подстроке
    for cand in candidates:
        key = cand.lower()
        for name, p in lower.items():
            if key in name:
                return p
    return None
