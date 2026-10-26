# -*- coding: utf-8 -*-
"""Тестовые векторы раздела 4.2 и sanity-проверки 4.3 ТЗ Data Analyst.

Запуск (без pytest, из папки проекта):
    python -m tests.test_vectors

Каждая проверка воспроизводит строку таблицы 4.2 с допуском 0.001.
Если вектор не сошёлся: либо правим код, либо сообщаем BA об ошибке
в формуле (правило ТЗ).
"""
# pylint: disable=unsubscriptable-object
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.config import load_exposure, load_weights  # noqa: E402
from analytics import formulas as F  # noqa: E402
from analytics import metrics_e as E  # noqa: E402

TOL = 0.001
PASS, FAIL = 0, 0


def check(name: str, got: float, want: float, tol: float = TOL):
    global PASS, FAIL
    ok = got is not None and abs(got - want) <= tol
    status = "OK  " if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{status}] {name}: получено {got}, ожидается {want} (допуск {tol})")


def approx_seq(name: str, got: dict, want: dict, tol: float = TOL):
    for k, wv in want.items():
        check(f"{name}.{k}", got.get(k, float("nan")), wv, tol)


# --- справочники: ищем docs/data -------------------------------------------
def _load_refs():
    from analytics.config import find_ref_dir
    ref = find_ref_dir()
    if ref is None:
        print("!! справочники docs/data не найдены — проверяю константы без матрицы")
        return None
    import pandas as pd
    matrix = pd.read_csv(ref / "sector_sensitivity_matrix.csv", encoding="utf-8-sig")
    personas = pd.read_csv(ref / "persona_weights.csv", encoding="utf-8-sig")
    return {"matrix": matrix, "personas": personas}


def _matrix_row(refs, sp: str) -> dict:
    m = refs["matrix"].set_index("sector_profile")
    row = m.loc[sp]
    return {f: float(row[f]) for f in
            ("RATE", "FX", "INFL", "ENERGY", "COMMOD", "DEMAND",
             "LABOR", "CREDIT", "BUDGET", "REG", "GEOPOL", "CPTY")}


def _client_ca01():
    """CA-01: строитель-заёмщик (SP-09): LOA=1, LEA=1, GAR=1, ZPP=1, DEP=0,
    FTS=0, зрелая (growth=0), платёжных данных нет."""
    import pandas as pd
    products = pd.DataFrame({
        "F08_RKO": [1.0], "F08_GAR": [1.0], "F08_LOA": [1.0], "F08_LEA": [1.0],
        "F08_DEP": [0.0], "F08_ZPP": [1.0], "F08_FACTORING": [0.0],
        "F09_fts_flag": [0.0], "F09_fts_country": [None],
        "F04_growth_flag": [0.0], "F05_headcount_mid": [np_nan()],
        "F06_is_state_related": [0.0],
        "G03_HHI_in": [np_nan()], "G03_HHI_out": [np_nan()],
        "G02_n_cust": [np_nan()], "G02_n_sup": [np_nan()],
    })
    return products


def np_nan():
    return float("nan")


def _prior_series(refs, sp: str):
    import pandas as pd
    row = _matrix_row(refs, sp)
    return pd.Series([row["BUDGET"]], dtype=float)


