# -*- coding: utf-8 -*-
"""WP-D9: контур оценки качества (Q-метрики), запуск одной командой.

Входы:
  --golden  data/golden_set.csv   (client_id, event_id, relevance 0..3,
                                   опц. annotator1/2, archetype, section)
  --pred    predictions.csv       (client_id, event_id, score, rank) — ленты
  --traces  папка с JSON-трейсами агента: insight.claims[].source_ids,
            sources[].id (+is_mock) — формат вашего RAG-агента
  --pairs   dedup_pairs.csv       (pair_id, is_dup, flagged) — для Q-11
  --rubric  rubric_scores.csv     (card_id, q08_score 1..5) — для Q-08/Q-10

Метрики:
  Q-04 NDCG@5, Q-05 Precision@5 (всего и по секциям ОКВЭД), Q-06 отсев шума,
  Q-07 PD@5 + стабильность клонов, Q-08 средняя рубрика, Q-09/Q-01/Q-02/Q-28/
  Q-29 по трейсам, Q-11 dedup P/R, Q-10 деградация на hold-out.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .golden_set import relevance_to_int


# --- Q-04: NDCG@5 ------------------------------------------------------------
def q04_ndcg(golden: pd.DataFrame, pred: pd.DataFrame, k: int = 5) -> float:
    """NDCG@k по золотому набору; gain = 2^rel - 1."""
    rel_map = dict(zip(golden["client_id"].astype(str) + "|" +
                       golden["event_id"].astype(str),
                       relevance_to_int(golden["relevance"])))
    ndcgs = []
    for client, grp in pred.groupby("client_id"):
        client = str(client)
        top = grp.sort_values("rank").head(k)
        rels = [rel_map.get(f"{client}|{e}", 0.0) for e in top["event_id"]]
        gains = [2.0 ** r - 1.0 for r in rels]
        dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
        ideal = sorted((2.0 ** rel_map.get(f"{client}|{e}", 0.0) - 1.0
                        for e in golden.loc[
                            golden["client_id"].astype(str) == client, "event_id"]),
                       reverse=True)[:k]
        idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
        if idcg > 0:
            ndcgs.append(dcg / idcg)
    return float(np.mean(ndcgs)) if ndcgs else np.nan


# --- Q-05: точность персонализации -------------------------------------------
def q05_precision_at_k(golden: pd.DataFrame, pred: pd.DataFrame,
                       k: int = 5, good: float = 2.0) -> tuple[float, pd.DataFrame]:
    """Доля событий High/Medium (rel >= 2) в топ-5 клиента + разрез по секциям."""
    rel_map = dict(zip(golden["client_id"].astype(str) + "|" +
                       golden["event_id"].astype(str),
                       relevance_to_int(golden["relevance"])))
    sec_map = (dict(zip(golden["client_id"].astype(str), golden["section"]))
               if "section" in golden.columns else {})
    rows = []
    for client, grp in pred.groupby("client_id"):
        top = grp.sort_values("rank").head(k)
        rels = [rel_map.get(f"{client}|{e}", 0.0) for e in top["event_id"]]
        rows.append({"client_id": client, "p_at_k": np.mean([r >= good for r in rels]),
                     "section": sec_map.get(str(client), "NA")})
    df = pd.DataFrame(rows)
    overall = float(df["p_at_k"].mean()) if len(df) else np.nan
    by_sec = df.groupby("section")["p_at_k"].mean().reset_index() \
        if len(df) else pd.DataFrame()
    return overall, by_sec


# --- Q-06: отсев шума ----------------------------------------------------------
def q06_noise_rejection(noise_csv: str | Path) -> float:
    """Доля нерелевантных (шумовых) событий, НЕ попавших в ленту. Цель >= 0.9.
    CSV: event_id, in_feed (0/1)."""
    df = pd.read_csv(noise_csv, encoding="utf-8-sig")
    return float(1 - df["in_feed"].mean())


# --- Q-07: дивергенция персонализации -------------------------------------------
def q07_divergence(pred: pd.DataFrame, pairs_csv: str | Path, k: int = 5) -> dict:
    """PD@5 = 1 - mean(Jaccard(T_c, T_c')) по контрастным парам;
    для клонов одного архетипа PD <= 0.10 (стабильность)."""
    pairs = pd.read_csv(pairs_csv, encoding="utf-8-sig")
    feeds = {c: set(g.sort_values("rank").head(k)["event_id"])
             for c, g in pred.groupby("client_id")}
    rows = []
    for _, p in pairs.iterrows():
        a, b = str(p["client_a"]), str(p["client_b"])
        ja = len(feeds.get(a, set()) & feeds.get(b, set())) / \
            max(1, len(feeds.get(a, set()) | feeds.get(b, set())))
        rows.append({**p.to_dict(), "jaccard": ja})
    df = pd.DataFrame(rows)
    contrast = df[df.get("contrast", 1) == 1]["jaccard"] if "contrast" in df.columns \
        else df["jaccard"]
    clones = df[df.get("contrast", 1) == 0]["jaccard"] if "contrast" in df.columns \
        else pd.Series(dtype=float)
    return {"PD@5": float(1 - contrast.mean()) if len(contrast) else np.nan,
            "clone_PD_max": float((1 - clones).max()) if len(clones) else np.nan,
            "target": 0.40, "clone_target": 0.10}


# --- Q-11: дедупликация ------------------------------------------------------------
def q11_dedup(pairs_csv: str | Path) -> dict:
    """Precision/Recall дедупликации по размеченным парам. Цели: 0.95 / 0.85.
    CSV: is_dup (истина), flagged (нашёл алгоритм)."""
    df = pd.read_csv(pairs_csv, encoding="utf-8-sig")
    tp = int(((df["is_dup"] == 1) & (df["flagged"] == 1)).sum())
    fp = int(((df["is_dup"] == 0) & (df["flagged"] == 1)).sum())
    fn = int(((df["is_dup"] == 1) & (df["flagged"] == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else np.nan
    recall = tp / (tp + fn) if tp + fn else np.nan
    return {"precision": precision, "recall": recall,
            "targets": {"precision": 0.95, "recall": 0.85}}


# --- Q-01/Q-02/Q-28/Q-29: по трейсам агента -----------------------------------------
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _iter_insights(traces_dir: str | Path):
    for p in sorted(Path(traces_dir).glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            yield p.name, it


def _claims_of(insight: dict) -> list[dict]:
    ins = insight.get("insight", insight)
    claims = ins.get("claims") or ins.get("risks") or ins.get("opportunities") or []
    out = []
    for c in claims:
        if isinstance(c, str):
            out.append({"text": c, "source_ids": []})
        else:
            out.append({"text": c.get("text", ""),
                        "source_ids": c.get("source_ids", []) or []})
    return out


def _sources_of(insight: dict) -> dict[str, dict]:
    d = insight.get("insight", insight)
    srcs = d.get("sources") or d.get("resolved_sources") or []
    return {str(s.get("id")): s for s in srcs if isinstance(s, dict) and s.get("id")}


def q_trace_metrics(traces_dir: str | Path) -> dict:
    """Q-28 трассируемость (>=0.95) и Q-29 индекс галлюцинаций (<=0.03)
    по трейсам: каждое утверждение должно иметь валидную ссылку на источник;
    моки (is_mock) валидными не считаются."""
    total_claims = 0
    unsupported = 0        # утверждение без ссылок
    broken_links = 0       # ссылка на несуществующий/моковый источник
    unbacked_numbers = 0   # числа из утверждения не найдены в тексте источников
    traced_insights = 0
    n_insights = 0

    for _, ins in _iter_insights(traces_dir):
        n_insights += 1
        claims = _claims_of(ins)
        srcs = _sources_of(ins)
        real_texts = [str(s.get("text", "")) for s in srcs.values()
                      if not s.get("is_mock")]
        blob = " ".join(real_texts)
        insight_traced = True
        for c in claims:
            total_claims += 1
            ids = [str(i) for i in c["source_ids"]]
            if not ids:
                unsupported += 1
                insight_traced = False
                continue
            valid = [i for i in ids if i in srcs and not srcs[i].get("is_mock")]
            if not valid:
                broken_links += 1
                insight_traced = False
            if blob:
                nums = _NUM_RE.findall(c["text"])
                for n in nums:
                    n_norm = n.replace(",", ".")
                    if n_norm not in blob and n not in blob:
                        unbacked_numbers += 1
        if insight_traced:
            traced_insights += 1

    halluc = 0.0
    if total_claims:
        halluc = (unsupported + broken_links + unbacked_numbers) / total_claims
    return {
        "n_insights": n_insights,
        "claims_total": total_claims,
        "Q28_traceability": traced_insights / n_insights if n_insights else np.nan,
        "Q29_hallucination_index": halluc,
        "detail": {"unsupported": unsupported, "broken_links": broken_links,
                   "unbacked_numbers": unbacked_numbers},
        "targets": {"Q28": 0.95, "Q29": 0.03},
    }


def q01_q02_from_traces(traces_dir: str | Path, audit_csv: str | Path | None = None):
    """Q-01 supported/total и Q-02 precision/recall цитирования.
    audit_csv (ручной аудит): claim_text, supported (0/1), link_correct (0/1)."""
    base = {"Q01_faithfulness": np.nan, "Q02_precision": np.nan,
            "Q02_recall": np.nan}
    total = supported = links = good_links = claims_with_link = 0
    for _, ins in _iter_insights(traces_dir):
        srcs = _sources_of(ins)
        for c in _claims_of(ins):
            total += 1
            ids = [str(i) for i in c["source_ids"]]
            if ids and any(i in srcs and not srcs[i].get("is_mock") for i in ids):
                supported += 1
            if ids:
                claims_with_link += 1
                links += len(ids)
                good_links += sum(1 for i in ids
                                  if i in srcs and not srcs[i].get("is_mock"))
    if total:
        base["Q01_faithfulness"] = supported / total
    if links:
        base["Q02_precision"] = good_links / links
        base["Q02_recall"] = supported / total if total else np.nan
    if audit_csv and Path(audit_csv).exists():
        a = pd.read_csv(audit_csv, encoding="utf-8-sig")
        base["Q01_faithfulness"] = float(a["supported"].mean())
    return base


# --- Q-08/Q-10: рубрика и устойчивость ----------------------------------------------
def q08_rubric(rubric_csv: str | Path) -> float:
    """Средняя оценка 1..5 по критериям кейса. Цель >= 4.0."""
    df = pd.read_csv(rubric_csv, encoding="utf-8-sig")
    return float(df["q08_score"].mean())


def q10_holdout_degradation(q04_dev: float, q04_hold: float,
                            q08_dev: float, q08_hold: float) -> dict:
    """Устойчивость на новых новостях: относительное падение Q-04 и Q-08 <= 10%."""
    drop04 = (q04_dev - q04_hold) / q04_dev if q04_dev else np.nan
    drop08 = (q08_dev - q08_hold) / q08_dev if q08_dev else np.nan
    return {"Q04_drop": drop04, "Q08_drop": drop08, "target": 0.10}


# --- Q-17 (P1): устойчивость к пропускам ---------------------------------------------
def q17_ablation_report(q04_full: float, q04_ablated: float,
                        q08_full: float, q08_ablated: float) -> dict:
    """Падение Q-04/Q-08 при удалении 30% полей клиента; цель <= 15%."""
    d04 = (q04_full - q04_ablated) / q04_full if q04_full else np.nan
    d08 = (q08_full - q08_ablated) / q08_full if q08_full else np.nan
    return {"Q04_drop": d04, "Q08_drop": d08, "target": 0.15}


# --- Прогон всего контура --------------------------------------------------------------
def run_quality(golden_csv: str | Path, pred_csv: str | Path,
                out_dir: str | Path, traces_dir: str | Path | None = None,
                noise_csv: str | Path | None = None,
                pairs_csv: str | Path | None = None,
                rubric_csv: str | Path | None = None) -> dict:
    """Главная команда WP-D9: отчёт out_dir/quality/report.md."""
    golden = pd.read_csv(golden_csv, encoding="utf-8-sig")
    pred = pd.read_csv(pred_csv, encoding="utf-8-sig")
    res: dict = {}

    res["Q04_NDCG@5"] = q04_ndcg(golden, pred)
    p5, by_sec = q05_precision_at_k(golden, pred)
    res["Q05_Precision@5"] = p5
    res["Q05_by_section"] = by_sec.to_dict("records") if len(by_sec) else []

    if noise_csv:
        res["Q06_noise_rejection"] = q06_noise_rejection(noise_csv)
    if traces_dir:
        res.update({k: v for k, v in q_trace_metrics(traces_dir).items()
                    if k != "detail"})
        res.update(q01_q02_from_traces(traces_dir))
    if pairs_csv:
        res["Q11_dedup"] = q11_dedup(pairs_csv)
    if rubric_csv:
        res["Q08_rubric"] = q08_rubric(rubric_csv)

    out = Path(out_dir) / "quality"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# WP-D9. Контур оценки качества", "",
             "| метрика | значение | цель |", "|---|---|---|"]
    targets = {"Q04_NDCG@5": 0.80, "Q05_Precision@5": 0.70,
               "Q06_noise_rejection": 0.90, "Q28_traceability": 0.95,
               "Q29_hallucination_index": 0.03, "Q01_faithfulness": 0.95,
               "Q02_precision": 0.90, "Q02_recall": 0.85, "Q08_rubric": 4.0}
    for k, v in res.items():
        if k.startswith("Q") and np.isscalar(v) and v is not None:
            t = targets.get(k, "—")
            vv = f"{v:.3f}" if isinstance(v, (int, float, np.floating)) else v
            lines.append(f"| {k} | {vv} | {t} |")
    if res.get("Q05_by_section"):
        lines += ["", "## Q-05 по секциям ОКВЭД", "",
                  "| секция | P@5 |", "|---|---|",
                  *[f"| {r['section']} | {r['p_at_k']:.2f} |"
                    for r in res["Q05_by_section"]]]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    with open(out / "metrics.json", "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in res.items()
                   if not isinstance(v, (pd.DataFrame,))},
                  fh, ensure_ascii=False, indent=2, default=str)
    return res


def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="WP-D9: контур качества (одна команда)")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--traces", help="папка JSON-трейсов агента (Q-28/Q-29)")
    ap.add_argument("--noise", help="noise_events.csv (Q-06)")
    ap.add_argument("--pairs", help="dedup_pairs.csv (Q-11)")
    ap.add_argument("--rubric", help="rubric_scores.csv (Q-08)")
    args = ap.parse_args(argv)
    res = run_quality(args.golden, args.pred, args.out_dir,
                      traces_dir=args.traces, noise_csv=args.noise,
                      pairs_csv=args.pairs, rubric_csv=args.rubric)
    for k, v in res.items():
        if k.startswith("Q") and np.isscalar(v):
            print(f"[DA] {k} = {v if isinstance(v, str) else round(float(v), 4)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
