"""Search without embeddings (RSK-03): SQLite FTS5 + built-in bm25(), lexical over lemmas. The interface is replaceable:
swap the body of these two functions for an embedding index once OQ-01 allows it."""
from __future__ import annotations

import json
import sqlite3

from .config import get_config
from .nlp import fts_query, lemma_text


def index_news(conn: sqlite3.Connection, doc_id: str, title: str, snippet: str | None) -> None:
    conn.execute("DELETE FROM news_fts WHERE doc_id=?", (doc_id,))
    conn.execute("INSERT INTO news_fts(doc_id,text) VALUES(?,?)", (doc_id, lemma_text(f"{title} {snippet or ''}")))


def index_chunk(conn: sqlite3.Connection, chunk_id: str, text: str) -> None:
    conn.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk_id,))
    conn.execute("INSERT INTO chunk_fts(chunk_id,text) VALUES(?,?)", (chunk_id, lemma_text(text)))


def search_news(conn: sqlite3.Connection, query: str, *, since: str | None = None, until: str | None = None,
                source_ids: list[str] | None = None, as_of: str | None = None, k: int | None = None,
                collapse_clusters: bool = True, client_id: str | None = None) -> list[dict]:
    """Tool `search_news`. Dates are UTC strings 'YYYY-MM-DDTHH:MM:SSZ'. `as_of` hides anything published later
    (needed for replaying the state of the world on a past date). Duplicates are collapsed to the cluster representative."""
    q = fts_query(query)
    if not q:
        return []
    k = k or get_config().get("search.default_k", 10)
    sql = ["""SELECT d.*, bm25(news_fts) AS score FROM news_fts JOIN documents d ON d.doc_id=news_fts.doc_id
              WHERE news_fts MATCH ? AND d.ready_at IS NOT NULL"""]
    args: list = [q]
    if client_id is not None:
        sql.append("AND (d.origin!='byo' OR EXISTS(SELECT 1 FROM pe_event_owners o WHERE o.doc_id=d.doc_id AND o.client_id IN (?, '*')))")
        args.append(client_id)
    if since:
        sql.append("AND d.published_at_utc>=?"); args.append(since)
    for bound in (until, as_of):
        if bound:
            sql.append("AND d.published_at_utc<=?"); args.append(bound)
    if source_ids:
        sql.append(f"AND d.source_id IN ({','.join('?' * len(source_ids))})"); args += source_ids
    sql.append("ORDER BY score LIMIT ?"); args.append(k * 5 if collapse_clusters else k)
    rows = conn.execute(" ".join(sql), args).fetchall()

    reps = {r["cluster_id"]: r["representative_doc_id"] for r in conn.execute("SELECT cluster_id, representative_doc_id FROM clusters")}
    sizes = {r["cluster_id"]: r["size"] for r in conn.execute("SELECT cluster_id,size FROM clusters")}
    out, seen = [], set()
    for r in rows:
        cid = r["cluster_id"]
        if collapse_clusters and cid:
            if cid in seen:
                continue
            seen.add(cid)
            if reps.get(cid) and reps[cid] != r["doc_id"]:
                rep = conn.execute("SELECT * FROM documents WHERE doc_id=?", (reps[cid],)).fetchone()
                visible = rep and (client_id is None or rep["origin"]!="byo" or conn.execute("SELECT 1 FROM pe_event_owners WHERE doc_id=? AND client_id IN (?, '*')",(rep["doc_id"],client_id)).fetchone())
                if visible and (not as_of or rep["published_at_utc"] <= as_of):
                    r = rep
        out.append({
            "doc_id": r["doc_id"], "source_id": r["source_id"], "title": r["title"], "url": r["url"],
            "published_at": r["published_at"], "snippet": r["snippet"], "tier_weight": r["tier_weight"],
            "stale": bool(r["stale"]), "cluster_id": cid, "cluster_size": sizes.get(cid, 1), "origin": r["origin"],
        })
        if len(out) >= k:
            break
    return out


def search_alfa_analytics(conn: sqlite3.Connection, query: str, *, doc_ids: list[str] | None = None,
                          as_of: str | None = None, k: int | None = None) -> list[dict]:
    """Tool `search_alfa_analytics`: chunks with doc_id + page so the UI can link to the exact page (UI-08)."""
    q = fts_query(query)
    if not q:
        return []
    k = k or get_config().get("search.default_k", 10)
    sql = ["""SELECT c.*, a.stale AS doc_stale, a.doc_date, a.title AS doc_title, bm25(chunk_fts) AS score
              FROM chunk_fts JOIN chunks c ON c.chunk_id=chunk_fts.chunk_id JOIN alfa_docs a ON a.doc_id=c.doc_id
              WHERE chunk_fts MATCH ?"""]
    args: list = [q]
    if doc_ids:
        sql.append(f"AND c.doc_id IN ({','.join('?' * len(doc_ids))})"); args += doc_ids
    if as_of:
        sql.append("AND (c.as_of_date IS NULL OR c.as_of_date<=?)"); args.append(as_of[:10])
    sql.append("ORDER BY score LIMIT ?"); args.append(k)
    return [{
        "chunk_id": r["chunk_id"], "doc_id": r["doc_id"], "doc_title": r["doc_title"], "page": r["page"],
        "block_type": r["block_type"], "text": r["text"], "numbers": json.loads(r["numbers_json"] or "[]"),
        "as_of": r["as_of_date"] or r["doc_date"], "stale": bool(r["doc_stale"]),
        "needs_manual_check": bool(r["needs_manual_check"]), "verified_by": r["verified_by"], "image_path": r["image_path"],
    } for r in conn.execute(" ".join(sql), args)]


def evaluate_alfa_queries(conn: sqlite3.Connection, path: str, k: int = 3) -> dict:
    """WP-B6 acceptance: for each (query, expected doc) pair, is the expected document in the top-k chunks?

    CSV columns: query,expected_doc_ids (';'-separated). Pass = at least one expected doc in the top-k.
    """
    import csv
    from pathlib import Path
    rows = list(csv.DictReader(open(Path(path), encoding="utf-8-sig", newline="")))
    results, passes = [], 0
    for row in rows:
        expected = [d.strip() for d in (row.get("expected_doc_ids") or "").split(";") if d.strip()]
        hits = search_alfa_analytics(conn, row["query"], k=k)
        got = [h["doc_id"] for h in hits]
        ok = any(d in got for d in expected)
        passes += int(ok)
        results.append({"query": row["query"], "expected": expected, "top_k_docs": got, "pass": ok})
    total = len(results)
    return {"k": k, "total": total, "passed": passes, "hit_rate": round(passes / total, 3) if total else None,
            "acceptance_met": total > 0 and passes >= max(1, round(0.8 * total)), "results": results}
