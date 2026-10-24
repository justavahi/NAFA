# -*- coding: utf-8 -*-
"""WP-D16: графы GR-01..GR-03 и формулы FM-21..FM-24.

GR-01 — корпоративные и бенефициарные связи: юрлица (SYNTHETIC_U) + физлица
(SYNTHETIC_F), рёбра: общее физлицо, общий GROUP_ID.
GR-02 — платёжная сеть: направленные рёбра с весом PAY_AMT.
GR-03 — отраслевые зависимости: двудольный граф SP x фактор (вес = 0..3).

Реализация — pandas + union-find без внешних зависимостей (networkx опционален
для визуализации CH-21/CH-22). Рёбра можно выгрузить в DuckDB/CSV (WP-B).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class UnionFind:
    """Диспоинт-сета для связных компонент без networkx."""

    def __init__(self):
        self.parent: dict = {}

    def find(self, u):
        p = self.parent.setdefault(u, u)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def gr01_company_graph(clients: pd.DataFrame,
                       link: pd.DataFrame | None) -> dict:
    """Связные компоненты через физлиц и GROUP_ID; размеры компонент."""
    uf = UnionFind()
    us = clients["SYNTHETIC_U"].astype(str)
    for u in us:
        uf.find(u)
    # общий GROUP_ID
    if "GROUP_ID" in clients.columns:
        for gid, grp in clients.groupby("GROUP_ID"):
            if pd.isna(gid):
                continue
            for u in grp["SYNTHETIC_U"].astype(str).iloc[1:]:
                uf.union(str(grp["SYNTHETIC_U"].iloc[0]), u)
    # общие физлица
    if link is not None and {"SYNTHETIC_F", "SYNTHETIC_U"} <= set(link.columns):
        for fid, grp in link.groupby("SYNTHETIC_F"):
            members = grp["SYNTHETIC_U"].astype(str).unique()
            for u in members[1:]:
                uf.union(str(members[0]), u)

    comp: dict[str, list[str]] = {}
    for u in us:
        comp.setdefault(uf.find(u), []).append(u)
    sizes = pd.Series({k: len(v) for k, v in comp.items()}).sort_values(ascending=False)
    return {"components": comp, "sizes": sizes,
            "n_components": len(comp),
            "max_component_size": int(sizes.max()) if len(sizes) else 0}


def gr02_payment_edges(pay: pd.DataFrame) -> pd.DataFrame:
    """Рёбра платёжной сети: PAYER -> BENEFICIARY с суммой и числом платежей."""
    if pay is None or len(pay) == 0:
        return pd.DataFrame(columns=["src", "dst", "PAY_AMT", "PAY_CNT"])
    agg = pay.groupby(["PAYER", "BENEFICIARY"]).agg(
        PAY_AMT=("PAY_AMT", "sum"),
        PAY_CNT=("PAY_CNT", "sum") if "PAY_CNT" in pay.columns else ("PAY_AMT", "size"),
    ).reset_index().rename(columns={"PAYER": "src", "BENEFICIARY": "dst"})
    return agg


def gr02_stats(edges: pd.DataFrame, sp_by_id: pd.Series | None = None) -> dict:
    """Число рёбер, плотность, доля контрагентов с известным профилем SP."""
    n_nodes = pd.Index(pd.concat([edges["src"], edges["dst"]])).nunique() if len(edges) else 0
    density = len(edges) / max(1, n_nodes * (n_nodes - 1)) if n_nodes > 1 else 0.0
    sp_coverage = np.nan
    if sp_by_id is not None and len(edges):
        known = edges["dst"].map(sp_by_id.to_dict()).notna().mean()
        sp_coverage = float(known)
    return {"n_edges": int(len(edges)), "n_nodes": int(n_nodes),
            "density": float(density), "sp_coverage": sp_coverage}


def gr03_sector_factor_graph(matrix: pd.DataFrame) -> pd.DataFrame:
    """Двудольный граф SP x фактор: long-таблица (SP, factor, weight 0..3)."""
    m = matrix.set_index("sector_profile")
    long = m[list(matrix.columns[2:])].stack().rename("weight").reset_index()
    long.columns = ["sector_profile", "factor", "weight"]
    return long


def counterparty_shock_edges(pay: pd.DataFrame) -> dict[str, list[tuple[str, float]]]:
    """Готовит рёбра для FM-22: {клиент: [(поставщик, доля поставщика)]}.

    Клиент = PAYER (он зависит от своих поставщиков = BENEFICIARY).
    Для спроса строится симметричная версия по BENEFICIARY.
    """
    if pay is None or len(pay) == 0:
        return {}
    out_flow = pay.groupby(["PAYER", "BENEFICIARY"])["PAY_AMT"].sum().reset_index()
    out = {}
    for payer, grp in out_flow.groupby("PAYER"):
        total = grp["PAY_AMT"].sum()
        if total <= 0:
            continue
        out[str(payer)] = [(str(b), float(a / total))
                           for b, a in zip(grp["BENEFICIARY"], grp["PAY_AMT"])]
    return out


def counterpart_impact(matrix: pd.DataFrame, sp_of: dict[str, str],
                       iota: dict[str, float]) -> dict[str, float]:
    """x_j — поражение контрагента j событием: x_j = Σ_f (prior_f(SP(j))/3)·ι_f / Σι_f.

    Если профиль SP контрагента неизвестен, его вклад не учитывается
    (узел просто отсутствует в словаре x).
    """
    m = matrix.set_index("sector_profile")
    total_iota = sum(iota.values()) or 1.0
    x: dict[str, float] = {}
    for node, sp in sp_of.items():
        if sp not in m.index:
            continue
        s = 0.0
        for f, v in iota.items():
            prior = m.at[sp, f] / 3.0 if f in m.columns else 0.0
            s += prior * v
        x[node] = s / total_iota
    return x


def build_graphs(clients: pd.DataFrame, pay: pd.DataFrame | None,
                 link: pd.DataFrame | None, matrix: pd.DataFrame | None,
                 features_f: pd.DataFrame | None = None) -> dict:
    """Контур WP-D16 целиком (без визуализаций — их делает charts.py)."""
    g1 = gr01_company_graph(clients, link)
    edges2 = gr02_payment_edges(pay)
    sp_by = (features_f["F03_sector_profile"] if features_f is not None else None)
    if sp_by is not None:
        sp_by.index = clients["SYNTHETIC_U"].astype(str)
    stats2 = gr02_stats(edges2, sp_by)
    g3 = gr03_sector_factor_graph(matrix) if matrix is not None else None
    return {"gr01": g1, "gr02_edges": edges2, "gr02_stats": stats2,
            "gr03_long": g3}
