# -*- coding: utf-8 -*-
"""Экспорт авто-приложения со статистиками метрик для docs/da/01_metrics_descriptions.md.

Читает выгрузки контура DA (out/features/*.csv) и формирует markdown-таблицы:
распределения (N, доля заполненных, min, p50, p90, max), словари категорий и
sanity-проверки из раздела 4.3 ТЗ. Приложение обновляется одним запуском:

    python -m analytics.export_doc_tables --features-dir out/features --out out/01_stats_appendix.md

Полученный файл копируется в репозиторий как docs/da/01_stats_appendix.md
(файл 1 ссылается на него вместо ручного копирования чисел).
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .config import FACTOR_ORDER

CAT_COLS = {"F03_okved_div", "F03_section", "F03_sector_profile",
            "F04_age_bucket", "F04_stage", "F06_okfs", "F06_org_type",
            "F09_fts_country", "F09_fx_hint", "F10_cl_type_raw",
            "F10_cl_type", "F07_home_region", "F07_home_city"}

GROUPS = [
    ("F-01 Масштаб", ["F01_is_large", "F01_rev_log10"]),
    ("F-02 Позиция в отрасли", ["F02_share_okved", "F02_pctl_industry",
                                "F02_pctl_region"]),
    ("F-03 Отрасль", ["F03_sector_profile", "F03_section"]),
    ("F-04 Возраст/стадия", ["F04_stage", "F04_growth_flag"]),
    ("F-05 Персонал", ["F05_headcount_mid", "F05_conflict",
                       "F05_rev_per_emp"]),
    ("F-06 Форма собственности", ["F06_is_state_related"]),
    ("F-07 География", ["F07_n_presence_regions", "F07_geo_entropy"]),
    ("F-08 Продукты", ["F08_RKO", "F08_GAR", "F08_LOA", "F08_LEA",
                       "F08_DEP", "F08_ZPP", "F08_FACTORING",
                       "F08_breadth"]),
    ("F-09 ВЭД", ["F09_fts_flag"]),
    ("F-10 Тип клиента", ["F10_cl_type"]),
    ("F-11 Группа", ["F11_group_size", "F11_group_revenue",
                     "F11_group_div_hhi"]),
    ("F-12 NEGATIVEMARK", ["F12_negativemark"]),
    ("F-13 Полнота", ["F13_completeness", "F13_noise_flags"]),
    ("G-01 Обороты", ["G01_in", "G01_out", "G01_net_ratio"]),
    ("G-02 Контрагенты", ["G02_n_cust", "G02_n_sup"]),
    ("G-03 HHI", ["G03_HHI_in", "G03_HHI_out"]),
    ("G-07 Внутригрупповые", ["G07_intragroup"]),
    ("G-10 Чек/частота", ["G10_avg_ticket", "G10_freq"]),
]


def _fmt(x: float) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    if float(x) == int(float(x)) and abs(x) < 1e6:
        return str(int(x))
    return f"{x:.4g}"


def _row(name: str, col: pd.Series) -> str:
    filled = col.notna()
    if not filled.any():
        return f"| `{name}` | 0 (0%) | — | — | — | — |"
    if col.name in CAT_COLS or col.dtype == object:
        vc = col.value_counts()
        top = "; ".join(f"{k} ({v / len(col):.1%})" for k, v in vc.head(3).items())
        return (f"| `{name}` | {filled.mean():.1%}, {len(vc)} уник. "
                f"| {top} | — | — | — |")
    q = col.quantile([0.5, 0.9])
    return (f"| `{name}` | {filled.mean():.1%} | {_fmt(col.min())} "
            f"| {_fmt(q[0.5])} | {_fmt(q[0.9])} | {_fmt(col.max())} |")


def _section(df: pd.DataFrame, title: str, cols: list[str]) -> list[str]:
    lines = [f"### {title}", ""]
    have = [c for c in cols if c in df.columns]
    missing = [c for c in cols if c not in df.columns]
    lines.append("| Колонка | Заполнено | min / словарь | p50 | p90 | max |")
    lines.append("|---|---|---|---|---|---|")
    for c in have:
        lines.append(_row(c, df[c]))
    for c in missing:
        lines.append(f"| `{c}` | колонка отсутствует в выгрузке | | | | |")
    lines.append("")
    return lines


def _sanity(feats: pd.DataFrame, eps: pd.DataFrame) -> list[str]:
    checks: list[tuple[str, str, bool]] = []
    ecols = [c for c in eps.columns if c.startswith("eps_")]
    if ecols:
        ok = all(eps[c].dropna().between(0, 1).all() for c in ecols)
        checks.append(("eps_* (12 факторов E-14) ∈ [0, 1]", f"{len(ecols)} колонок", ok))
    if "E15_rate_net" in eps.columns:
        ok = eps["E15_rate_net"].dropna().between(-1, 1).all()
        checks.append(("E15_rate_net ∈ [-1, 1]", f"min={_fmt(eps['E15_rate_net'].min())}, max={_fmt(eps['E15_rate_net'].max())}", ok))
    for c in ("G03_HHI_in", "G03_HHI_out"):
        if c in feats.columns:
            s = feats[c].dropna()
            if len(s):
                checks.append((f"{c} ∈ (0, 1] (HHI)", f"min={_fmt(s.min())}, max={_fmt(s.max())}", bool(((s > 0) & (s <= 1)).all())))
    if "G01_net_ratio" in feats.columns:
        s = feats["G01_net_ratio"].dropna()
        if len(s):
            checks.append(("G01_net_ratio ∈ [0, 1]", f"min={_fmt(s.min())}, max={_fmt(s.max())}", bool(s.between(0, 1).all())))
    if "F13_completeness" in feats.columns:
        s = feats["F13_completeness"].dropna()
        checks.append(("F13_completeness ∈ [0, 1]", f"медиана={_fmt(s.median())}", bool(s.between(0, 1).all())))
    if "F01_is_large" in feats.columns:
        checks.append(("Доля крупного бизнеса F01_is_large=1 (порог 15 млрд)",
                       f"{feats['F01_is_large'].mean():.2%}", True))
    if "F03_sector_profile" in feats.columns:
        cov = feats["F03_sector_profile"].notna().mean()
        checks.append(("Покрытие ОКВЭД → SP (F-03 заполнен)", f"{cov:.1%}", cov > 0.99))
    if "G01_in" in feats.columns:
        cov = feats["G01_in"].notna().mean()
        checks.append(("Доля клиентов с платёжными данными", f"{cov:.1%}", True))
    if "F09_fts_flag" in feats.columns:
        checks.append(("Доля клиентов с ВЭД (F09_fts_flag=1)",
                       f"{feats['F09_fts_flag'].mean():.2%}", True))
    lines = ["## Sanity-проверки (раздел 4.3 ТЗ)", "",
             "| Проверка | Значение | Статус |", "|---|---|---|"]
    for name, val, ok in checks:
        lines.append(f"| {name} | {val} | {'OK' if ok else 'ВНИМАНИЕ'} |")
    lines.append("")
    return lines


def main(features_dir: str | Path = "out/features",
         out: str | Path = "out/01_stats_appendix.md") -> Path:
    fdir = Path(features_dir)
    feats = pd.read_csv(fdir / "client_features.csv", encoding="utf-8-sig")
    pays_path = fdir / "payment_features.csv"
    pays = (pd.read_csv(pays_path, encoding="utf-8-sig") if pays_path.exists()
            else pd.DataFrame(index=feats.index))
    eps = pd.read_csv(fdir / "exposures.csv", encoding="utf-8-sig")

    lines = [f"# Приложение A. Статистики метрик по данным кейса (авто-экспорт)",
             "",
             f"Дата прогона: {date.today().isoformat()}. ",
             f"Клиентов: {len(feats)}; строк exposures: {len(eps)}.",
             "Источник: `out/features/*.csv` контура DA (`python -m analytics.run_all`).",
             "Числа обновляются автоматически: `python -m analytics.export_doc_tables`.",
             ""]
    for title, cols in GROUPS:
        src = pays if title.startswith("G-") and len(pays.columns) > 1 else feats
        lines += _section(src, title, cols)
    lines += _section(eps, "E-14/E-15 Вектор экспозиций",
                      [f"eps_{f}" for f in FACTOR_ORDER] + ["E15_rate_net"])
    lines += _sanity(feats, eps)
    if "F03_sector_profile" in feats.columns:
        vc = feats["F03_sector_profile"].value_counts()
        lines += ["## Распределение клиентов по профилям отраслей (SP)", "",
                  "| SP | Клиентов | Доля |", "|---|---|---|"]
        for k, v in vc.items():
            lines.append(f"| {k} | {v} | {v / len(feats):.1%} |")
        lines.append("")

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[DA] Приложение статистик: {out} ({len(lines)} строк)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Авто-приложение статистик для файла 1")
    ap.add_argument("--features-dir", default="out/features")
    ap.add_argument("--out", default="out/01_stats_appendix.md")
    a = ap.parse_args()
    main(a.features_dir, a.out)
