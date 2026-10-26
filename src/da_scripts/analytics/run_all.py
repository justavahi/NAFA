# -*- coding: utf-8 -*-
"""Оркестратор контура Data Analyst (WP-D1..WP-D16) — одна команда.

Демо без реальных данных (сгенерирует синтетику и прогонит всё):
    python -m analytics.run_all --demo

На реальных данных (положите df_Uid_data_hack.csv и платежи в папку data_raw):
    python -m analytics.run_all --data-dir data_raw

Результаты: out/profiling/, out/dictionaries/, out/features/,
out/charts/, out/quality/, out/calibration/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from . import charts, formulas as F
from .calibrate import run_calibration
from .columns import CLIENT_COLUMNS
from .config import find_ref_dir, load_exposure, load_weights
from .data_io import (dedup_clients, load_clients, load_link, load_payments,
                      load_references)
from .dictionaries import run_dictionaries
from .golden_set import kappa_report, relevance_to_int
from .graphs import build_graphs
from .metrics_e import compute_exposures
from .metrics_f import build_features
from .metrics_g import build_payment_features
from .profiling import run_profiling


def _log(msg: str):
    print(f"[DA] {msg}")


def run_pipeline(data_dir: str | Path, out_dir: str | Path,
                 ref_dir: str | Path | None = None,
                 do_charts: bool = True, do_quality: bool = True) -> dict:
    """Полный прогон. Возвращает словарь путей к артефактам."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_dir = ref_dir or find_ref_dir()
    refs = load_references(ref_dir)
    if ref_dir:
        _log(f"Справочники: {ref_dir}")
    else:
        _log("ВНИМАНИЕ: справочники docs/data не найдены (матрица/ОКВЭД/персоны)")

    # --- WP-D1: профилирование -------------------------------------------------
    clients, clients_path = load_clients(data_dir)
    pay, pay_path = load_payments(data_dir)
    link, link_path = load_link(data_dir)
    _log(f"Клиенты: {len(clients)} строк ({clients_path})")
    _log(f"Платежи: {'нет файла' if pay is None else f'{len(pay)} строк ({pay_path})'}")
    prof = run_profiling(clients, pay, out_dir)
    _log(f"WP-D1 готов: {prof['report']}")
    for q in prof["questions"][:5]:
        _log(f"  вопрос BA/OQ: {q}")

    # дубли/пустые SYNTHETIC_U уже видны WP-D1; дальше считаем на уникальных ID
    clients = dedup_clients(clients)
    if pay is not None and link is not None:
        _log(f"Платежи и связи на {len(clients)} уникальных клиентах")

    # --- WP-D2: справочники -------------------------------------------------------
    dict_res = run_dictionaries(clients, refs["okved_map"], out_dir)
    _log(f"WP-D2 готов: {dict_res['report']}")
    if dict_res["coverage"].get("uncovered"):
        _log(f"  ОКВЭД без SP ({len(dict_res['coverage']['uncovered'])}): "
             f"{dict_res['coverage']['uncovered'][:10]}...")

    # --- WP-D3: метрики F ------------------------------------------------------------
    weights_cfg = load_weights()
    features = build_features(
        clients, okved_map=refs["okved_map"], link=link,
        country_currency=dict_res["country_currency"],
        completeness_weights=weights_cfg["f13_completeness"])
    features.insert(0, "SYNTHETIC_U", clients["SYNTHETIC_U"].values)
    # сырые поля рядом с признаками — нужны графикам CH-01/CH-03 и паспортам
    for raw in ("REVENUE_COMPANY", "MAINOKVED_CODE", "REGION",
                "AMOUNT_WORKERS_FNS_GRADATION",
                "AMOUNT_WORKERS_SPARK_GRADATION", "ECO_MARK_LABEL",
                "COMPANY_AGE_YEARS_GRADATION", "CL_TYPE"):
        features[raw] = clients[raw].values
    feat_path = out_dir / "features" / "client_features.csv"
    feat_path.parent.mkdir(exist_ok=True)
    features.to_csv(feat_path, index=False, encoding="utf-8-sig")
    _log(f"WP-D3 готов: {feat_path} ({len(features.columns)} колонок)")

    # --- WP-D4: метрики G ---------------------------------------------------------------
    gres = build_payment_features(pay, clients, features, link)
    g01 = gres["g01"].reset_index()
    g01 = g01.join(gres["g02"].reset_index(drop=True)) \
             .join(gres["g03"].reset_index(drop=True)) \
             .join(gres["g10"].reset_index(drop=True))
    g01["G07_intragroup"] = gres["g07"].values
    g_path = out_dir / "features" / "payment_features.csv"
    g01.to_csv(g_path, index=False, encoding="utf-8-sig")
    mix_sup = gres["mix_sup"]
    # G-метрики нужны экспозициям (E-13/CPTY): вливаем их в features до WP-D5
    if pay is not None:
        ids = clients["SYNTHETIC_U"]
        for src, cols in ((gres["g01"], ("G01_in", "G01_out", "G01_net_ratio")),
                          (gres["g02"], ("G02_n_cust", "G02_n_sup")),
                          (gres["g03"], ("G03_HHI_in", "G03_HHI_out"))):
            for c in cols:
                if c in src.columns:
                    features[c] = src[c].reindex(ids).to_numpy()
    _log(f"WP-D4 готов: {g_path}")

    # --- WP-D5: экспозиции ----------------------------------------------------------------
    exp = compute_exposures(features, clients, refs["matrix"], mix_sup,
                            load_exposure())
    eps = exp["eps"]
    eps_out = eps.copy() if eps is not None else pd.DataFrame()
    eps_out.insert(0, "SYNTHETIC_U", features["SYNTHETIC_U"].values)
    eps_out["E15_rate_net"] = exp["e15_rate_net"].values
    e_path = out_dir / "features" / "exposures.csv"
    eps_out.to_csv(e_path, index=False, encoding="utf-8-sig")
    _log(f"WP-D5 готов: {e_path}")

    # --- WP-D16: графы ----------------------------------------------------------------------
    graphs = build_graphs(clients, pay, link, refs["matrix"], features)
    g2 = graphs["gr02_stats"]
    _log(f"WP-D16 готов: GR-01 компонент={graphs['gr01']['n_components']}, "
         f"макс. размер={graphs['gr01']['max_component_size']}; "
         f"GR-02 рёбра={g2['n_edges']}, покрытие SP={g2['sp_coverage']}")
    graphs["gr02_edges"].to_csv(out_dir / "features" / "gr02_edges.csv",
                                index=False, encoding="utf-8-sig")

    ctx = {"out_dir": out_dir, "matrix": refs["matrix"],
           "features_csv": str(feat_path), "pred": None,
           "rate_fact_csv": None, "rate_forecast_csv": None}

    # --- WP-D11: графики -----------------------------------------------------------------------
    if do_charts:
        ctx["eps"] = {row["SYNTHETIC_U"]:
                      {f.replace("eps_", "", 1): row[f]
                       for f in eps_out.columns
                       if f.startswith("eps_") and pd.notna(row[f])}
                      for _, row in eps_out.iterrows()} if len(eps_out) else {}
        # два демо-клиента для радара: с макс и мин eps_RATE
        if ctx["eps"]:
            sorted_ids = sorted(ctx["eps"], key=lambda u: ctx["eps"][u].get("RATE", 0))
            ctx["radar_clients"] = [sorted_ids[-1], sorted_ids[0]]
        if (data_dir / "rate_fact.csv").exists():
            ctx["rate_fact_csv"] = str(data_dir / "rate_fact.csv")
            ctx["rate_forecast_csv"] = str(data_dir / "rate_forecast.csv")
        # CH-14: водопад по тестовому примеру CA-01 (справочник 8.2)
        w13 = weights_cfg["fm13"]["weights"]
        fresh = F.fm08_fresh(3.0, 14.0)
        pers = 0.94
        inner = (w13["Rel"] * 0.5091 + w13["Imp"] * 0.75 + w13["Urg"] * 0.0
                 + w13["Nov"] * 1.0 + w13["Cred"] * 1.0)
        score = fresh * inner * (0.5 + 0.5 * pers)
        ctx["wf_components"] = {"Rel": w13["Rel"] * 0.5091, "Imp": w13["Imp"] * 0.75,
                                "Urg": 0.0, "Nov": w13["Nov"] * 1.0,
                                "Cred": w13["Cred"] * 1.0}
        ctx["wf_multipliers"] = {"Fresh": fresh, "Роль": 0.5 + 0.5 * pers}
        ctx["wf_score"] = score
        # CH-23: демо-распределение Rel по отраслям из матрицы
        if refs["matrix"] is not None:
            m = refs["matrix"]
            rows = []
            for _, r in m.iterrows():
                base = float(r["RATE"]) / 3.0
                for v in rng_rels(base):
                    rows.append({"sector_profile": r["sector_profile"], "rel": v})
            rel_path = out_dir / "rel_by_sector.csv"
            pd.DataFrame(rows).to_csv(rel_path, index=False)
            ctx["rel_csv"] = str(rel_path)
        # CH-15/CH-25: демо-ленты и воронка
        pred = demo_predictions(clients, refs, ctx.get("eps", {}))
        ctx["pred"] = pred
        ctx["stages"] = {"Входящий поток": 500, "После дедупликации": 380,
                         "Выше порога Rel": 190, "Топ-5 клиентов": 150,
                         "Открыто пользователем": 47}
        res = charts.build_charts(ctx, priority="P0")
        for ch, msg in res.items():
            _log(f"  {ch}: {msg}")
        res1 = charts.build_charts(ctx, priority="P1")
        for ch, msg in res1.items():
            _log(f"  {ch}: {msg}")

    # --- WP-D9: качество (демо-прогон на синтетических лентах) ---------------------------------
    if do_quality and ctx.get("pred") is not None:
        from .quality import run_quality
        gold_dir = out_dir / "quality"
        gold_dir.mkdir(parents=True, exist_ok=True)
        pred = ctx["pred"]
        pred.to_csv(out_dir / "predictions_demo.csv", index=False,
                    encoding="utf-8-sig")
        # золотая разметка: релевантность берём из Rel-скорing (демо)
        golden = pred.copy()
        golden["relevance"] = (golden["score"].clip(0, 1) * 3).round()
        golden["archetype"] = "demo"
        golden = golden.drop_duplicates(["client_id", "event_id"])
        golden_path = out_dir / "golden_demo.csv"
        golden.to_csv(golden_path, index=False, encoding="utf-8-sig")
        q = run_quality(golden_path, out_dir / "predictions_demo.csv", out_dir)
        _log(f"WP-D9 демо-прогон: NDCG@5={q['Q04_NDCG@5']}, "
             f"P@5={q['Q05_Precision@5']} (демо-разметка, не для сдачи)")

    artifacts = {"profiling": prof["report"], "dictionaries": dict_res["report"],
                 "features": str(feat_path), "payments": str(g_path),
                 "exposures": str(e_path)}
    return artifacts