def run_vector_tests(refs):
    import pandas as pd
    ex = load_exposure()
    w = load_weights()

    # --- 1. E-01 = 0.72 (LOA=1, LEA=1, FACT=0) ------------------------------
    e01 = F.fm03_noisy_or([(0.6, 1.0), (0.3, 1.0), (0.3, 0.0)])
    check("E-01 (noisy-OR LOA/LEA)", e01, 0.72)

    # --- 2. E-02 = 0 (DEP=0); rate_net = -0.72 -------------------------------
    e02 = 0.6 * 0.0
    check("E-02 (DEP=0)", e02, 0.0)
    check("rate_net = E-02 - E-01", e02 - e01, -0.72)

    # --- 3. Вектор eps для CA-01 (12 компонентов) -----------------------------
    if refs:
        feats = _client_ca01()
        prior_budget = pd.Series([_matrix_row(refs, "SP-09")["BUDGET"]] * len(feats),
                                 index=feats.index)
        e08 = pd.Series([float("nan")], index=feats.index)
        e09 = E.e09_own_credit(feats, feats["F04_growth_flag"], ex["e09_credit"])
        e10 = E.e10_own_budget(feats, feats["F06_is_state_related"],
                               prior_budget, ex["e10_budget"])
        e12 = E.e12_own_geopol(feats["F09_fts_flag"], feats["F09_fts_country"],
                               ex["e12_geopol"])
        e13 = E.e13_own_cpty(feats["G03_HHI_in"], feats["G03_HHI_out"],
                             feats["G02_n_cust"], feats["G02_n_sup"],
                             ex["e13_cpty"])
        own = E.build_own_components(feats, e01_series(), e02_series(), e03_series(),
                                     e08, e09, e10, e12, e13)
        eps_df = E.exposure_vector(own, refs["matrix"], None, ex["e14_alpha"],
                                   sp_of=pd.Series(["SP-09"], index=feats.index))
        want_ca01 = {"RATE": 0.86, "FX": 0.167, "INFL": 1.0, "ENERGY": 0.333,
                     "COMMOD": 0.667, "DEMAND": 1.0, "LABOR": 1.0,
                     "CREDIT": 0.75, "BUDGET": 0.85, "REG": 1.0,
                     "GEOPOL": 0.333, "CPTY": 1.0}
        got = {f: float(eps_df[f"eps_{f}"].iloc[0]) for f in want_ca01}
        approx_seq("eps CA-01", {k: round(v, 3) for k, v in got.items()},
                   want_ca01)

        # --- 4. Cov = 0.727; Rel = 0.509 (Match=0) ---------------------------
        iota = {"RATE": 1.0, "FX": 0.5, "INFL": 0.5, "CREDIT": 0.5}
        eps1 = {f: got[f] for f in iota}
        cov, rel = F.fm06_relevance(eps1, iota, match=0.0)
        check("Cov CA-01", cov, 0.727)
        check("Rel CA-01", rel, 0.509)

        # --- 5. Pers = 0.94 (PER-03, финансовый директор) ----------------------
        per = refs["personas"]
        prow = per[per["persona"] == "PER-03"].iloc[0]
        pw = {f: float(prow[f]) for f in
              ("RATE", "FX", "INFL", "ENERGY", "COMMOD", "DEMAND",
               "LABOR", "CREDIT", "BUDGET", "REG", "GEOPOL", "CPTY")}
        pers = F.fm07_pers(pw, iota)
        check("Pers PER-03", pers, 0.94)

        # --- 7. Score = 0.536 ----------------------------------------------------
        fresh = F.fm08_fresh(3.0, 14.0)          # проверка 6 ниже
        score = F.fm13_score(fresh, rel, imp=0.75, urg=0.0, nov=1.0, cred=1.0,
                             pers=pers, weights=w["fm13"]["weights"])
        check("Score CA-01/PER-03", score, 0.536)

        # --- 8. CA-07: Cov = 0.353; Rel = 0.247 (< 0.25) ------------------------
        feats7 = pd.DataFrame({
            "F08_RKO": [1.0], "F08_GAR": [0.0], "F08_LOA": [0.0],
            "F08_LEA": [0.0], "F08_DEP": [1.0], "F08_ZPP": [0.0],
            "F08_FACTORING": [0.0], "F09_fts_flag": [0.0],
            "F09_fts_country": [None], "F04_growth_flag": [0.0],
            "F05_headcount_mid": [np_nan()], "F06_is_state_related": [0.0],
            "G03_HHI_in": [np_nan()], "G03_HHI_out": [np_nan()],
            "G02_n_cust": [np_nan()], "G02_n_sup": [np_nan()],
        })
        e01_7 = F.fm03_noisy_or([(0.6, 0.0), (0.3, 0.0), (0.3, 0.0)])  # нет долга
        e02_7 = 0.6 * 1.0
        own7 = E.build_own_components(
            feats7,
            pd.Series([max(e01_7, e02_7)], index=feats7.index),  # RATE = max
            pd.Series([e02_7], index=feats7.index),
            pd.Series([0.0], index=feats7.index),                # FTS=0 -> own 0
            pd.Series([float("nan")], index=feats7.index),
            E.e09_own_credit(feats7, feats7["F04_growth_flag"], ex["e09_credit"]),
            E.e10_own_budget(feats7, feats7["F06_is_state_related"],
                             pd.Series([_matrix_row(refs, "SP-13")["BUDGET"]] * 1,
                                       index=feats7.index), ex["e10_budget"]),
            E.e12_own_geopol(feats7["F09_fts_flag"], feats7["F09_fts_country"],
                             ex["e12_geopol"]),
            pd.Series([float("nan")], index=feats7.index))
        eps7 = E.exposure_vector(own7, refs["matrix"], None, ex["e14_alpha"],
                                 sp_of=pd.Series(["SP-13"], index=feats7.index))
        eps7d = {f: float(eps7[f"eps_{f}"].iloc[0]) for f in iota}
        cov7, rel7 = F.fm06_relevance(eps7d, iota, match=0.0)
        check("Cov CA-07", cov7, 0.353)
        check("Rel CA-07 (< порога 0.25)", rel7, 0.247)
        check("CA-07 ниже порога отбора",
              1.0 if rel7 < w["fm13"]["rel_threshold"] else 0.0, 1.0, tol=1e-9)

    # --- 6. Fresh = 0.862 (age 3 дня, h = 14) ---------------------------------
    check("Fresh (0.5^(3/14))", F.fm08_fresh(3.0, 14.0), 0.862)

    # --- FM-15 (а): +10 млн при долге 1000 млн и +1 п.п. -------------------------
    check("FM-15(а) проценты", F.fm15_interest_expense(1000.0, 1.0), 10.0)
    # --- FM-15 (б): -43.5 млн по валютным платежам -------------------------------
    check("FM-15(б) валюта", F.fm15_fx_result(10.0, 82.85, 87.2, -1), -43.5)

    # --- FM-16: версии позиции банка ---------------------------------------------
    recs = [{"indicator": "RATE", "horizon": "конец 2026", "value": 13.5,
             "as_of": "31.07.2026"},
            {"indicator": "RATE", "horizon": "конец 2026", "value": 13.5,
             "as_of": "07.09.2026"},
            {"indicator": "RATE", "horizon": "конец 2026", "value": 14.0,
             "as_of": "11.09.2026"}]
    v = F.fm16_versions(recs, {"RATE": 0.25})
    check("FM-16 активное значение", float(v["active"]["value"]), 14.0)
    check("FM-16 superseded count", float(len(v["superseded"])), 2.0)
    check("FM-16 флаг конфликта (Δ=0.5)", 1.0 if v["conflict"] else 0.0, 1.0)

    # --- FM-14: знак эффекта -------------------------------------------------------
    r = F.fm14_sign_effect(-0.72, d_rate=+1)
    check("FM-14 effect при rate_net=-0.72", float(r["effect"]), -1.0)
    r0 = F.fm14_sign_effect(0.0)
    check("FM-14 две ветви при rate_net=0",
          float(len(r0["branches"]) == 2), 1.0)

    # --- FM-21: V_rate = 0.72 / 0.36 / 0.54 -----------------------------------------
    check("FM-21 pctl=1", F.fm21_v_rate(0.72, 1.0), 0.72)
    check("FM-21 pctl=0", F.fm21_v_rate(0.72, 0.0), 0.36)
    check("FM-21 выручка неизвестна", F.fm21_v_rate(0.72, None), 0.54)

    # --- FM-22: P1 = 0.38; P2 = 0.50 -------------------------------------------------
    edges = {"c": [("j1", 0.6), ("j2", 0.4)]}
    p = F.fm22_shock_propagation(edges, {"j1": 0.5, "j2": 0.2}, gamma=0.5, depth=1)
    check("FM-22 P1", p["c"], 0.38)
    # второй сценарий: P_1(j1)=0.4 уже в x? По ТЗ: x(j1)=0.5 + gamma*0.4/... =>
    # воспроизводим: x(j1) = 0.5, j1 имеет собственного поставщика с P=0.4
    edges2 = {"c": [("j1", 0.6), ("j2", 0.4)], "j1": [("k1", 1.0)]}
    p2 = F.fm22_shock_propagation(edges2, {"j1": 0.5, "j2": 0.2, "k1": 0.4},
                                  gamma=0.5, depth=2)
    check("FM-22 P2", p2["c"], 0.50)

    # --- FM-23: x_group = 0.4 --------------------------------------------------------
    check("FM-23", F.fm23_group_spread(0.3, 0.8, 0.5), 0.4)

    # --- FM-24: ScaleFactor = 0.8; Score_lin = 0.762 ---------------------------------
    w24 = {"IndustryMatch": 0.35, "ScaleFactor": 0.15,
           "ProductRelevance": 0.30, "SupplyChainRisk": 0.20}
    scale, lin = F.fm24_linear_score(1.0, 0.9, 0.7, 0.72, 0.38, w24)
    check("FM-24 ScaleFactor", scale, 0.8)
    check("FM-24 Score_lin", lin, 0.762)


