"""BankView storage with FM-16 versioning: for each (indicator, horizon) the newest `as_of` is active, older ones are superseded."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .db import tx
from .models import BankViewIn
from .util import iso, now_utc


def _recompute(conn: sqlite3.Connection, indicator: str, horizon: str) -> None:
    rows = conn.execute("SELECT id FROM bank_view WHERE indicator=? AND horizon=? ORDER BY as_of DESC, id DESC",
                        (indicator, horizon)).fetchall()
    prev = None
    for rank, r in enumerate(rows):
        conn.execute("UPDATE bank_view SET version_rank=?, superseded_by=? WHERE id=?", (rank, prev, r["id"]))
        prev = r["id"]


def add_bank_view(conn: sqlite3.Connection, rec: BankViewIn) -> int:
    with tx(conn):
        row = conn.execute(
            "SELECT id FROM bank_view WHERE indicator=? AND horizon=? AND as_of=? AND doc_id IS ? AND value IS ?",
            (rec.indicator, rec.horizon, rec.as_of.isoformat(), rec.doc_id, rec.value)).fetchone()
        if row:
            vid = row["id"]
            if rec.verified_by == "manual":      # manual verification upgrades an existing record
                conn.execute("UPDATE bank_view SET verified_by='manual' WHERE id=?", (vid,))
        else:
            cur = conn.execute(
                """INSERT INTO bank_view(indicator,horizon,value,unit,stance,as_of,doc_id,page,quote_span,extraction_method,
                   verified_by,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rec.indicator, rec.horizon, rec.value, rec.unit, rec.stance, rec.as_of.isoformat(), rec.doc_id, rec.page,
                 rec.quote_span, rec.extraction_method, rec.verified_by, rec.note, iso(now_utc())))
            vid = cur.lastrowid
        _recompute(conn, rec.indicator, rec.horizon)
    return vid


def get_bank_view(conn: sqlite3.Connection, indicator: str, horizon: str | None = None, as_of: str | None = None) -> list[dict]:
    """Tool `get_bank_view`: per horizon the active record as of `as_of` (default: now) plus the version history."""
    hs = [horizon] if horizon else [r["horizon"] for r in conn.execute(
        "SELECT DISTINCT horizon FROM bank_view WHERE indicator=? ORDER BY horizon", (indicator,))]
    out = []
    for h in hs:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM bank_view WHERE indicator=? AND horizon=? ORDER BY as_of DESC, id DESC", (indicator, h))]
        visible = [r for r in rows if not as_of or r["as_of"] <= as_of[:10]]
        if not visible:
            continue
        for i, r in enumerate(visible):
            r["status"] = "active" if i == 0 else "superseded"
        out.append({"indicator": indicator, "horizon": h, "active": visible[0], "history": visible[1:]})
    return out


def list_bank_view(conn: sqlite3.Connection) -> list[dict]:
    keys = conn.execute("SELECT DISTINCT indicator, horizon FROM bank_view ORDER BY indicator, horizon").fetchall()
    res = []
    for k in keys:
        res += get_bank_view(conn, k["indicator"], k["horizon"])
    return res


def seed_from_csv(conn: sqlite3.Connection, path: str | Path) -> int:
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            clean = {k: (v if v not in ("", None) else None) for k, v in row.items()}
            if clean.get("value") is not None:
                clean["value"] = float(clean["value"])
            if clean.get("page") is not None:
                clean["page"] = int(clean["page"])
            add_bank_view(conn, BankViewIn(**clean))
            n += 1
    return n