def rng_rels(base: float):
    """Демо-выборка релевантностей вокруг отраслевого prior."""
    import numpy as np
    rng = np.random.default_rng(abs(hash(str(base))) % (2 ** 32))
    return (base * 0.7 + rng.beta(2, 3, size=12) * 0.6).clip(0.02, 0.98)


def demo_predictions(clients: pd.DataFrame, refs: dict,
                     eps: dict[str, dict]) -> pd.DataFrame:
    """Демо-ленты: топ-5 событий ET-01 по Score для 8 клиентов."""
    w = load_weights()
    rows = []
    demo_clients = list(eps.keys())[:8]
    events = {
        "EV-01": {"iota": {"RATE": 1.0, "FX": 0.5, "INFL": 0.5, "CREDIT": 0.5},
                  "fresh": F.fm08_fresh(3, 14), "imp": 0.75, "urg": 0.0,
                  "nov": 1.0, "cred": 1.0},
        "EV-02": {"iota": {"FX": 1.0, "GEOPOL": 0.5, "COMMOD": 0.5},
                  "fresh": F.fm08_fresh(10, 3), "imp": 0.5, "urg": 0.0,
                  "nov": 0.6, "cred": 0.85},
        "EV-03": {"iota": {"INFL": 1.0, "ENERGY": 0.5},
                  "fresh": F.fm08_fresh(1, 3), "imp": 0.6, "urg": 0.2,
                  "nov": 0.9, "cred": 1.0},
        "EV-04": {"iota": {"DEMAND": 1.0, "CREDIT": 0.5},
                  "fresh": F.fm08_fresh(20, 30), "imp": 0.4, "urg": 0.0,
                  "nov": 0.5, "cred": 0.6},
        "EV-05": {"iota": {"LABOR": 1.0, "REG": 0.5},
                  "fresh": F.fm08_fresh(5, 14), "imp": 0.45, "urg": 0.0,
                  "nov": 0.8, "cred": 0.85},
        "EV-06": {"iota": {"BUDGET": 1.0, "CREDIT": 0.5},
                  "fresh": F.fm08_fresh(2, 14), "imp": 0.55, "urg": 0.1,
                  "nov": 0.7, "cred": 0.9},
    }
    for u in demo_clients:
        evs = []
        for ev_id, ev in events.items():
            cov, rel = F.fm06_relevance(eps[u], ev["iota"], match=0.0)
            score = F.fm13_score(ev["fresh"], rel, ev["imp"], ev["urg"],
                                 ev["nov"], ev["cred"], pers=0.8,
                                 weights=w["fm13"]["weights"])
            evs.append({"client_id": u, "event_id": ev_id, "score": score,
                        "rel": rel})
        evs = [e for e in evs if e["rel"] >= w["fm13"]["rel_threshold"]]
        evs.sort(key=lambda e: -e["score"])
        for rank, e in enumerate(evs[:w["fm13"]["top_k"]], start=1):
            rows.append({"client_id": u, "event_id": e["event_id"],
                         "score": e["score"], "rank": rank})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Контур Data Analyst NAFA")
    ap.add_argument("--data-dir", help="папка с df_Uid_data_hack.csv и платежами")
    ap.add_argument("--out-dir", default="out", help="куда писать результаты")
    ap.add_argument("--ref-dir", default=None, help="папка docs/data справочников")
    ap.add_argument("--demo", action="store_true",
                    help="сгенерировать синтетические данные и прогнать всё")
    args = ap.parse_args(argv)

    if args.demo:
        from examples.make_synthetic_demo import make_demo
        demo_dir = Path("demo_data")
        paths = make_demo(demo_dir)
        _log(f"Демо-данные созданы в {demo_dir.resolve()}")
        data_dir = demo_dir
    else:
        if not args.data_dir:
            ap.error("укажите --data-dir или --demo")
        data_dir = args.data_dir

    artifacts = run_pipeline(data_dir, args.out_dir, ref_dir=args.ref_dir)
    _log("Готово. Артефакты:")
    for k, v in artifacts.items():
        _log(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
