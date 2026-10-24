# -*- coding: utf-8 -*-
"""WP-D1: профилирование данных (проверки раздела 3.1 ТЗ).

Результат: out_dir/profiling/report.md + таблицы CSV. Список расхождений
с кейсом (единицы выручки, словари, имена файлов) передаётся BA для OQ-xx.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .columns import CLIENT_COLUMNS, PAYMENT_COLUMNS, resolve_columns
from .metrics_f import PRODUCT_COLS, _parse_gradation, _to_num

DICT_FIELDS = ["ECO_MARK_LABEL", "OKFS_NAME", "ORG_TYPE",
               "COMPANY_AGE_YEARS_GRADATION",
               "AMOUNT_WORKERS_FNS_GRADATION",
               "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE"]


def _pct(s: float) -> str:
    return f"{100 * s:.1f}%"


def profile_clients(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Профилирование файла клиентов. Возвращает (таблица находок, вопросы BA)."""
    findings: list[dict] = []
    questions: list[str] = []
    n = len(df)
    findings.append({"блок": "Размер", "проверка": "число строк",
                     "результат": str(n)})

    # --- дубли SYNTHETIC_U -------------------------------------------------
    dup = int(df["SYNTHETIC_U"].duplicated().sum())
    findings.append({"блок": "Размер", "проверка": "дубли SYNTHETIC_U",
                     "результат": str(dup),
                     "критерий": "должно быть 0"})
    if dup:
        questions.append(f"Найдено {dup} дубликатов SYNTHETIC_U — уточнить у организаторов")

    # --- масштаб и единицы выручки (RSK-04) --------------------------------
    rev = _to_num(df["REVENUE_COMPANY"])
    if rev.notna().any():
        share_large = (rev >= 15e9).mean()
        findings.append({"блок": "Масштаб", "проверка": "медиана выручки",
                         "результат": f"{rev.median():,.0f}"})
        findings.append({"блок": "Масштаб", "проверка": "доля >= 15 млрд (порог FM-01)",
                         "результат": _pct(share_large),
                         "критерий": "если мала — сообщить BA: возможно, единицы не рубли"})
        if share_large < 0.05:
            questions.append(
                f"Доля клиентов с выручкой >= 15 млрд = {_pct(share_large)}: "
                "проверить единицы REVENUE_COMPANY (тыс./млн?) — вопрос BA (OQ)")
        q = rev.quantile([0.25, 0.5, 0.75, 0.95]).to_dict()
        for k, v in q.items():
            findings.append({"блок": "Масштаб",
                             "проверка": f"квантиль выручки p{int(k*100)}",
                             "результат": f"{v:,.0f}"})

    # --- пропуски -----------------------------------------------------------
    miss = df.isna().mean().sort_values(ascending=False)
    for col in CLIENT_COLUMNS:
        if col in df.columns and miss[col] > 0:
            findings.append({"блок": "Пропуски", "проверка": f"NULL в {col}",
                             "результат": _pct(miss[col])})

    # --- словари значений ----------------------------------------------------
    for col in DICT_FIELDS:
        if col in df.columns:
            vals = df[col].astype("string").str.strip().value_counts(dropna=True)
            top = "; ".join(f"{k} ({v})" for k, v in vals.head(12).items())
            findings.append({"блок": "Словари", "проверка": f"значения {col}",
                             "результат": top or "пусто"})

    # --- продукты: значения флагов и соотношение по CL_TYPE ------------------
    for short, col in PRODUCT_COLS.items():
        if col in df.columns:
            vals = df[col].astype("string").str.strip().value_counts(dropna=False)
            findings.append({"блок": "Продукты",
                             "проверка": f"{col}: значения",
                             "результат": dict(vals.head(6))})
    if "CL_TYPE" in df.columns:
        ct = df["CL_TYPE"].astype("string").str.strip()
        findings.append({"блок": "Продукты", "проверка": "разбивка по CL_TYPE",
                         "результат": dict(ct.value_counts(dropna=False))})
        questions.append(
            "Проверить: у потенциальных клиентов NULL продуктовых флагов = "
            "«неизвестно», а не «нет продукта» (правило ТЗ)")

    # --- ФНС и СПАРК ----------------------------------------------------------
    fns = df["AMOUNT_WORKERS_FNS_GRADATION"].map(_parse_gradation)
    spark = df["AMOUNT_WORKERS_SPARK_GRADATION"].map(_parse_gradation)
    pairs = [(a, b) for a, b in zip(fns, spark) if a and b]
    if pairs:
        conflicts = sum(1 for a, b in pairs if not (a[0] <= b[1] and b[0] <= a[1]))
        findings.append({"блок": "ФНС и СПАРК",
                         "проверка": "доля конфликтов градаций численности",
                         "результат": _pct(conflicts / len(pairs))})

    # --- ВЭД -------------------------------------------------------------------
    if "FTS_COUNTRY_NAME" in df.columns:
        countries = (df["FTS_COUNTRY_NAME"].astype("string").str.strip()
                     .value_counts().head(20))
        findings.append({"блок": "ВЭД", "проверка": "список стран (топ-20)",
                         "результат": dict(countries)})
        questions.append("Собрать полный словарь страна ФТС -> валюта (WP-D2)")

    # --- NEGATIVEMARK -----------------------------------------------------------
    if "NEGATIVEMARK" in df.columns:
        nm = df["NEGATIVEMARK"].value_counts(dropna=False)
        findings.append({"блок": "NEGATIVEMARK", "проверка": "распределение",
                         "результат": dict(nm.head(6))})
        questions.append("NEGATIVEMARK: семантика поля расходится с описанием — "
                         "уточнить у организаторов (OQ-03)")

    # --- группы -------------------------------------------------------------------
    if "GROUP_ID" in df.columns and df["GROUP_ID"].notna().any():
        gg = df.assign(
            okv2=df["MAINOKVED_CODE"].astype("string").str.strip().str[:2]
        ).groupby("GROUP_ID")
        sizes = gg.size()
        one_industry = gg["okv2"].nunique().eq(1).mean()
        findings.append({"блок": "Группы", "проверка": "размеры групп",
                         "результат": f"медиана {sizes.median():.0f}, макс {sizes.max()}"})
        findings.append({"блок": "Группы", "проверка": "доля одноотраслевых групп",
                         "результат": _pct(one_industry)})

    # --- география -------------------------------------------------------------------
    for col in ("CITY", "REGION"):
        if col in df.columns:
            findings.append({"блок": "География", "проверка": f"доля пустых {col}",
                             "результат": _pct(df[col].isna().mean())})

    # --- покрытие по SP (заполняется после WP-D2) --------------------------------------
    code = df["MAINOKVED_CODE"].astype("string").str.strip().str[:2]
    findings.append({"блок": "Покрытие", "проверка": "уникальных 2-значных ОКВЭД",
                     "результат": str(code.nunique())})

    # --- выбросы (winsorization для графиков, не для расчётов) --------------------------
    if rev.notna().any():
        q1, q3 = rev.quantile([0.25, 0.75])
        iqr = q3 - q1
        lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
        n_out = int(((rev < lo) | (rev > hi)).sum())
        findings.append({"блок": "Выбросы", "проверка": "выручка вне 3·IQR",
                         "результат": str(n_out),
                         "критерий": "winsorize только на графиках"})

    return pd.DataFrame(findings), questions


