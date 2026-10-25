"""Generates the three deliverable files of the role from the live DB + config (never hand-written status):
  docs/backend/01_parsing_sources.md, 02_sources_descriptions.md, 03_metrics_collected.md"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from .config import ROOT, get_sources

# What the data layer actually produces, per Backend metric id (45 in the catalogue). Anything else is reported honestly.
PRODUCED = {
    "N-06": ("documents.tier_weight / sources.tier_weight", "on ingest", "collected"),
    "N-07": ("documents.published_at_utc (input; Fresh formula FM-09 is DA's function)", "at ranking", "input stored"),
    "N-08": ("documents.cluster_id, clusters (exact/near/same_event)", "on ingest", "collected (cluster part; Nov needs client history)"),
    "M-01": ("macro_series via SRC-16 (cbr.ru key-rate table)", "6 h", "collected"),
    "M-03": ("macro_series via SRC-02 XML_daily", "3 h", "collected"),
    "M-04": ("macro_series via SRC-02 XML_daily", "3 h", "collected"),
    "M-05": ("macro_series via SRC-02 XML_daily", "3 h", "collected"),
    "M-11": ("macro_series M-11.weekly / M-11.mom / M-11.yoy: fallback from DOC-04 (no stable Rosstat API)", "weekly / monthly", "collected (fallback source DOC-04)"),
    "Q-13": ("llm_calls (tokens, RUB per call; /budget)", "continuous", "computed"),
    "Q-23": ("fetch_runs -> /quality/ingestion", "continuous", "computed"),
    "Q-24": ("ingestion_log (ready_at - published_at) -> /quality/ingestion", "continuous", "computed"),
    "Q-25": ("llm_calls by operation -> /quality/ingestion", "continuous", "computed"),
    "Q-26": ("ingestion_log -> /quality/ingestion", "daily", "computed"),
    "Q-27": ("ingestion_log, alfa_docs -> /quality/ingestion", "daily", "computed"),
}
AGENT_LAYER = {"N-01", "N-03", "N-10", "N-13", "Q-12", "Q-14", "Q-15", "Q-16", "Q-18", "Q-19", "Q-20", "Q-21", "Q-22", "Q-28", "Q-30"}


def _samples(conn: sqlite3.Connection, source_id: str) -> list[str]:
    rows = conn.execute("SELECT published_at,title,url FROM documents WHERE source_id=? ORDER BY published_at_utc DESC LIMIT 3", (source_id,)).fetchall()
    if rows:
        return [f"{r['published_at']} | {r['title'][:90]} | {r['url']}" for r in rows]
    sid = {k: v for k, v in get_sources().items()}.get(source_id, {})
    series = list((sid.get("series") or {}).values())
    out = []
    if series:
        ph = ",".join("?" * len(series))
        for r in conn.execute(f"SELECT series_id,date,value,unit FROM macro_series WHERE series_id IN ({ph}) ORDER BY date DESC, series_id LIMIT 3", series):
            out.append(f"{r['series_id']} | {r['date']} | {r['value']} {r['unit'] or ''}")
    return out[:3]


def render_sources(conn: sqlite3.Connection) -> str:
    lines = ["# Parsing Sources", "", "_Generated from config/sources.yaml and the database. Do not edit by hand._", "",
             "| ID | Source | Type | Priority | Parser | Poll (min) | Status | Last success | Samples |", "|---|---|---|---|---|---|---|---|---|"]
    for r in conn.execute("SELECT * FROM sources ORDER BY source_id"):
        s = _samples(conn, r["source_id"])
        lines.append(f"| {r['source_id']} | {r['name']} | {r['type']} | {r['priority']} | {r['parser']} | {r['schedule_minutes'] or ''} | "
                     f"{r['status']} | {r['last_success_at'] or 'never'} | {len(s)} |")
    lines += ["", "## Samples (3 latest records per source)", ""]
    for r in conn.execute("SELECT source_id,name FROM sources ORDER BY source_id"):
        lines.append(f"### {r['source_id']} - {r['name']}")
        for x in _samples(conn, r["source_id"]) or ["_no records yet_"]:
            lines.append(f"- {x}")
        lines.append("")
    return "\n".join(lines)


def render_passports(conn: sqlite3.Connection) -> str:
    lines = ["# Descriptions of sources", "", "_Template: spec section 4.1. `legal` shows FACTS recorded by `check-sources`; the terms-of-use review is manual._", ""]
    cfg = get_sources()
    for r in conn.execute("SELECT * FROM sources ORDER BY source_id"):
        c = cfg.get(r["source_id"], {})
        legal = json.loads(r["legal_json"] or "{}")
        robots = legal.get("robots", "not checked yet")
        terms = legal.get("terms_checked_at") or "NOT VERIFIED - manual review required"
        terms_url = (c.get("legal") or {}).get("terms_url")
        if terms_url and not legal.get("terms_checked_at"):
            terms = f"{terms}; terms URL: {terms_url}"
        lines += [f"## {r['source_id']} - {r['name']}", "", "| Field | Value |", "|---|---|",
                  f"| id | {r['source_id']} |", f"| name / owner | {r['name']} / {r['owner']} |", f"| url / path | {r['url'] or 'none'} |",
                  f"| type | {r['type']} |", f"| auth | none (public endpoint) |",
                  f"| update_frequency | our poll every {r['schedule_minutes'] or 'n/a'} min |",
                  f"| rate_limit | >= {1.0} s between requests to one host (http.min_interval_s) |",
                  f"| legal | robots.txt: **{robots}** (checked {legal.get('robots_checked_at') or 'never'}); terms of use: **{terms}**; storage allowed in our config: **{legal.get('store', 'n/a')}** |",
                  f"| fields | see connector `{r['parser']}` -> Document / MacroSeries |", f"| quirks | {c.get('quirks', 'none recorded')} |",
                  f"| tier_weight | {r['tier_weight']} |", f"| parser | app/connectors ({r['parser']}) |",
                  f"| status | {r['status']} (checked {r['status_checked_at'] or 'never'}) |",
                  f"| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |",
                  f"| samples | {'; '.join(_samples(conn, r['source_id'])) or 'none yet'} |", ""]
    return "\n".join(lines)


def _ops_value(conn: sqlite3.Connection, mid: str) -> tuple[str, str, str]:
    """Q-23..Q-27 are not single series: show the computed 7-day window values instead of the word 'computed'."""
    from .quality import ingestion_summary

    q = ingestion_summary(conn, 7)
    if mid == "Q-23":
        rows = q["q23_parse_success"]
        if not rows:
            return "", "", "computed: no fetch attempts in the last 7 days"
        att = sum(r["attempts"] for r in rows)
        ok = sum(r["ok"] for r in rows)
        detail = "; ".join(f"{r['source_id']}={r['success_rate']}" for r in rows)
        return f"{ok / att:.4f}", "last 7 days", f"computed (7d) overall; per source: {detail} (target >=0.95)"
    if mid == "Q-24":
        rows = [r for r in q["q24_latency"] if r["p95_s"] is not None]
        if not rows:
            return "", "", "computed: no latency samples in the last 7 days"
        worst = max(rows, key=lambda r: r["p95_s"])
        detail = "; ".join(f"{r['source_id']}: p50={r['p50_s']:.0f}s/p95={r['p95_s']:.0f}s(n={r['n']})" for r in rows)
        return f"p95 {worst['p95_s']:.0f} s", "last 7 days", f"computed (7d) {detail} (cold-start feed; re-measure after polling)"
    if mid == "Q-25":
        by = q["q25_tokens"]["by_operation"]
        tin = sum(b["tokens_in"] for b in by)
        tout = sum(b["tokens_out"] for b in by)
        cost = sum(b["cost_rub"] for b in by)
        today = q["q25_tokens"]["today"]
        return f"{tin} in / {tout} out", today["day"], f"computed (7d): {cost:.2f} RUB spent; today remaining {today['remaining_rub']:.2f} RUB"
    if mid == "Q-26":
        rows = q["q26_duplicates_errors"]
        if not rows:
            return "", "", "computed: no ingestion records in the last 7 days"
        dup = sum(r["duplicate_share"] for r in rows) / len(rows)
        err = sum(r["error_share"] for r in rows) / len(rows)
        return f"dup {dup:.3f} / err {err:.3f}", "last 7 days", "computed (7d) average over sources"
    if mid == "Q-27":
        news = q["q27_processing"]["news_by_day"]
        total = sum(d["received"] for d in news)
        return str(total), news[-1]["day"] if news else "", "computed (7d): documents received by day (classified stage is DA/PE)"
    return "", "", "computed"


def render_metrics(conn: sqlite3.Connection) -> str:
    cat = list(csv.DictReader(open(ROOT / "data" / "ref" / "metrics_catalog.csv", encoding="utf-8-sig")))
    mine = [r for r in cat if "Backend" in r["кто считает"]]
    lines = ["# Metrics collected", "", "_Generated. Covers the Backend-owned/co-owned metrics of metrics_catalog.csv._", "",
             "| ID | Metric | Priority | Source / table | Frequency | Last value | Last date | Status |", "|---|---|---|---|---|---|---|---|"]
    ops = {"Q-23", "Q-24", "Q-25", "Q-26", "Q-27"}
    for r in mine:
        mid = r["id"]
        if mid in ops:
            last_v, last_d, status = _ops_value(conn, mid)
            src, freq, _ = PRODUCED.get(mid, ("-", "-", "computed"))
            lines.append(f"| {mid} | {r['название']} | {r['приоритет']} | {src} | {freq} | {last_v} | {last_d} | {status} |")
            continue
        src, freq, status = PRODUCED.get(mid, ("-", "-", "agent layer (orchestrator/UI), not the data layer" if mid in AGENT_LAYER else "not implemented"))
        last_v = last_d = ""
        for ser in [k for k in ([mid] + [mid + ".weekly", mid + ".yoy"])]:
            row = conn.execute("SELECT date,value FROM macro_series WHERE series_id=? ORDER BY date DESC LIMIT 1", (ser,)).fetchone()
            if row:
                last_v, last_d = str(row["value"]), row["date"]
                break
        if last_v == "" and status.startswith(("collected", "fallback")) and mid.startswith("M-"):
            status += " (no data loaded yet)"
        lines.append(f"| {mid} | {r['название']} | {r['приоритет']} | {src} | {freq} | {last_v} | {last_d} | {status} |")
    return "\n".join(lines)


def render_corpus_verification(conn: sqlite3.Connection) -> str:
    """WP-B5 acceptance evidence: the curated key numbers compared to the stored chunks (read-only re-check)."""
    def _num(v):
        try:
            return float(str(v).replace(",", ".").replace("\u00a0", "").replace(" ", ""))
        except (TypeError, ValueError):
            return None

    cat = list(csv.DictReader(open(ROOT / "data" / "ref" / "key_numbers_to_verify.csv", encoding="utf-8-sig")))
    docs = [dict(r) for r in conn.execute(
        """SELECT a.doc_id, a.parse_status, a.stale,
           (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=a.doc_id) chunks,
           (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=a.doc_id AND c.verified_by='manual') verified
           FROM alfa_docs a ORDER BY a.doc_id""")]
    lines = ["# Corpus key-number verification (WP-B5)", "",
             "_Generated by `report`. Re-run the check with `python -m app.cli corpus-verify-key`._", "",
             "## Corpus state", "", "| Document | Parse status | Stale | Chunks | Verified by manual review |", "|---|---|---:|---:|---:|"]
    for d in docs:
        lines.append(f"| {d['doc_id']} | {d['parse_status']} | {d['stale']} | {d['chunks']} | {d['verified']} |")
    checked = matched = 0
    rows = []
    for row in cat:
        cid = (row.get("chunk_id") or "").strip()
        r = conn.execute("SELECT numbers_json FROM chunks WHERE chunk_id=?", (cid,)).fetchone()
        exp = _num(row.get("value"))
        tol = _num(row.get("tolerance")) or 1e-6
        status = "chunk missing"
        if r:
            checked += 1
            nums = json.loads(r["numbers_json"] or "[]")
            ok = exp is not None and any(abs(float(n.get("value", 1e9)) - exp) <= tol for n in nums)
            status = "match" if ok else "MISMATCH"
            matched += int(ok)
        rows.append((cid, row.get("value"), status, row.get("note") or ""))
    lines += ["", "## Key numbers", "",
              f"Checked **{checked}** of {len(cat)} listed key numbers; **{matched}** match the stored chunks, "
              f"**{checked - matched}** discrepancies." if checked == len(cat) else
              f"Checked **{checked}** of {len(cat)} listed key numbers; **{matched}** match; some chunks are missing.", "",
              "| Chunk | Expected | Result | Subject |", "|---|---:|---|---|"]
    for cid, val, status, note in rows:
        lines.append(f"| {cid} | {val} | {status} | {note} |")
    return "\n".join(lines)


def write_reports(conn: sqlite3.Connection, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {"01_parsing_sources.md": render_sources(conn), "02_sources_descriptions.md": render_passports(conn),
             "03_metrics_collected.md": render_metrics(conn),
             "04_corpus_verification.md": render_corpus_verification(conn)}
    paths = []
    for name, text in files.items():
        p = out / name
        p.write_text(text + "\n", encoding="utf-8")
        paths.append(p)
    return paths
