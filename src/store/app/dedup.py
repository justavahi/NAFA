"""FM-17 deduplication. Three levels:
  exact       - same normalised title+snippet (content_hash) under a different URL
  near        - TF-IDF cosine >= near_exact_threshold
  same_event  - cosine >= same_event_threshold  -> goes to one cluster; representative = highest source weight
Thresholds are assumptions in settings.yaml; `evaluate_pairs` measures precision/recall on DA-labelled pairs (Q-11)."""
from __future__ import annotations

import sqlite3
from datetime import timedelta

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .config import get_config
from .nlp import lemma_text
from .util import iso, now_utc, parse_iso_utc, sha1


def _vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(token_pattern=r"\S+", ngram_range=(1, 2), sublinear_tf=True, lowercase=False)


def _text(row) -> str:
    return lemma_text(f"{row['title']} {row['snippet'] or ''}")


def assign_clusters(conn: sqlite3.Connection, new_doc_ids: list[str]) -> dict[str, str | None]:
    """Returns {doc_id: dup_kind|None}. Writes cluster_id / dup_of / dup_kind and refreshes `clusters`."""
    if not new_doc_ids:
        return {}
    cfg = get_config()
    window = timedelta(hours=cfg.get("news.dedup.window_hours", 72))
    t_near = cfg.get("news.dedup.near_exact_threshold", 0.85)
    t_event = cfg.get("news.dedup.same_event_threshold", 0.50)
    min_tokens = cfg.get("news.dedup.min_tokens", 3)

    ph = ",".join("?" * len(new_doc_ids))
    new_rows = conn.execute(
        f"SELECT doc_id,title,snippet,published_at_utc,content_hash,tier_weight,cluster_id FROM documents WHERE doc_id IN ({ph}) ORDER BY published_at_utc, doc_id",
        new_doc_ids).fetchall()
    lo = iso_min = min(r["published_at_utc"] for r in new_rows)
    lo = (parse_iso_utc(iso_min) - window).strftime("%Y-%m-%dT%H:%M:%SZ")
    cands = conn.execute(
        f"SELECT doc_id,title,snippet,published_at_utc,content_hash,tier_weight,cluster_id FROM documents WHERE published_at_utc>=? AND doc_id NOT IN ({ph}) ORDER BY published_at_utc, doc_id",
        [lo, *new_doc_ids]).fetchall()

    rows = list(cands) + list(new_rows)
    n_c = len(cands)
    texts = [_text(r) for r in rows]
    ntok = [len(t.split()) for t in texts]
    X = None
    if any(t for t in texts):
        try:
            X = _vectorizer().fit_transform(texts)
        except ValueError:                       # empty vocabulary
            X = None
    pubs = [parse_iso_utc(r["published_at_utc"]) for r in rows]
    cluster_of = {r["doc_id"]: r["cluster_id"] for r in rows}
    result: dict[str, str | None] = {}
    touched: set[str] = set()

    for j in range(n_c, len(rows)):
        me = rows[j]
        best, best_sim, kind = None, 0.0, None
        for i in range(0, j):                    # candidates + earlier new docs
            if abs(pubs[j] - pubs[i]) > window:
                continue
            if rows[i]["content_hash"] == me["content_hash"]:
                best, best_sim, kind = i, 1.0, "exact"
                break
        if kind is None and X is not None and ntok[j] >= min_tokens:
            sims = (X[j] @ X[:j].T).toarray().ravel() if j > 0 else np.array([])
            order = np.argsort(-sims)
            for i in order:
                if abs(pubs[j] - pubs[i]) > window or ntok[i] < min_tokens:
                    continue
                s = float(sims[i])
                if s >= t_event:
                    best, best_sim = int(i), s
                    kind = "near" if s >= t_near else "same_event"
                    break                        # first qualifying neighbour is the best eligible one
        result[me["doc_id"]] = kind
        if kind is None:
            continue
        other = rows[best]
        cid = cluster_of.get(other["doc_id"])
        if not cid:
            cid = "CL-" + sha1(other["doc_id"])[:10]
            cluster_of[other["doc_id"]] = cid
            conn.execute("UPDATE documents SET cluster_id=? WHERE doc_id=?", (cid, other["doc_id"]))
        cluster_of[me["doc_id"]] = cid
        conn.execute("UPDATE documents SET cluster_id=?, dup_of=?, dup_kind=? WHERE doc_id=?",
                     (cid, other["doc_id"], kind, me["doc_id"]))
        touched.add(cid)

    for cid in touched:
        refresh_cluster(conn, cid)
    return result


def refresh_cluster(conn: sqlite3.Connection, cluster_id: str) -> None:
    members = conn.execute(
        "SELECT doc_id FROM documents WHERE cluster_id=? ORDER BY tier_weight DESC, published_at_utc ASC, doc_id ASC",
        (cluster_id,)).fetchall()
    if not members:
        return
    conn.execute(
        """INSERT INTO clusters(cluster_id,representative_doc_id,size,updated_at) VALUES(?,?,?,?)
           ON CONFLICT(cluster_id) DO UPDATE SET representative_doc_id=excluded.representative_doc_id,
             size=excluded.size, updated_at=excluded.updated_at""",
        (cluster_id, members[0]["doc_id"], len(members), iso(now_utc())))


def evaluate_pairs(conn: sqlite3.Connection, pairs: list[tuple[str, str, int]],
                   thresholds: list[float] | None = None) -> list[dict]:
    """pairs = (doc_id_a, doc_id_b, label 1=same event/duplicate, 0=different). Returns precision/recall/F1 per threshold (Q-11)."""
    thresholds = thresholds or [round(x, 2) for x in np.arange(0.3, 0.96, 0.05)]
    ids = sorted({d for a, b, _ in pairs for d in (a, b)})
    rows = {r["doc_id"]: r for r in conn.execute(
        f"SELECT doc_id,title,snippet FROM documents WHERE doc_id IN ({','.join('?' * len(ids))})", ids)}
    missing = [d for d in ids if d not in rows]
    if missing:
        raise KeyError(f"unknown doc_ids in labelled pairs: {missing[:5]}")
    order = list(rows)
    X = _vectorizer().fit_transform([_text(rows[d]) for d in order])
    idx = {d: i for i, d in enumerate(order)}
    sims = [float((X[idx[a]] @ X[idx[b]].T).toarray()[0, 0]) for a, b, _ in pairs]
    labels = [l for _, _, l in pairs]
    out = []
    for t in thresholds:
        pred = [s >= t for s in sims]
        tp = sum(1 for p, l in zip(pred, labels) if p and l)
        fp = sum(1 for p, l in zip(pred, labels) if p and not l)
        fn = sum(1 for p, l in zip(pred, labels) if not p and l)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out.append({"threshold": t, "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)})
    return out