def profile_payments(pay: pd.DataFrame | None, client_ids: set[str]) -> tuple[pd.DataFrame, list[str]]:
    """Проверки платежей: идентификаторы вне клиентов, нули/отрицательные,
    самоплатежи, PAY_CNT = 0. Правила очистки -> в паспорт метрик G."""
    findings, questions = [], []
    if pay is None:
        findings.append({"блок": "Платежи", "проверка": "файл",
                         "результат": "НЕ НАЙДЕН — имя файла уточнить (RSK-02)"})
        questions.append("Уточнить имя файла платежей у организаторов")
        return pd.DataFrame(findings), questions

    findings.append({"блок": "Платежи", "проверка": "число строк",
                     "результат": str(len(pay))})
    for side in ("PAYER", "BENEFICIARY"):
        n_outside = int((~pay[side].isin(client_ids)).sum())
        findings.append({"блок": "Платежи",
                         "проверка": f"{side} вне списка клиентов",
                         "результат": f"{n_outside} строк",
                         "критерий": "контрагенты анонимны — покрытие SP считать по совпадениям"})
    amt = pd.to_numeric(pay["PAY_AMT"], errors="coerce")
    findings.append({"блок": "Платежи", "проверка": "нулевых/отрицательных сумм",
                     "результат": str(int((amt <= 0).sum()))})
    self_pay = int((pay["PAYER"] == pay["BENEFICIARY"]).sum())
    findings.append({"блок": "Платежи", "проверка": "самоплатежей",
                     "результат": str(self_pay)})
    if "PAY_CNT" in pay.columns:
        cnt = pd.to_numeric(pay["PAY_CNT"], errors="coerce")
        findings.append({"блок": "Платежи", "проверка": "PAY_CNT = 0",
                         "результат": str(int((cnt == 0).sum()))})
    if amt.isna().any():
        questions.append(f"{int(amt.isna().sum())} строк с нечисловой PAY_AMT — правило очистки")
    return pd.DataFrame(findings), questions


def run_profiling(clients: pd.DataFrame, pay: pd.DataFrame | None,
                  out_dir: str | Path) -> dict:
    """WP-D1 целиком: отчёт markdown + CSV-таблицы в out_dir/profiling/."""
    out = Path(out_dir) / "profiling"
    out.mkdir(parents=True, exist_ok=True)
    fnd_clients, q1 = profile_clients(clients)
    fnd_pay, q2 = profile_payments(pay, set(clients["SYNTHETIC_U"].astype(str)))

    fnd_clients.to_csv(out / "clients_checks.csv", index=False, encoding="utf-8-sig")
    fnd_pay.to_csv(out / "payments_checks.csv", index=False, encoding="utf-8-sig")

    lines = [
        "# WP-D1. Профилирование данных", "",
        "Результаты проверок раздела 3.1 ТЗ. Расхождения с кейсом передать BA "
        "для списка OQ-xx (раздел 13.2 справочника).", "",
        "## Файл клиентов", "",
        fnd_clients.to_markdown(index=False), "",
        "## Файл платежей", "",
        fnd_pay.to_markdown(index=False), "",
        "## Вопросы BA / организаторам (OQ-кандидаты)", "",
        *[f"- {q}" for q in dict.fromkeys(q1 + q2)], "",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(out / "report.md"),
            "questions": list(dict.fromkeys(q1 + q2))}