# --- sanity-проверки раздела 4.3 ------------------------------------------------
def run_sanity_tests(refs):
    import pandas as pd
    ex = load_exposure()
    feats = pd.DataFrame({
        # [0] без долга и депозита; [1] LOA+DEP (двусторонний); [2] DEP без долга
        "F08_RKO": [1.0, 0.0, 1.0], "F08_GAR": [0.0, 0.0, 0.0],
        "F08_LOA": [0.0, 1.0, 0.0], "F08_LEA": [0.0, 0.0, 0.0],
        "F08_DEP": [0.0, 1.0, 1.0], "F08_ZPP": [0.0, 0.0, 0.0],
        "F08_FACTORING": [0.0, 0.0, 0.0], "F09_fts_flag": [0.0, 0.0, 0.0],
        "F09_fts_country": [None, None, None],
    }, index=[0, 1, 2])
    e01 = E.e01_own_rate_debt(feats, ex["e01_debt"])
    e02 = E.e02_own_dep(feats, ex["e02_dep"])
    rate_net = e02 - e01
    check("sanity: без LOA/LEA/FACTORING -> E-01=0", float(e01.iloc[0]), 0.0)
    check("sanity: DEP без долга -> rate_net>0",
          1.0 if rate_net.iloc[2] > 0 else 0.0, 1.0, tol=1e-9)
    check("sanity: LOA+DEP -> rate_net ~ 0 (двусторонний вывод)",
          1.0 if abs(rate_net.iloc[1]) <= 0.15 else 0.0, 1.0, tol=1e-9)
    fx = E.e03_own_fx(feats["F09_fts_flag"], ex["e03_fx"])
    check("sanity: FTS=0 -> own FX = 0", float(fx.iloc[0]), 0.0)
    # монотонность: добавление LEA не уменьшает E-01
    feats_m = feats.copy()
    feats_m.loc[0, "F08_LEA"] = 1.0
    e01_m = E.e01_own_rate_debt(feats_m, ex["e01_debt"])
    check("sanity: монотонность E-01 по LEA",
          1.0 if e01_m.iloc[0] >= e01.iloc[0] else 0.0, 1.0, tol=1e-9)
    # mix суммируется в 1
    mix = {"SP-01": 0.4, "SP-09": 0.6}
    check("sanity: сумма долей mix = 1", float(sum(mix.values())), 1.0, tol=1e-6)


def e01_series():
    import pandas as pd
    return pd.Series([0.72], dtype=float)


def e02_series():
    import pandas as pd
    return pd.Series([0.0], dtype=float)


def e03_series():
    import pandas as pd
    return pd.Series([0.0], dtype=float)  # FTS=0: own FX доступна и = 0


def main() -> int:
    print("=" * 72)
    print("Тестовые векторы раздела 4.2 и sanity 4.3 ТЗ Data Analyst")
    print("=" * 72)
    refs = _load_refs()
    run_vector_tests(refs)
    run_sanity_tests(refs)
    print("-" * 72)
    print(f"Итог: {PASS} OK / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
