# -*- coding: utf-8 -*-
"""WP-D7: золотой набор.

Старт: 16 seed-тестов (golden_set_seed.csv, приложение A справочника).
Задача: расширить до 40-60 пар «клиент x событие», разметить двумя
аналитиками по шкале 0..3, посчитать согласие Cohen kappa >= 0.6,
отделить hold-out набор (события, не использовавшиеся при настройке).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

RELEVANCE_MAP = {"noise": 0, "low": 1, "medium": 2, "high": 3}


def load_golden(path: str | Path) -> pd.DataFrame:
    """Загружает разметку. Ожидаемые колонки:
    client_id, event_id, relevance (Noise/Low/Medium/High или 0..3),
    annotator1, annotator2 (опционально), archetype, section (опционально)."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    return df


def relevance_to_int(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    return s.astype("string").str.strip().str.lower().map(RELEVANCE_MAP)


def annotation_template(seed_path: str | Path, out_path: str | Path,
                        n_new: int = 30) -> pd.DataFrame:
    """Создаёт CSV-шаблон для ручной разметки двумя аналитиками (WP-D7)."""
    seed = pd.read_csv(seed_path, encoding="utf-8-sig") if Path(seed_path).exists() \
        else pd.DataFrame(columns=["id", "client", "event", "expected_relevance"])
    rows = []
    for _, r in seed.iterrows():
        rows.append({"pair_id": r.get("id", len(rows) + 1),
                     "client_id": r.get("client", r.get("client_id", "")),
                     "event_id": r.get("event", r.get("event_id", "")),
                     "annotator1": "", "annotator2": "",
                     "note": f"seed: {r.get('expected_relevance', '')}"})
    for i in range(n_new):
        rows.append({"pair_id": f"NEW-{i + 1:02d}", "client_id": "", "event_id": "",
                     "annotator1": "", "annotator2": "", "note": "новая пара"})
    tpl = pd.DataFrame(rows)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tpl.to_csv(out_path, index=False, encoding="utf-8-sig")
    return tpl


def cohen_kappa(a: pd.Series, b: pd.Series) -> float:
    """Согласие двух разметчиков (шкала 0..3). Цель WP-D7: kappa >= 0.6."""
    pair = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    if pair.empty:
        return np.nan
    cats = sorted(set(pair["a"]) | set(pair["b"]))
    n = len(pair)
    po = (pair["a"] == pair["b"]).mean()
    pe = sum(((pair["a"] == c).sum() / n) * ((pair["b"] == c).sum() / n)
             for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else np.nan


def kappa_report(golden: pd.DataFrame) -> dict:
    """Kappa по всем парам с двумя разметками."""
    if "annotator1" not in golden.columns or "annotator2" not in golden.columns:
        return {"kappa": np.nan, "n_pairs": 0,
                "status": "нужны колонки annotator1/annotator2"}
    a = relevance_to_int(golden["annotator1"])
    b = relevance_to_int(golden["annotator2"])
    ok = a.notna() & b.notna()
    k = cohen_kappa(a[ok], b[ok])
    return {"kappa": k, "n_pairs": int(ok.sum()),
            "target": 0.6,
            "status": "OK" if (pd.notna(k) and k >= 0.6) else "ниже цели — "
                       "пересогласовать шкалу и правила разметки"}


def split_holdout(golden: pd.DataFrame, by: str = "event_id",
                  frac: float = 0.3, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Отделяет hold-out: пары, отобранные по группам `by` (события/архетипы),
    которые НЕ использовались при настройке весов (WP-D7/WP-D8)."""
    rng = np.random.default_rng(seed)
    if by not in golden or golden[by].isna().any():
        raise ValueError("holdout grouping requires a nonmissing group for every row")
    if not 0 < frac < 1:
        raise ValueError("holdout fraction must be between zero and one")
    groups = golden[by].dropna().unique()
    if len(groups) < 2:
        raise ValueError("at least two independent groups required for a holdout")
    hold_groups = set(rng.choice(groups, size=min(len(groups)-1, max(1, int(len(groups) * frac))),
                                 replace=False))
    mask = golden[by].isin(hold_groups)
    return golden[~mask], golden[mask]


def save_golden_and_holdout(golden: pd.DataFrame, out_dir: str | Path,
                            by: str = "event_id") -> dict:
    """Финализирует WP-D7: data/golden_set.csv + data/holdout_set.csv."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dev, hold = split_holdout(golden, by=by)
    # Keep an annotation master, but never give held-out labels to calibration.
    golden.to_csv(out / "golden_set_all.csv", index=False, encoding="utf-8-sig")
    dev.to_csv(out / "golden_set.csv", index=False, encoding="utf-8-sig")
    hold.to_csv(out / "holdout_set.csv", index=False, encoding="utf-8-sig")
    return {"golden": str(out / "golden_set.csv"),
            "all_annotations": str(out / "golden_set_all.csv"),
            "holdout": str(out / "holdout_set.csv"),
            "n_development": int(len(dev)),
            "n_holdout": int(len(hold))}


def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="WP-D7: золотой набор")
    ap.add_argument("--template", help="seed-файл -> CSV-шаблон для разметки")
    ap.add_argument("--out", help="куда писать шаблон / hold-out")
    ap.add_argument("--check", help="посчитать Cohen kappa по разметке")
    ap.add_argument("--split", help="отделить hold-out по событиям")
    args = ap.parse_args(argv)
    if args.template and args.out:
        tpl = annotation_template(args.template, args.out)
        print(f"[DA] Шаблон разметки: {args.out} ({len(tpl)} строк)")
    if args.check:
        g = load_golden(args.check)
        rep = kappa_report(g)
        print(f"[DA] Cohen kappa = {rep['kappa'] if rep['kappa'] == rep['kappa'] else 'н/д'} "
              f"на {rep['n_pairs']} парах. Статус: {rep['status']}")
    if args.split and args.out:
        g = load_golden(args.split)
        res = save_golden_and_holdout(g, args.out)
        print(f"[DA] golden_set.csv + holdout_set.csv ({res['n_holdout']} пар hold-out) в {args.out}")
    if not (args.template or args.check or args.split):
        ap.error("укажите --template/--check/--split")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
