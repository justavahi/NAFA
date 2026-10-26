# -*- coding: utf-8 -*-
"""WP-D8 (P1): калибровка весов FM-13 по золотому набору.

Метод (справочник 8.1, FM-18):
  1. Перебор весов по симплексу с шагом 0.05 (сумма = 1).
  2. Максимум среднего NDCG@5; штраф lambda·||w - w0||² к стартовым весам.
  3. Проверка leave-one-archetype-out (LOAO).
  4. Бутстрэп 1000 повторов -> среднее и доверительный интервал.
  5. Сравнение с базовой линией FM-24 и абляции по членам.

Вход — base_components.csv: client_id, event_id, archetype, fresh, rel, imp,
urg, nov, cred, pers (компоненты скора, посчитанные вне перебора) + golden
разметка relevance 0..3. Вывод: лучшие веса -> config/weights.yaml.
"""
from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .golden_set import relevance_to_int

SIMPLEX_STEP = 0.05


def simplex_grid(step: float = SIMPLEX_STEP) -> list[tuple[float, ...]]:
    """Все комбинации из 5 неотрицательных весов с суммой 1."""
    vals = np.arange(0.0, 1.0 + 1e-9, step)
    out = []
    for w in itertools.product(vals, repeat=5):
        if abs(sum(w) - 1.0) < 1e-9:
            out.append(w)
    return out


def mean_ndcg(components: pd.DataFrame, golden: pd.DataFrame,
              weights: tuple[float, ...], k: int = 5) -> float:
    """Средний NDCG@5 при данных весах: Score = Fresh·(Σw·компонент)·(0.5+0.5·Pers)."""
    comp = components.copy()
    w = dict(zip(["Rel", "Imp", "Urg", "Nov", "Cred"], weights))
    comp["score"] = (comp["fresh"] *
                     (w["Rel"] * comp["rel"] + w["Imp"] * comp["imp"]
                      + w["Urg"] * comp["urg"] + w["Nov"] * comp["nov"]
                      + w["Cred"] * comp["cred"]) *
                     (0.5 + 0.5 * comp["pers"]))
    rel_map = dict(zip(golden["client_id"].astype(str) + "|" +
                       golden["event_id"].astype(str),
                       relevance_to_int(golden["relevance"])))
    ndcgs = []
    ranked = comp.sort_values(["client_id", "score"], ascending=[True, False])
    for client, grp in ranked.groupby("client_id"):
        top = grp.head(k)
        rels = [rel_map.get(f"{client}|{e}", 0.0) for e in top["event_id"]]
        dcg = sum((2.0 ** r - 1.0) / math.log2(i + 2) for i, r in enumerate(rels))
        ideal_rels = sorted((rel_map.get(f"{client}|{e}", 0.0)
                             for e in golden.loc[
                                 golden["client_id"].astype(str) == str(client),
                                 "event_id"]), reverse=True)[:k]
        idcg = sum((2.0 ** r - 1.0) / math.log2(i + 2)
                   for i, r in enumerate(ideal_rels))
        if idcg > 0:
            ndcgs.append(dcg / idcg)
    return float(np.mean(ndcgs)) if ndcgs else 0.0


def calibrate(components: pd.DataFrame, golden: pd.DataFrame,
              w0: tuple[float, ...] = (0.40, 0.25, 0.10, 0.10, 0.15),
              lambda_reg: float = 0.01, step: float = SIMPLEX_STEP) -> dict:
    """Перебор по симплексу + регуляризация. Возвращает лучшие веса и отчёт."""
    best = {"ndcg": -1.0, "weights": None}
    for w in simplex_grid(step):
        ndcg = mean_ndcg(components, golden, w)
        penalty = lambda_reg * sum((a - b) ** 2 for a, b in zip(w, w0))
        obj = ndcg - penalty
        if obj > best["ndcg"]:
            best = {"ndcg": obj, "raw_ndcg": ndcg, "weights": w}
    return best


