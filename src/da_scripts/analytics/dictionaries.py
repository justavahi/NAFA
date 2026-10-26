# -*- coding: utf-8 -*-
"""WP-D2: справочники.

Проверяет покрытие ОКВЭД -> SP на реальных кодах данных, выписывает словари
значений (ECO_MARK_LABEL, OKFS_NAME, ORG_TYPE, градации), определяет правила
growth_flag/is_state_related и соответствие «страна ФТС -> валюта».

Критерий приёмки: 100% кодов ОКВЭД из данных отнесены к профилю SP;
необъяснённые значения перечислены.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE_COUNTRY_CURRENCY = {
    "КИТАЙ": "CNY", "CHINA": "CNY", "ГЕРМАНИЯ": "EUR", "ГЕРМАНИЮ": "EUR",
    "ФРАНЦИЯ": "EUR", "ИТАЛИЯ": "EUR", "ИСПАНИЯ": "EUR", "НИДЕРЛАНДЫ": "EUR",
    "ПОЛЬША": "EUR", "ТУРЦИЯ": "TRY", "ИНДИЯ": "INR", "ОАЭ": "AED",
    "ВЬЕТНАМ": "USD", "БЕЛАРУСЬ": "BYN", "КАЗАХСТАН": "KZT",
    "УЗБЕКИСТАН": "UZS", "США": "USD", "ВЕЛИКОБРИТАНИЯ": "GBP",
    "ЯПОНИЯ": "JPY", "КОРЕЯ": "KRW", "БРАЗИЛИЯ": "BRL", "ЕГИПЕТ": "EGP",
    "ИРАН": "IRR", "СЕРБИЯ": "RSD",
}


def _norm_div(s) -> str:
    """Нормализует 2-значный дивизион: '1' / 1.0 / '01' -> '01'."""
    x = str(s).strip()
    if x.endswith(".0"):
        x = x[:-2]
    return x.zfill(2) if x.isdigit() else x


def check_okved_coverage(clients: pd.DataFrame,
                         okved_map: pd.DataFrame | None) -> dict:
    """Покрытие 2-значных кодов ОКВЭД профилями SP из справочника."""
    codes = (clients["MAINOKVED_CODE"].astype("string").str.strip()
             .str[:2].dropna().unique())
    result = {"n_codes": len(codes), "covered": [], "uncovered": [], "sp_counts": {}}
    if okved_map is None:
        result["uncovered"] = sorted(codes)
        result["error"] = "справочник okved_to_sector_profile.csv не найден"
        return result
    lut = {_norm_div(d): sp for d, sp in
           zip(okved_map["okved_division"], okved_map["sector_profile"])}
    for c in codes:
        sp = lut.get(str(c))
        if sp:
            result["covered"].append(str(c))
            result["sp_counts"][sp] = result["sp_counts"].get(sp, 0) + 1
        else:
            result["uncovered"].append(str(c))
    result["uncovered"] = sorted(result["uncovered"])
    return result


def extract_dictionaries(clients: pd.DataFrame,
                         fields: list[str] | None = None) -> dict[str, list[str]]:
    """Полный словарь значений по полям-справочникам (в файл 1, WP-D1)."""
    fields = fields or ["ECO_MARK_LABEL", "OKFS_NAME", "ORG_TYPE",
                        "COMPANY_AGE_YEARS_GRADATION",
                        "AMOUNT_WORKERS_FNS_GRADATION",
                        "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE",
                        "FTS_COUNTRY_NAME"]
    out: dict[str, list[str]] = {}
    for f in fields:
        if f in clients.columns:
            vals = (clients[f].astype("string").str.strip()
                    .dropna().unique().tolist())
            out[f] = sorted(vals)
    return out


def growth_stage_rule(ecomark_values: list[str]) -> dict:
    """Определяет, какие значения ECO_MARK_LABEL дают growth_flag = 1.

    Стартовое правило ТЗ: стадии «роста/запуска». Возвращает правило,
    которое нужно подтвердить по словарю данных (допущение).
    """
    growth = [v for v in ecomark_values
              if any(k in str(v).lower() for k in ("рост", "запуск", "старт"))]
    return {"rule": "growth_flag=1 для значений, содержащих: рост/запуск/старт",
            "matched_values": growth, "status": "допущение — подтвердить по данным"}


def state_related_rule(okfs_values: list[str]) -> dict:
    """Определяет значения ОКФС, дающие is_state_related = 1."""
    state = [v for v in okfs_values
             if any(k in str(v).lower() for k in ("гос", "муницип", "федерал"))]
    return {"rule": "is_state_related=1 для значений с 'гос/муницип/федерал'",
            "matched_values": state, "status": "допущение — подтвердить по данным"}


def country_currency_dict(fts_countries: list[str]) -> tuple[dict[str, str], list[str]]:
    """Соответствие страна ФТС -> валюта; неизвестные страны — в список."""
    known = {c: BASE_COUNTRY_CURRENCY[c.upper()] for c in fts_countries
             if str(c).upper() in BASE_COUNTRY_CURRENCY}
    unknown = [c for c in fts_countries if str(c).upper() not in BASE_COUNTRY_CURRENCY]
    return known, sorted(unknown)


def run_dictionaries(clients: pd.DataFrame, okved_map: pd.DataFrame | None,
                     out_dir: str | Path) -> dict:
    """WP-D2 целиком: отчёт markdown + вопрос BA по непокрытым кодам."""
    out = Path(out_dir) / "dictionaries"
    out.mkdir(parents=True, exist_ok=True)

    cov = check_okved_coverage(clients, okved_map)
    dicts = extract_dictionaries(clients)
    growth = growth_stage_rule(dicts.get("ECO_MARK_LABEL", []))
    state = state_related_rule(dicts.get("OKFS_NAME", []))
    cc, unknown_countries = country_currency_dict(
        dicts.get("FTS_COUNTRY_NAME", []))

    lines = [
        "# WP-D2. Справочники", "",
        f"Уникальных 2-значных кодов ОКВЭД в данных: **{cov['n_codes']}**",
        f"Покрыто профилем SP: **{len(cov['covered'])}** "
        f"({100 * len(cov['covered']) / max(1, cov['n_codes']):.1f}%)", "",
        "## Непокрытые коды (передать BA)", "",
        ", ".join(cov["uncovered"]) if cov["uncovered"] else "— все покрыты", "",
        "## Распределение клиентов по SP", "",
        "| SP | клиентов |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(cov["sp_counts"].items())], "",
        "## Словари значений", "",
    ]
    for f, vals in dicts.items():
        lines += [f"### {f}", ", ".join(map(str, vals)) or "—", ""]
    lines += [
        "## Правила флагов", "",
        f"- growth_flag: {growth['rule']}. Значения: "
        f"{', '.join(map(str, growth['matched_values'])) or '—'}. {growth['status']}",
        f"- is_state_related: {state['rule']}. Значения: "
        f"{', '.join(map(str, state['matched_values'])) or '—'}. {state['status']}", "",
        "## Страна ФТС -> валюта", "",
        "| страна | валюта |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(cc.items())], "",
        f"Неизвестные страны (добавить в справочник): "
        f"{', '.join(unknown_countries) or '—'}", "",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    if cov["uncovered"]:
        (out / "uncovered_okved.csv").write_text(
            "\n".join(cov["uncovered"]), encoding="utf-8")
    return {"coverage": cov, "dictionaries": dicts,
            "country_currency": cc, "unknown_countries": unknown_countries,
            "report": str(out / "report.md")}
