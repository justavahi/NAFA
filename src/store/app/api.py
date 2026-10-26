"""FastAPI data and provisional agent endpoints.
Admin-only endpoints require header X-Admin-Token == NAFA_ADMIN_TOKEN."""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import bank_view as bv
from .db import connect, init_db, sync_sources
from .http import FetchError
from .ingest import byo
from .ingest.clients import get_client_record, list_clients
from .ingest.macro import get_series
from .llm_gateway import usage_today
from .quality import ingestion_summary
from .agent import orchestrator
from .search import search_alfa_analytics, search_news


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db()
    sync_sources(conn)
    conn.close()
    yield


app = FastAPI(title="NAFA data layer", version="0.1.0", lifespan=lifespan)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    if getattr(getattr(request.state, "principal", None), "role", None) == "admin":
        return
    token = os.getenv("NAFA_ADMIN_TOKEN")
    if not token or x_admin_token != token:
        raise HTTPException(401, "admin token required")


@app.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/clients")
def clients(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), conn=Depends(get_conn)):
    principal = getattr(request.state, "principal", None)
    if principal and principal.role == "client":
        row = conn.execute("SELECT synthetic_u,mainokved_code,region,cl_type FROM clients WHERE synthetic_u=?", (principal.client_id,)).fetchone()
        if row is None:
            return []
        from .util import pseudonym
        return [dict(client_id=row["synthetic_u"],pseudonym=pseudonym(row["synthetic_u"]),okved=row["mainokved_code"],region=row["region"],cl_type=row["cl_type"])]
    return list_clients(conn, limit, offset)


@app.get("/clients/{client_id}/profile")
def client_profile(client_id: str, request: Request, conn=Depends(get_conn)):
    """Raw record + flags + payment totals. F/G/E metrics are computed by the DA functions on top of this."""
    rec = get_client_record(conn, client_id)
    if not rec:
        raise HTTPException(404, "client not found")
    if getattr(request.state, "principal", None):
        allowed = {"synthetic_u","mainokved_code","mainokved_name","region","city","cl_type","flags","payments_summary"}
        rec = {key:value for key,value in rec.items() if key in allowed}
    return rec


class IngestIn(BaseModel):
    text: Optional[str] = Field(None, max_length=50000)
    url: Optional[str] = None
    title: Optional[str] = None
    published_at: Optional[str] = None
    client_id: Optional[str] = None
    shared: bool = False


class CardGenerateIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    persona: str = Field(default="PER-03", max_length=64)
    event_doc_id: Optional[str] = None
    query: Optional[str] = Field(None, max_length=500)
    as_of: Optional[str] = None


class CardAskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    scenario: Optional[dict] = None


class CardFeedbackIn(BaseModel):
    helpful: bool
    reason: Optional[str] = Field(None, max_length=500)


@app.post("/events/ingest")
def events_ingest(body: IngestIn, request: Request, conn=Depends(get_conn)):
    """BYO news (C05): text or URL. URL host must be in byo.allowed_domains."""
    if bool(body.text) == bool(body.url):
        raise HTTPException(422, "provide exactly one of: text, url")
    principal = getattr(request.state, "principal", None)
    owner = None
    if principal:
        if principal.role == "client":
            if body.shared or body.client_id not in (None, principal.client_id):
                raise HTTPException(403, "event ownership denied")
            owner = principal.client_id
        else:
            owner = "*" if body.shared else body.client_id
            if not owner:
                raise HTTPException(422, "choose client_id or explicitly share the event")
    try:
        result = byo.ingest_text(conn, body.text, body.title, body.published_at) if body.text else byo.ingest_url(conn, body.url)
        if owner:
            row = conn.execute("SELECT origin FROM documents WHERE doc_id=?",(result["doc_id"],)).fetchone()
            if row and row["origin"] == "byo":
                conn.execute("INSERT OR IGNORE INTO pe_event_owners VALUES(?,?)",(result["doc_id"],owner))
        return result
    except FetchError as e:
        code = 403 if e.error_type in ("domain_not_allowed", "robots_disallowed") else 502
        raise HTTPException(code, f"{e.error_type}: {e.message}")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/feed")
