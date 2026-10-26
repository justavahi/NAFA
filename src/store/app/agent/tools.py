"""Validated allow-listed read tools. Only write_trace mutates state by design."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import bank_view as bv
from ..ingest.clients import get_client_record, pseudonym
from ..ingest.macro import get_series
from ..search import search_alfa_analytics, search_news
from ..util import iso, new_id, now_utc


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientProfileArgs(StrictModel):
    client_id: str = Field(min_length=1, max_length=128)

class PaymentFeaturesArgs(StrictModel):
    client_id: str = Field(min_length=1, max_length=128)

class SearchNewsArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    since: str | None = None
    until: str | None = None
    source_ids: list[str] | None = None
    as_of: str | None = None
    k: int = Field(default=10, ge=1, le=50)

class SearchAlfaArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    doc_ids: list[str] | None = None
    as_of: str | None = None
    k: int = Field(default=10, ge=1, le=50)

class MacroArgs(StrictModel):
    series_id: str = Field(min_length=1, max_length=64)
    start: str | None = None
    end: str | None = None

class BankViewArgs(StrictModel):
    indicator: str = Field(min_length=1, max_length=100)
    horizon: str | None = None
    as_of: str | None = None

class ScenarioArgs(StrictModel):
    scenario_type: str
    floating_debt_rub: float | None = Field(default=None, ge=0)
    delta_rate_pp: float | None = None
    fx_volume: float | None = Field(default=None, ge=0)
    spot_from: float | None = Field(default=None, gt=0)
    spot_to: float | None = Field(default=None, gt=0)
    direction: str | None = None
    cogs_rub: float | None = Field(default=None, ge=0)
    pass_through: float | None = Field(default=None, ge=0, le=1)
    delta_inflation_pp: float | None = None

class VerifyClaimsArgs(StrictModel):
    claims: list[dict[str, Any]] = Field(min_length=1, max_length=50)

class WriteTraceArgs(StrictModel):
    trace_id: str = Field(min_length=1, max_length=128)
    card_id: str | None = None
    step: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=100)
    args: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="ok", max_length=30)
    result_ref: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    model: str | None = None
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_rub: float = Field(default=0, ge=0)
    retry_of: int | None = None


def get_payment_features(conn: sqlite3.Connection, client_id: str) -> dict:
    """Aggregated payment features without exposing raw counterparty identifiers."""
    exists = conn.execute("SELECT 1 FROM clients WHERE synthetic_u=?", (client_id,)).fetchone()
    if not exists:
        raise KeyError("client not found")
    rows = conn.execute("""SELECT payer, beneficiary, pay_amt, pay_cnt FROM payments
                          WHERE payer=? OR beneficiary=?""", (client_id, client_id)).fetchall()
    total_out = sum(float(r["pay_amt"] or 0) for r in rows if r["payer"] == client_id)
    total_in = sum(float(r["pay_amt"] or 0) for r in rows if r["beneficiary"] == client_id)
    counterparties: dict[str, float] = {}
    for r in rows:
        if r["payer"] == client_id and r["beneficiary"] != client_id:
            counterparties[r["beneficiary"]] = counterparties.get(r["beneficiary"], 0) + float(r["pay_amt"] or 0)
        elif r["beneficiary"] == client_id and r["payer"] != client_id:
            counterparties[r["payer"]] = counterparties.get(r["payer"], 0) + float(r["pay_amt"] or 0)
    tot = sum(counterparties.values())
    shares = [v / tot for v in counterparties.values()] if tot else []
    return {
        "client_pseudonym": pseudonym(client_id), "has_payment_data": bool(rows),
        "payment_rows": len(rows), "inflow_rub": round(total_in, 2) if rows else None,
        "outflow_rub": round(total_out, 2) if rows else None,
        "counterparty_count": len(counterparties) if rows else None,
        "counterparty_hhi": round(sum(x*x for x in shares), 5) if rows and shares else None,
        "data_note": "Aggregates only; counterparty IDs are intentionally omitted.",
    }


def compute_scenario(args: dict[str, Any]) -> dict:
    """Deterministic FM-15 calculator; only supplied inputs are used; no LLM arithmetic."""
    a = ScenarioArgs.model_validate(args)
    kind = a.scenario_type.lower().strip()
    if kind in {"rate", "interest_expense", "fm15_a"}:
        if a.floating_debt_rub is None or a.delta_rate_pp is None:
            raise ValueError("interest_expense requires floating_debt_rub and delta_rate_pp")
        delta = a.floating_debt_rub * (a.delta_rate_pp / 100.0)
        return {"scenario_type": "interest_expense", "delta_annual_rub": round(delta, 2),
                "formula": "floating_debt_rub * (delta_rate_pp / 100)", "inputs": a.model_dump(exclude_none=True),
                "unit": "RUB/year", "limitations": "Assumes the full debt reprices for a full year."}
    if kind in {"fx", "fx_result", "fm15_b"}:
        if None in (a.fx_volume, a.spot_from, a.spot_to) or a.direction not in {"receipts", "payments"}:
            raise ValueError("fx scenario requires fx_volume, spot_from, spot_to and direction=receipts|payments")
        sign = 1 if a.direction == "receipts" else -1
        delta = a.fx_volume * (a.spot_to - a.spot_from) * sign
        return {"scenario_type": "fx_result", "delta_rub": round(delta, 2),
                "formula": "fx_volume * (spot_to - spot_from) * k; k=+1 receipts, -1 payments",
                "inputs": a.model_dump(exclude_none=True), "unit": "RUB",
                "limitations": "Ignores hedging, taxes and timing differences."}
    if kind in {"inflation", "cost_inflation", "fm15_c"}:
        if None in (a.cogs_rub, a.pass_through, a.delta_inflation_pp):
            raise ValueError("cost_inflation requires cogs_rub, pass_through and delta_inflation_pp")
        delta = a.cogs_rub * a.pass_through * (a.delta_inflation_pp / 100.0)
        return {"scenario_type": "cost_inflation", "delta_cost_rub": round(delta, 2),
                "formula": "cogs_rub * pass_through * (delta_inflation_pp / 100)",
                "inputs": a.model_dump(exclude_none=True), "unit": "RUB",
                "limitations": "Linear first-order approximation over the supplied pass-through share."}
    raise ValueError("unsupported scenario_type; use rate, fx or inflation")


def verify_claims(conn: sqlite3.Connection, claims: list[dict[str, Any]]) -> list[dict]:
    """Conservative claim check: a claim is not 'verified' unless its referenced quote is present verbatim."""
    out = []
    for i, claim in enumerate(claims):
        text = str(claim.get("text", "")).strip()
        source_id = claim.get("doc_id")
        quote = str(claim.get("quote", "")).strip()
        evidence = None
        if source_id and quote:
            row = conn.execute("SELECT title,snippet,text,url FROM documents WHERE doc_id=?", (source_id,)).fetchone()
            if row and quote.casefold() in " ".join(str(row[k] or "") for k in ("title", "snippet", "text")).casefold():
                evidence = {"doc_id": source_id, "url": row["url"], "quote": quote}
            else:
                chunk = conn.execute("SELECT text,doc_id,page FROM chunks WHERE doc_id=? AND instr(lower(text),lower(?))>0 LIMIT 1", (source_id, quote)).fetchone()
                if chunk:
                    evidence = {"doc_id": source_id, "page": chunk["page"], "quote": quote}
        out.append({"index": i, "claim": text, "verdict": "supported_quote" if evidence else "unverified",
                    "evidence": evidence, "note": "Literal quote matching only; semantic entailment requires a human/model critic."})
    return out


def write_trace(conn: sqlite3.Connection, args: dict[str, Any]) -> int:
    a = WriteTraceArgs.model_validate(args)
    # Never persist raw client identifiers or full user-provided news in args_redacted.
    redacted = {k: v for k, v in a.args.items() if not re.search(r"client|payer|beneficiar|account|payment|raw|text", k, re.I)}
    raw = json.dumps(redacted, ensure_ascii=False, default=str)[:4000]
    cur = conn.execute("""INSERT INTO trace_events(trace_id,card_id,step,tool,args_redacted,started_at,duration_ms,model,
                      tokens_in,tokens_out,cost_rub,result_ref,status,retry_of)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (a.trace_id, a.card_id, a.step, a.tool, raw, iso(now_utc()), a.duration_ms, a.model,
                       a.tokens_in, a.tokens_out, a.cost_rub, a.result_ref, a.status, a.retry_of))
    return int(cur.lastrowid)


