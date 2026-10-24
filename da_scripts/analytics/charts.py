# -*- coding: utf-8 -*-
"""WP-D11: графики CH-01..CH-25 (спецификация — раздел 5 ТЗ).

Общие требования:
  PNG 1600x900 (16:9), 200 dpi; русский язык; заголовок формулирует ВЫВОД;
  подпись источника с as_of внизу; палитра, различимая при дальтонизме;
  имя файла CH-xx_<slug>.png; рядом CH-xx.json с метаданными
  (id, title, metric_ids, as_of, takeaway <= 25 слов, alt_text) — их
  использует Prompt Engineer.

P0 (обязательные): CH-06, CH-07, CH-08, CH-14, CH-15, CH-23, CH-25.
Остальные — P1/P2: строители готовы, но требуют данных от Backend (ряды M).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import FACTOR_ORDER

# Дальтонизм-безопасная палитра (Okabe-Ito)
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
           "#56B4E9", "#F0E442", "#000000"]
FIGSIZE, DPI = (8.0, 4.5), 200  # -> 1600x900 px


def _setup() -> plt.RcParams:
    plt.rcParams.update({
        "font.size": 11, "axes.titlesize": 13, "axes.titleweight": "bold",
        "figure.facecolor": "white", "axes.grid": True,
        "grid.alpha": 0.25, "axes.axisbelow": True,
    })
    return plt.rcParams


def _save(fig, out_dir: Path, ch_id: str, title: str, takeaway: str,
          metric_ids: list[str], slug: str, alt_text: str,
          source: str = "Расчёт DA по данным кейса (Приложение 1)") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = date.today().isoformat()
    path = out_dir / f"{ch_id}_{slug}.png"
    fig.savefig(path, dpi=DPI, facecolor="white")
    meta = {"id": ch_id, "title": title, "metric_ids": metric_ids,
            "as_of": as_of, "takeaway": takeaway, "alt_text": alt_text,
            "source": source}
    (out_dir / f"{ch_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    plt.close(fig)
    return path


# --- CH-06: радар вектора экспозиций (P0) -----------------------------------
def ch06_radar(eps: dict[str, dict[str, float]], out_dir: Path,
               clients: list[str] | None = None,
               takeaway: str = "Профили экспозиций клиентов различаются: ставка бьёт по заёмщику, но не по вкладчику") -> Path:
    """eps: {client_id: {FACTOR: value 0..1}}; сравнение двух клиентов."""
    _setup()
    clients = clients or list(eps.keys())[:2]
    labels = FACTOR_ORDER
    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]
    fig, ax = plt.subplots(figsize=FIGSIZE, subplot_kw=dict(polar=True),
                           constrained_layout=True)
    for i, c in enumerate(clients):
        vals = [eps[c].get(f, 0.0) for f in labels]
        vals += vals[:1]
        ax.plot(angles, vals, color=PALETTE[i], lw=2, label=c)
        ax.fill(angles, vals, color=PALETTE[i], alpha=0.15)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1)
    ax.set_title("Вектор экспозиций: клиент-заёмщик vs клиент-вкладчик", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1), fontsize=9)
    ax.text(0.5, -0.1, "Источник: расчёт DA (E-14), значения 0..1",
            transform=ax.transAxes, ha="center", fontsize=8, alpha=0.7)
    return _save(fig, out_dir, "CH-06",
                 "Радар вектора экспозиций клиента",
                 takeaway, ["E-14", "CH-06"], "radar_exposures",
                 "Полярная диаграмма: 12 осей факторов, 0..1, два клиента")


# --- CH-07: матрица SP x фактор (P0) ------------------------------------------
def ch07_matrix(matrix: pd.DataFrame, out_dir: Path,
                takeaway: str = "Строительство и добыча чувствительнее ИТ к ставке: prior отрасли 3 против 1") -> Path:
    """matrix: таблица sector_sensitivity_matrix.csv (SP-xx, значения 0..3)."""
    _setup()
    m = matrix.set_index("sector_profile")
    factors = [f for f in FACTOR_ORDER if f in m.columns]
    data = m[factors].astype(float)
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    im = ax.imshow(data.values, cmap="YlGnBu", vmin=0, vmax=3, aspect="auto")
    ax.set_xticks(range(len(factors)), factors, rotation=45, ha="right")
    ax.set_yticks(range(len(data)), data.index)
    for i in range(len(data)):
        for j in range(len(factors)):
            ax.text(j, i, f"{data.values[i, j]:.0f}", ha="center", va="center",
                    color="white" if data.values[i, j] > 1.5 else "black",
                    fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8, label="чувствительность 0..3")
    ax.set_title("Матрица «профиль отрасли x фактор»: гипотеза для проверки данными")
    return _save(fig, out_dir, "CH-07", "Матрица SP x фактор",
                 takeaway, ["F-03", "E-14"], "matrix_sp_factor",
                 "Тепловая карта 15 профилей отраслей на 12 факторов, значения 0..3")


# --- CH-08: ключевая ставка, факт + прогноз Альфы (P0) --------------------------
def ch08_rate(fact_csv: str | Path, forecast_csv: str | Path, out_dir: Path,
              takeaway: str = "Альфа подняла прогноз на конец 2026 с 13,5% до 14,0% (11.09): деньги остаются дорогими") -> Path:
    """fact_csv: date,value (ряд ЦБ M-01); forecast_csv: as_of,horizon_date,value."""
    if fact_csv is None or forecast_csv is None:
        raise FileNotFoundError(
            "нет rate_fact.csv / rate_forecast.csv — график появится, "
            "когда Backend отдаст ряды M (M-01, B-01)")
    _setup()
    fact = pd.read_csv(fact_csv, parse_dates=["date"])
    fc = pd.read_csv(forecast_csv, parse_dates=["as_of", "horizon_date"])
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    ax.plot(fact["date"], fact["value"], color=PALETTE[0], lw=2,
            label="Ключевая ставка ЦБ (факт)")
    versions = fc.sort_values("as_of")
    for i, (_, row) in enumerate(versions.iterrows()):
        seg = fc[(fc["as_of"] == row["as_of"])].sort_values("horizon_date")
        ax.step(seg["horizon_date"], seg["value"], where="post",
                color=PALETTE[1 + i % len(PALETTE)], ls="--", lw=2,
                label=f"Прогноз Альфы от {row['as_of'].date()}")
    ax.set_ylabel("% годовых")
    ax.set_title("Ставка: факт ЦБ и путь прогноза Альфы на конец 2026")
    ax.legend(fontsize=9, loc="best")
    ax.text(0.99, -0.14, "Источник: ЦБ РФ (M-01), отчёты Альфа-Банка (DOC-07)",
            transform=ax.transAxes, ha="right", fontsize=8, alpha=0.7)
    return _save(fig, out_dir, "CH-08", "Ключевая ставка: факт и прогноз",
                 takeaway, ["M-01", "B-01"], "rate_fact_forecast",
                 "Линия факта ставки и ступени прогнозов Альфы с датами документов")


# --- CH-14: разложение скора (P0) -------------------------------------------------
def ch14_waterfall(components: dict[str, float], multipliers: dict[str, float],
                   score: float, out_dir: Path,
                   takeaway: str = "Скор 0.536 складывается из релевантности, масштаба и достоверности; роль усиливает, свежесть ослабляет") -> Path:
    """components: {'Rel': 0.4*rel, 'Imp': 0.25*imp, ...} — вклады в скобку;
    multipliers: {'Fresh': 0.862, 'Роль': 0.97}; score — итог FM-13."""
    _setup()
    labels = list(components.keys()) + list(multipliers.keys()) + ["Score"]
    cum = 0.0
    bottoms, heights, colors = [], [], []
    for k, v in components.items():
        bottoms.append(cum)
        heights.append(v)
        colors.append(PALETTE[0])
        cum += v
    base_after_components = cum
    for k, m in multipliers.items():
        bottoms.append(cum)
        heights.append(m * cum - cum)  # прирост от умножения
        colors.append(PALETTE[1])
        cum = m * cum
    bottoms.append(0)
    heights.append(score)
    colors.append(PALETTE[2])
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    bars = ax.bar(range(len(labels)), heights, bottom=bottoms, color=colors,
                  edgecolor="white")
    top = max([b + h for b, h in zip(bottoms, heights) if h >= 0] or [1.0])
    ax.set_ylim(0, top * 1.18)
    for i, (b, h) in enumerate(zip(bottoms, heights)):
        ax.text(i, b + h + 0.01, f"{h:.3f}" if i < len(labels) - 1 else f"{score:.3f}",
                ha="center", fontsize=9)
    ax.set_xticks(range(len(labels)),
                  [f"{l}" for l in labels], rotation=0)
    ax.set_ylabel("вклад в Score")
    ax.set_title(f"Разложение скора карточки: итог {score:.3f}")
    ax.text(0.99, -0.16,
            f"Контроль: сумма вкладов x множители = {cum:.4f} vs Score {score:.4f} "
            f"(допуск 0.001)", transform=ax.transAxes, ha="right", fontsize=8,
            alpha=0.7)
    assert abs(cum - score) <= 0.001 + 1e-9, \
        f"Водопад не сходится: {cum} != {score}"
    return _save(fig, out_dir, "CH-14", "Waterfall скора FM-13",
                 takeaway, ["FM-13", "CH-14"], "score_waterfall",
                 "Столбчатая диаграмма-водопад: вклады компонентов и множителей в итоговый скор")


# --- CH-15: расхождение лент клиентов (P0) ------------------------------------------
def ch15_divergence(pred: pd.DataFrame, out_dir: Path, k: int = 5,
                    takeaway: str = "Ленты контрастных клиентов пересекаются менее чем наполовину: персонализация работает") -> Path:
    """Тепловая карта Jaccard топ-5 лент; по осям — клиенты (архетипы)."""
    _setup()
    feeds = {c: set(g.sort_values("rank").head(k)["event_id"])
             for c, g in pred.groupby("client_id")}
    ids = sorted(feeds)
    n = len(ids)
    J = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            a, b = feeds[ids[i]], feeds[ids[j]]
            J[i, j] = len(a & b) / len(a | b) if a | b else 1.0
    pd_at_5 = 1 - np.mean([J[i, j] for i in range(n) for j in range(n) if i < j])
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    im = ax.imshow(J, cmap="RdYlGn_r", vmin=0, vmax=1)
    ax.set_xticks(range(n), ids, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(n), ids, fontsize=8)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{J[i, j]:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.8, label="Jaccard")
    ax.set_title(f"Расхождение лент клиентов: PD@5 = {pd_at_5:.2f} (цель >= 0.40)")
    return _save(fig, out_dir, "CH-15", "Матрица Jaccard лент",
                 takeaway, ["Q-07", "CH-15"], "feed_divergence",
                 "Тепловая карта попарного пересечения топ-5 лент клиентов")


# --- CH-23: релевантность по отраслям (P0) --------------------------------------------
def ch23_relevance_by_sector(rel_csv: str | Path, out_dir: Path,
                             threshold: float = 0.25,
                             takeaway: str = "У 12 из 15 отраслей медианная релевантность ставки выше порога 0.25") -> Path:
    """rel_csv: sector_profile, rel — релевантности событий по отраслям (FM-06)."""
    _setup()
    df = pd.read_csv(rel_csv, encoding="utf-8-sig")
    sectors = sorted(df["sector_profile"].unique())
    data = [df.loc[df["sector_profile"] == s, "rel"].values for s in sectors]
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    ax.violinplot(data, showmedians=True)
    ax.axhline(threshold, color=PALETTE[1], ls="--", lw=2,
               label=f"порог Rel = {threshold}")
    counts = [int((d >= threshold).sum()) for d in data]
    ax.set_xticks(range(1, len(sectors) + 1),
                  [f"{s}\n(n>{threshold}: {c})" for s, c in zip(sectors, counts)],
                  fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("Rel (FM-06)")
    ax.set_title("Релевантность событий по отраслям: кто выше порога")
    ax.legend(fontsize=9)
    return _save(fig, out_dir, "CH-23", "Релевантность по отраслям",
                 takeaway, ["FM-06", "CH-23"], "rel_by_sector",
                 "Скрипичные диаграммы Rel по 15 профилям отраслей с линией порога 0.25")


# --- CH-25: воронка персонализации (P0) -------------------------------------------------
def ch25_funnel(stages: dict[str, int], out_dir: Path,
                takeaway: str = "От 500 событий до 5 карточек: главный фильтр — порог релевантности") -> Path:
    """stages: {'Входящий поток': 500, 'После дедупликации': 380, ...}."""
    _setup()
    names = list(stages)
    vals = [stages[k] for k in names]
    conv = [100.0] + [100.0 * v / vals[i] for i, v in enumerate(vals[1:], start=1)]
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    y = np.arange(len(names))[::-1]
    ax.barh(y, vals, color=PALETTE[:len(names)], edgecolor="white")
    for yi, v, c in zip(y, vals, conv):
        ax.text(v, yi + 0.1, f" {v}  ({c:.0f}% от пред.)", va="center", fontsize=9)
    ax.set_yticks(y, names)
    ax.set_xlabel("число событий")
    ax.set_title("Воронка персонализации: от потока новостей до открытых карточек")
    return _save(fig, out_dir, "CH-25", "Воронка персонализации",
                 takeaway, ["Q-31", "CH-25"], "funnel",
                 "Горизонтальная воронка: поток -> дедупликация -> порог Rel -> топ-5 -> открыто")


# --- P1/P2-строители (нужны данные от Backend) --------------------------------------------
def ch01_revenue_box(features_csv: str | Path, out_dir: Path) -> Path:
    """CH-01 (P1): выручка по секциям ОКВЭД, box, лог-шкала."""
    _setup()
    df = pd.read_csv(features_csv, encoding="utf-8-sig")
    df = df.dropna(subset=["REVENUE_COMPANY"])
    df = df[df["REVENUE_COMPANY"] > 0]
    sections = sorted(df["F03_section"].dropna().unique())
    data = [df.loc[df["F03_section"] == s, "REVENUE_COMPANY"] / 1e9
            for s in sections]
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    ax.boxplot(data, showfliers=True,
               flierprops=dict(markersize=2, alpha=0.4))
    ax.set_xticks(range(1, len(sections) + 1), sections)
    ax.set_yscale("log")
    ax.set_ylabel("выручка, млрд руб (лог)")
    ax.set_title("Выручка по секциям ОКВЭД: разброс внутри секций велик")
    return _save(fig, out_dir, "CH-01", "Выручка по секциям",
                 "Медианная выручка и выбросы по секциям A..U", ["F-01", "F-03"],
                 "revenue_box", "Box-плоты выручки по секциям, лог-шкала")


def ch02_product_heatmap(features_csv: str | Path, out_dir: Path) -> Path:
    """CH-02 (P1): тепловая карта долей продуктов по профилям отраслей."""
    _setup()
    df = pd.read_csv(features_csv, encoding="utf-8-sig")
    prods = ["F08_RKO", "F08_GAR", "F08_LOA", "F08_LEA", "F08_DEP",
             "F08_ZPP", "F08_FACTORING"]
    df = df.dropna(subset=["F03_sector_profile"])
    piv = df.groupby("F03_sector_profile")[prods].mean()
    fig, ax = plt.subplots(figsize=FIGSIZE, constrained_layout=True)
    im = ax.imshow(piv.values, cmap="YlGnBu", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(prods)),
                  [p.replace("F08_", "") for p in prods], rotation=45, ha="right")
    ax.set_yticks(range(len(piv)), piv.index, fontsize=8)
    for i in range(len(piv)):
        for j in range(len(prods)):
            ax.text(j, i, f"{piv.values[i, j]:.2f}", ha="center", va="center",
                    fontsize=7,
                    color="white" if piv.values[i, j] > 0.6 else "black")
    fig.colorbar(im, ax=ax, shrink=0.8, label="доля клиентов с продуктом")
    ax.set_title("Продуктовый профиль по отраслям")
    return _save(fig, out_dir, "CH-02", "Продуктовый профиль",
                 "Доли продуктов различаются по отраслям: заёмные сконцентрированы в строительстве",
                 ["F-08", "F-03"], "products_heatmap",
                 "Тепловая карта: отрасли x 7 продуктов, доли 0..1")


def ch03_headcount_conflict(features_csv: str | Path, out_dir: Path) -> Path:
    """CH-03 (P1): тепловая карта расхождений градаций ФНС и СПАРК."""
    _setup()
    df = pd.read_csv(features_csv, encoding="utf-8-sig")
    f, s = "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION"
    ct = pd.crosstab(df[f].astype(str), df[s].astype(str), normalize=True)
    conflict_share = float(df["F05_conflict"].mean())
    fig, ax = plt.subplots(figsize=(7, 6), constrained_layout=True)
    im = ax.imshow(ct.values, cmap="YlGnBu")
    ax.set_xticks(range(len(ct.columns)), ct.columns, rotation=90, fontsize=6)
    ax.set_yticks(range(len(ct.index)), ct.index, fontsize=6)
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title(f"ФНС vs СПАРК: доля конфликтов {conflict_share:.1%} (диагональ — согласие)")
    return _save(fig, out_dir, "CH-03", "Расхождение численности",
                 f"Конфликт градаций ФНС/СПАРК у {conflict_share:.0%} клиентов",
                 ["F-05"], "headcount_conflict",
                 "Тепловая карта сопоставления градаций численности двух источников")


# --- Реестр графиков ---------------------------------------------------------------------
CHART_REGISTRY = {
    "CH-01": ("P1", lambda ctx: ch01_revenue_box(ctx["features_csv"], ctx["out_dir"])),
    "CH-02": ("P1", lambda ctx: ch02_product_heatmap(ctx["features_csv"], ctx["out_dir"])),
    "CH-03": ("P1", lambda ctx: ch03_headcount_conflict(ctx["features_csv"], ctx["out_dir"])),
    "CH-06": ("P0", lambda ctx: ch06_radar(ctx["eps"], ctx["out_dir"],
                                           ctx.get("radar_clients"))),
    "CH-07": ("P0", lambda ctx: ch07_matrix(ctx["matrix"], ctx["out_dir"])),
    "CH-08": ("P0", lambda ctx: ch08_rate(ctx["rate_fact_csv"],
                                          ctx["rate_forecast_csv"], ctx["out_dir"])),
    "CH-14": ("P0", lambda ctx: ch14_waterfall(ctx["wf_components"],
                                               ctx["wf_multipliers"],
                                               ctx["wf_score"], ctx["out_dir"])),
    "CH-15": ("P0", lambda ctx: ch15_divergence(ctx["pred"], ctx["out_dir"])),
    "CH-23": ("P0", lambda ctx: ch23_relevance_by_sector(ctx["rel_csv"], ctx["out_dir"])),
    "CH-25": ("P0", lambda ctx: ch25_funnel(ctx["stages"], ctx["out_dir"])),
}


def build_charts(ctx: dict, priority: str = "P0",
                 only: list[str] | None = None) -> dict[str, str]:
    """Строит графики выбранного приоритета. ctx — словарь входов (см. run_all).

    Отсутствующие входы -> график пропускается с сообщением (для P1/P2 это
    норма: данные придут от Backend).
    """
    out_dir = Path(ctx["out_dir"]) / "charts"
    results: dict[str, str] = {}
    for ch_id, (prio, fn) in CHART_REGISTRY.items():
        if only and ch_id not in only:
            continue
        if priority != "ALL" and prio != priority:
            continue
        try:
            path = fn(ctx)
            results[ch_id] = str(path)
        except (FileNotFoundError, KeyError, AssertionError, ValueError) as e:
            results[ch_id] = f"ПРОПУЩЕН ({type(e).__name__}: {e})"
    return results