def leave_one_archetype_out(components: pd.DataFrame, golden: pd.DataFrame,
                            lambda_reg: float = 0.01,
                            step: float = SIMPLEX_STEP) -> dict:
    """LOAO: калибруем без архетипа X, проверяем NDCG на X."""
    if "archetype" not in components.columns:
        return {"status": "нет колонки archetype — LOAO недоступен"}
    archs = components["archetype"].dropna().unique()
    report = {}
    for a in archs:
        mask = components["archetype"] != a
        if mask.sum() < 10 or (~mask).sum() < 5:
            continue
        best = calibrate(components[mask], golden, lambda_reg=lambda_reg,
                         step=step)
        held = mean_ndcg(components[~mask], golden, best["weights"])
        report[str(a)] = {"best_weights": best["weights"], "ndcg_holdout": held}
    return report


def bootstrap_ci(components: pd.DataFrame, golden: pd.DataFrame,
                 weights: tuple[float, ...], iters: int = 1000,
                 seed: int = 42) -> dict:
    """Доверительный интервал NDCG@5 бутстрэпом по клиентам."""
    rng = np.random.default_rng(seed)
    clients = components["client_id"].unique()
    vals = []
    for _ in range(iters):
        sample = rng.choice(clients, size=len(clients), replace=True)
        comp = pd.concat([components[components["client_id"] == c] for c in sample])
        vals.append(mean_ndcg(comp, golden, weights))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return {"mean": float(np.mean(vals)), "ci95": [float(lo), float(hi)]}


def run_calibration(components_csv: str | Path, golden_csv: str | Path,
                    out_dir: str | Path) -> dict:
    """WP-D8 целиком; результат — отчёт + предложение весов для weights.yaml."""
    comp = pd.read_csv(components_csv, encoding="utf-8-sig")
    golden = pd.read_csv(golden_csv, encoding="utf-8-sig")
    best = calibrate(comp, golden)
    loao = leave_one_archetype_out(comp, golden)
    ci = bootstrap_ci(comp, golden, best["weights"])
    base_w = {"IndustryMatch": 0.35, "ScaleFactor": 0.15,
              "ProductRelevance": 0.30, "SupplyChainRisk": 0.20}
    out = Path(out_dir) / "calibration"
    out.mkdir(parents=True, exist_ok=True)
    loao_is_table = isinstance(loao, dict) and loao and \
        all(isinstance(v, dict) and "best_weights" in v for v in loao.values())
    if loao_is_table:
        loao_lines = [f"| {a} | {r['best_weights']} | {r['ndcg_holdout']:.4f} |"
                      for a, r in loao.items()]
    else:
        loao_lines = [f"- {loao.get('status', 'недоступен')}" if isinstance(loao, dict)
                      else str(loao)]
    lines = [
        "# WP-D8. Калибровка весов FM-13", "",
        f"Лучшие веса (Rel, Imp, Urg, Nov, Cred): **{best['weights']}**",
        f"NDCG@5 с лучшими весами: {best.get('raw_ndcg', float('nan')):.4f}", "",
        f"Бутстрэп (1000): mean={ci['mean']:.4f}, "
        f"CI95=[{ci['ci95'][0]:.4f}, {ci['ci95'][1]:.4f}]", "",
        "## Leave-one-archetype-out", "",
        "| архетип исключён | веса | NDCG@5 на исключённом |", "|---|---|---|",
        *loao_lines, "",
        "Сравнить с FM-24 (базовой линией) и вписать итог в config/weights.yaml. "
        "После просмотра hold-out параметры НЕ менять (правило ТЗ 4.4).", "",
        f"Веса базовой линии FM-24: {base_w}",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return {"best": best, "loao": loao, "bootstrap": ci,
            "report": str(out / "report.md")}


def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="WP-D8: калибровка весов FM-13")
    ap.add_argument("--components", required=True, help="base_components.csv")
    ap.add_argument("--golden", required=True, help="golden_set.csv")
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args(argv)
    res = run_calibration(args.components, args.golden, args.out_dir)
    print(f"[DA] Лучшие веса: {res['best']['weights']}; отчёт: {res['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