# Public names and schemas are intentional; callers cannot dispatch arbitrary Python functions.
TOOL_SCHEMAS = {
    "get_client_profile": ClientProfileArgs,
    "get_payment_features": PaymentFeaturesArgs,
    "search_news": SearchNewsArgs,
    "search_alfa_analytics": SearchAlfaArgs,
    "get_macro_series": MacroArgs,
    "get_bank_view": BankViewArgs,
    "compute_scenario": ScenarioArgs,
    "verify_claims": VerifyClaimsArgs,
    "write_trace": WriteTraceArgs,
}


def call_tool(conn: sqlite3.Connection, name: str, arguments: dict[str, Any]) -> Any:
    if name not in TOOL_SCHEMAS:
        raise ValueError(f"tool '{name}' is not allow-listed")
    parsed = TOOL_SCHEMAS[name].model_validate(arguments)
    a = parsed.model_dump(exclude_none=True)
    try:
        if name == "get_client_profile":
            result = get_client_record(conn, a["client_id"])
            if result is None: raise KeyError("client not found")
            result["synthetic_u"] = pseudonym(a["client_id"])
            result.pop("payments_summary", None)  # payment features are a separate aggregate-only tool
            return result
        if name == "get_payment_features": return get_payment_features(conn, a["client_id"])
        if name == "search_news": return search_news(conn, **a)
        if name == "search_alfa_analytics": return search_alfa_analytics(conn, **a)
        if name == "get_macro_series": return get_series(conn, a["series_id"], a.get("start"), a.get("end"))
        if name == "get_bank_view": return bv.get_bank_view(conn, a["indicator"], a.get("horizon"), a.get("as_of"))
        if name == "compute_scenario": return compute_scenario(a)
        if name == "verify_claims": return verify_claims(conn, a["claims"])
        if name == "write_trace": return {"trace_event_id": write_trace(conn, a)}
    except ValidationError:
        raise
    raise ValueError(f"tool '{name}' is not implemented")