def feed(client_id: str, persona: str = "PER-03", as_of: str | None = None, k: int = Query(5, ge=1, le=5), conn=Depends(get_conn)):
    """Personalised top-5 feed using available client flags and the deterministic scoring baseline."""
    try:
        return orchestrator.feed(conn, client_id, persona, as_of, k)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/cards/generate")
def cards_generate(body: CardGenerateIn, conn=Depends(get_conn)):
    if bool(body.event_doc_id) == bool(body.query):
        raise HTTPException(422, "provide exactly one of event_doc_id or query")
    try:
        return orchestrator.generate_card(conn, client_id=body.client_id, persona=body.persona,
                                          event_doc_id=body.event_doc_id, query=body.query, as_of=body.as_of)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/cards/{card_id}")
def card_get(card_id: str, conn=Depends(get_conn)):
    result = orchestrator.get_card(conn, card_id)
    if result is None:
        raise HTTPException(404, "card not found")
    return result


@app.post("/cards/{card_id}/ask")
def card_ask(card_id: str, body: CardAskIn, conn=Depends(get_conn)):
    try:
        return orchestrator.ask_card(conn, card_id, body.question, body.scenario)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/cards/{card_id}/feedback")
def card_feedback(card_id: str, body: CardFeedbackIn, conn=Depends(get_conn)):
    try:
        return orchestrator.add_feedback(conn, card_id, body.helpful, body.reason)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/cards/{card_id}/trace")
def card_trace(card_id: str, conn=Depends(get_conn)):
    try:
        return orchestrator.card_trace(conn, card_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/sources")
def sources(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT source_id,name,type,url,priority,tier_weight,enabled,status,status_checked_at,last_success_at,last_error,legal_json FROM sources ORDER BY source_id")]


@app.get("/series/{series_id}")
def series(series_id: str, start: str | None = None, end: str | None = None, conn=Depends(get_conn)):
    pts = get_series(conn, series_id, start, end)
    if not pts and not conn.execute("SELECT 1 FROM series_catalog WHERE series_id=?", (series_id,)).fetchone():
        raise HTTPException(404, "unknown series")
    return {"series_id": series_id, "points": pts}


@app.get("/bank-view")
def bank_view_get(indicator: str | None = None, horizon: str | None = None, as_of: str | None = None, conn=Depends(get_conn)):
    return bv.get_bank_view(conn, indicator, horizon, as_of) if indicator else bv.list_bank_view(conn)


@app.post("/bank-view", dependencies=[Depends(require_admin)])
def bank_view_post(rec: bv.BankViewIn, conn=Depends(get_conn)):
    return {"id": bv.add_bank_view(conn, rec)}


@app.get("/search/news")
def api_search_news(request: Request, q: str, k: int = Query(10, ge=1, le=50), since: str | None = None, until: str | None = None,
                    as_of: str | None = None, source_id: list[str] | None = Query(None), conn=Depends(get_conn)):
    principal = getattr(request.state,"principal",None)
    client_id = principal.client_id if principal and principal.role=="client" else None
    return search_news(conn, q, since=since, until=until, as_of=as_of, source_ids=source_id, k=k, client_id=client_id)


@app.get("/search/alfa")
def api_search_alfa(q: str, k: int = Query(10, le=50), doc_id: list[str] | None = Query(None), as_of: str | None = None, conn=Depends(get_conn)):
    return search_alfa_analytics(conn, q, doc_ids=doc_id, as_of=as_of, k=k)


@app.get("/quality/summary")
def quality_summary(days: int = Query(7, ge=1, le=90), conn=Depends(get_conn)):
    return ingestion_summary(conn, days)


@app.get("/quality/ingestion")
def quality_ingestion(days: int = Query(7, ge=1, le=90), conn=Depends(get_conn)):
    return ingestion_summary(conn, days)


@app.get("/budget")
def budget(conn=Depends(get_conn)):
    return {"today": usage_today(conn), "by_operation": ingestion_summary(conn, 7)["q25_tokens"]["by_operation"]}
