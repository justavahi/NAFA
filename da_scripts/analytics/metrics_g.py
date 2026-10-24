# -*- coding: utf-8 -*-
"""Метрики G-01..G-10 — платежи и контрагенты (WP-D4, P0: G-01..G-04).

Вход: payments DataFrame с колонками PAYER, BENEFICIARY, PAY_AMT, PAY_CNT
(имя файла в кейсе уточняется — WP-D1). Идентификаторы контрагентов
анонимны: сопоставление отраслей — по признакам (SP из данных клиентов).

Правило приёмки WP-D4: у клиентов без платежей поле ПУСТОЕ (NaN), а не ноль.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UNKNOWN = "UNKNOWN"  # контрагент не найден среди клиентов


def _clean(pay: pd.DataFrame) -> pd.DataFrame:
    out = pay.copy()
    out["PAY_AMT"] = pd.to_numeric(out["PAY_AMT"], errors="coerce")
    if "PAY_CNT" in out.columns:
        out["PAY_CNT"] = pd.to_numeric(out["PAY_CNT"], errors="coerce")
    return out.dropna(subset=["PAY_AMT"])


def _mix(pay_sub: pd.DataFrame, total: pd.Series, sector_of: pd.Series) -> pd.DataFrame:
    """Доли оборота по профилям SP контрагентов (G-04/G-05-подобный расчёт)."""
    rows = {}
    for client, grp in pay_sub.groupby("client", sort=False):
        tot = float(total.get(client, np.nan))
        if not np.isfinite(tot) or tot <= 0:
            continue
        mix = grp.groupby(grp["sp"].fillna(UNKNOWN))["PAY_AMT"].sum() / tot
        rows[client] = mix
    return pd.DataFrame(rows).T if rows else pd.DataFrame()


def g01_turnover(pay: pd.DataFrame, client_ids: pd.Index) -> pd.DataFrame:
    """G-01: обороты вход/выход и net_ratio = in / (in + out)."""
    pay = _clean(pay)
    idx = pd.Index(client_ids, name="SYNTHETIC_U")
    inn = pay.groupby("BENEFICIARY")["PAY_AMT"].sum().reindex(idx).rename("G01_in")
    out = pay.groupby("PAYER")["PAY_AMT"].sum().reindex(idx).rename("G01_out")
    df = pd.concat([inn, out], axis=1)
    total = df["G01_in"] + df["G01_out"]
    df["G01_net_ratio"] = (df["G01_in"] / total).where(total > 0)
    # у клиентов без платежей — пусто, не ноль; у клиентов с платежами
    # отсутствующая сторона оборота — честный ноль
    has_any = df[["G01_in", "G01_out"]].notna().any(axis=1)
    df.loc[has_any, ["G01_in", "G01_out"]] = df.loc[
        has_any, ["G01_in", "G01_out"]].fillna(0.0)
    df.loc[~has_any, :] = np.nan
    return df


def g02_counters(pay: pd.DataFrame, client_ids: pd.Index) -> pd.DataFrame:
    """G-02: число контрагентов — клиентов (платят c) и поставщиков (c платит)."""
    pay = _clean(pay)
    idx = pd.Index(client_ids, name="SYNTHETIC_U")
    n_cust = (pay.groupby("BENEFICIARY")["PAYER"].nunique()
              .reindex(idx).rename("G02_n_cust"))
    n_sup = (pay.groupby("PAYER")["BENEFICIARY"].nunique()
             .reindex(idx).rename("G02_n_sup"))
    df = pd.concat([n_cust, n_sup], axis=1)
    df.loc[df[["G02_n_cust", "G02_n_sup"]].isna().all(axis=1), :] = np.nan
    return df


def g03_hhi(pay: pd.DataFrame, client_ids: pd.Index) -> pd.DataFrame:
    """G-03: концентрация контрагентов HHI = Σ s_i² по входу и выходу."""
    pay = _clean(pay)
    idx = pd.Index(client_ids, name="SYNTHETIC_U")
    res = {c: np.nan for c in ("G03_HHI_in", "G03_HHI_out")}
    out = pd.DataFrame(res, index=idx)

    def side(group_col: str, target_col: str, colname: str):
        sums = pay.groupby([target_col, group_col])["PAY_AMT"].sum()
        if sums.empty:
            return
        totals = sums.groupby(level=0).transform("sum")
        shares = (sums / totals).reset_index()
        hhi = shares.groupby(target_col).apply(lambda s: (s["PAY_AMT"] ** 2).sum())
        out[colname] = hhi.reindex(idx)

    side("PAYER", "BENEFICIARY", "G03_HHI_in")        # кто платит клиенту
    side("BENEFICIARY", "PAYER", "G03_HHI_out")       # кому платит клиент
    return out


def g04_sector_mix(pay: pd.DataFrame, sp_by_id: pd.Series,
                   client_ids: pd.Index) -> tuple[pd.DataFrame, pd.DataFrame]:
    """G-04: отраслевой микс контрагентов.

    mix_cust[s] — доля входящего оборота от контрагентов профиля s;
    mix_sup[s]  — доля исходящего оборота к контрагентам профиля s.
    Контрагенты, которых нет среди клиентов, уходят в бакет UNKNOWN.
    """
    pay = _clean(pay)
    sp = sp_by_id.to_dict()

    pay = pay.assign(
        sp_in=pay["PAYER"].map(sp),
        sp_out=pay["BENEFICIARY"].map(sp),
    )
    idx = pd.Index(client_ids, name="SYNTHETIC_U")

    in_part = pay.rename(columns={"BENEFICIARY": "client"}).assign(sp=lambda d: d["sp_in"])
    out_part = pay.rename(columns={"PAYER": "client"}).assign(sp=lambda d: d["sp_out"])
    tot_in = in_part.groupby("client")["PAY_AMT"].sum().reindex(idx)
    tot_out = out_part.groupby("client")["PAY_AMT"].sum().reindex(idx)

    mix_cust = _mix(in_part.dropna(subset=["sp"]), tot_in, sp)
    mix_sup = _mix(out_part.dropna(subset=["sp"]), tot_out, sp)
    mix_cust = mix_cust.reindex(idx).fillna(0.0)
    mix_sup = mix_sup.reindex(idx).fillna(0.0)
    # у клиентов без платежей микс пустой
    empty = tot_in.isna() & tot_out.isna()
    mix_cust.loc[empty, :] = np.nan
    mix_sup.loc[empty, :] = np.nan
    return mix_cust, mix_sup


def g05_region_mix(pay: pd.DataFrame, region_by_id: pd.Series,
                   client_ids: pd.Index) -> tuple[pd.DataFrame, pd.DataFrame]:
    """G-05: региональный микс контрагентов (аналог G-04 по REGION)."""
    pay = _clean(pay)
    reg = region_by_id.to_dict()
    pay = pay.assign(
        rg_in=pay["PAYER"].map(reg),
        rg_out=pay["BENEFICIARY"].map(reg),
    )
    idx = pd.Index(client_ids, name="SYNTHETIC_U")
    in_part = pay.rename(columns={"BENEFICIARY": "client"}).assign(rg=lambda d: d["rg_in"])
    out_part = pay.rename(columns={"PAYER": "client"}).assign(rg=lambda d: d["rg_out"])
    tot_in = in_part.groupby("client")["PAY_AMT"].sum().reindex(idx)
    tot_out = out_part.groupby("client")["PAY_AMT"].sum().reindex(idx)
    mix_cust = _mix(in_part.dropna(subset=["rg"]).rename(columns={"rg": "sp"}), tot_in, reg)
    mix_sup = _mix(out_part.dropna(subset=["rg"]).rename(columns={"rg": "sp"}), tot_out, reg)
    mix_cust = mix_cust.reindex(idx)
    mix_sup = mix_sup.reindex(idx)
    return mix_cust, mix_sup


def g06_top_counterparties(pay: pd.DataFrame, attrs: pd.DataFrame,
                           client_ids: pd.Index, k: int = 5) -> pd.DataFrame:
    """G-06: топ-k контрагентов по обороту с их SP, регионом и типом клиента."""
    pay = _clean(pay)
    rows = []
    a = attrs.set_index("SYNTHETIC_U") if "SYNTHETIC_U" in attrs.columns else attrs
    for client, grp in pay.groupby("BENEFICIARY"):
        top = grp.groupby("PAYER")["PAY_AMT"].sum().nlargest(k)
        for cp, amt in top.items():
            row = {"SYNTHETIC_U": client, "counterparty": cp, "PAY_AMT": amt,
                   "direction": "in"}
            if cp in a.index:
                row.update({
                    "sp": a.at[cp, "F03_sector_profile"] if "F03_sector_profile" in a.columns else np.nan,
                    "region": a.at[cp, "F07_home_region"] if "F07_home_region" in a.columns else np.nan,
                    "cl_type": a.at[cp, "F10_cl_type"] if "F10_cl_type" in a.columns else np.nan,
                })
            rows.append(row)
    for client, grp in pay.groupby("PAYER"):
        top = grp.groupby("BENEFICIARY")["PAY_AMT"].sum().nlargest(k)
        for cp, amt in top.items():
            row = {"SYNTHETIC_U": client, "counterparty": cp, "PAY_AMT": amt,
                   "direction": "out"}
            if cp in a.index:
                row.update({
                    "sp": a.at[cp, "F03_sector_profile"] if "F03_sector_profile" in a.columns else np.nan,
                    "region": a.at[cp, "F07_home_region"] if "F07_home_region" in a.columns else np.nan,
                    "cl_type": a.at[cp, "F10_cl_type"] if "F10_cl_type" in a.columns else np.nan,
                })
            rows.append(row)
    return pd.DataFrame(rows)


def g07_intragroup(pay: pd.DataFrame, group_by_id: pd.Series,
                   client_ids: pd.Index) -> pd.Series:
    """G-07: доля внутригрупповых платежей (оба конца с одним GROUP_ID)."""
    pay = _clean(pay)
    g = group_by_id.to_dict()
    both = pay.assign(g_in=pay["PAYER"].map(g), g_out=pay["BENEFICIARY"].map(g))
    both = both.dropna(subset=["g_in", "g_out"])
    intra = both[both["g_in"] == both["g_out"]]
    idx = pd.Index(client_ids, name="SYNTHETIC_U")
    denom = (pay.groupby("BENEFICIARY")["PAY_AMT"].sum()
             .add(pay.groupby("PAYER")["PAY_AMT"].sum(), fill_value=0.0))
    num = pd.concat([
        intra.groupby("BENEFICIARY")["PAY_AMT"].sum(),
        intra.groupby("PAYER")["PAY_AMT"].sum(),
    ]).groupby(level=0).sum()
    res = (num / denom.where(denom > 0)).reindex(idx)
    return res


def g08_pagerank(pay: pd.DataFrame, client_ids: pd.Index,
                 iterations: int = 30, d: float = 0.85) -> pd.Series:
    """G-08 (P2): PageRank платёжной сети — простая степенная итерация, без networkx."""
    pay = _clean(pay)
    nodes = pd.Index(sorted(set(pay["PAYER"]) | set(pay["BENEFICIARY"])))
    n = len(nodes)
    if n == 0:
        return pd.Series(dtype=float)
    pos = {u: i for i, u in enumerate(nodes)}
    M = np.zeros((n, n))
    for payer, ben, amt in zip(pay["PAYER"], pay["BENEFICIARY"], pay["PAY_AMT"]):
        M[pos[ben], pos[payer]] += amt  # деньги текут payer -> beneficiary
    colsum = M.sum(axis=0, keepdims=True)
    M = np.divide(M, colsum, out=np.zeros_like(M), where=colsum > 0)
    r = np.ones(n) / n
    for _ in range(iterations):
        r = d * M @ r + (1 - d) / n
    return pd.Series(r, index=nodes).reindex(client_ids)


def g09_family_links(link: pd.DataFrame | None,
                     client_ids: pd.Index) -> pd.Series:
    """G-09 (P2): число компаний, делящих хотя бы одно физлицо (SYNTHETIC_F)."""
    if link is None or not {"SYNTHETIC_F", "SYNTHETIC_U"} <= set(link.columns):
        return pd.Series(np.nan, index=client_ids)
    pair_counts = link.groupby("SYNTHETIC_U")["SYNTHETIC_F"].transform("size")
    lk = link.assign(sz=pair_counts)
    same = lk.merge(lk, on="SYNTHETIC_F")
    same = same[same["SYNTHETIC_U_x"] != same["SYNTHETIC_U_y"]]
    cnt = same.groupby("SYNTHETIC_U_x")["SYNTHETIC_U_y"].nunique()
    return cnt.reindex(client_ids)


def g10_ticket_freq(pay: pd.DataFrame, client_ids: pd.Index) -> pd.DataFrame:
    """G-10 (P2): средний чек = ΣPAY_AMT / ΣPAY_CNT; частота = ΣPAY_CNT."""
    pay = _clean(pay)
    if "PAY_CNT" not in pay.columns:
        return pd.DataFrame({"G10_avg_ticket": np.nan, "G10_freq": np.nan},
                            index=pd.Index(client_ids, name="SYNTHETIC_U"))
    idx = pd.Index(client_ids, name="SYNTHETIC_U")
    amt_in = pay.groupby("BENEFICIARY")["PAY_AMT"].sum()
    cnt_in = pay.groupby("BENEFICIARY")["PAY_CNT"].sum()
    amt_out = pay.groupby("PAYER")["PAY_AMT"].sum()
    cnt_out = pay.groupby("PAYER")["PAY_CNT"].sum()
    amt = amt_in.add(amt_out, fill_value=0.0).reindex(idx)
    cnt = cnt_in.add(cnt_out, fill_value=0.0).reindex(idx)
    has = amt.notna() | cnt.notna()
    return pd.DataFrame({
        "G10_avg_ticket": (amt / cnt.replace(0, np.nan)).where(has),
        "G10_freq": cnt.where(has),
    }, index=idx)


def build_payment_features(pay: pd.DataFrame | None, clients: pd.DataFrame,
                           features_f: pd.DataFrame | None = None,
                           link: pd.DataFrame | None = None) -> dict:
    """Полный контур G-метрик. Возвращает словарь таблиц.

    sp/регионы контрагентов берутся из F-03/F-07 клиентов (features_f),
    GROUP_ID — из исходного clients.
    """
    client_ids = clients["SYNTHETIC_U"]
    if pay is None or len(pay) == 0:
        empty = pd.DataFrame(index=pd.Index(client_ids, name="SYNTHETIC_U"))
        return {"g01": empty, "g02": empty, "g03": empty,
                "mix_cust": empty, "mix_sup": empty,
                "g07": pd.Series(np.nan, index=client_ids), "top": pd.DataFrame(),
                "g10": empty}

    sp_by = (features_f["F03_sector_profile"] if features_f is not None
             else pd.Series(np.nan, index=clients["SYNTHETIC_U"]))
    reg_by = (features_f["F07_home_region"] if features_f is not None
              else pd.Series(np.nan, index=clients["SYNTHETIC_U"]))
    grp_by = pd.Series(clients["GROUP_ID"].values, index=clients["SYNTHETIC_U"])

    mix_cust, mix_sup = g04_sector_mix(pay, sp_by, client_ids)
    # атрибуты контрагентов (SP/регион/тип) для топ-k: клиенты + их F-признаки
    attrs = clients[["SYNTHETIC_U"]].reset_index(drop=True)
    if features_f is not None:
        attrs = pd.concat([attrs, features_f.reset_index(drop=True)], axis=1)
    attrs = attrs.set_index("SYNTHETIC_U")
    return {
        "g01": g01_turnover(pay, client_ids),
        "g02": g02_counters(pay, client_ids),
        "g03": g03_hhi(pay, client_ids),
        "mix_cust": mix_cust, "mix_sup": mix_sup,
        "g07": g07_intragroup(pay, grp_by, client_ids),
        "top": g06_top_counterparties(pay, attrs, client_ids),
        "g10": g10_ticket_freq(pay, client_ids),
    }
