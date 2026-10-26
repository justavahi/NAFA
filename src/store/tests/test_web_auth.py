"""Real session + API isolation, with controlled model responses (not quality scores)."""
import json
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / "report_scripts"))
from tests.test_automatic_pe import production
from pe.backend_bridge import BackendBridge, install_backend_bridge
from pe.web_auth import install_auth
from pe.web_ui import install_web
from app import api
from app.agent import orchestrator
from app.db import connect
from app.search import search_news
from app.util import pseudonym


@pytest.fixture
def web(conn, production, monkeypatch):
    uid,models,provider=production
    conn.execute("INSERT INTO clients(synthetic_u) VALUES('another-company')")
    monkeypatch.setenv("NAFA_CLIENT_TOKENS",json.dumps({"demo-own":uid,"other-token":"another-company"}))
    application=FastAPI(lifespan=api.lifespan)
    application.include_router(api.app.router)
    service=BackendBridge(gateway_factory=lambda c:models,pseudonym=pseudonym,search_news=search_news,case_provider=provider,demo=True)
    uninstall=install_backend_bridge(application,orchestrator,service)
    install_web(application,root=Path(__file__).resolve().parents[1],demo=True)
    install_auth(application,connect)
    try:
        with TestClient(application) as client:
            yield client,uid,models,provider
    finally:
        uninstall()


def sign_in(client,token="demo-own"):
    result=client.post("/session",json={"token":token})
    assert result.status_code==200
    return result


def test_public_entry_login_private_api_logout(web):
    client,uid,_,_=web
    assert client.get("/").status_code==200
    assert "default-src 'self'" in client.get("/").headers["content-security-policy"]
    assert client.get("/assets/app.js").status_code==200
    assert client.get("/runtime").status_code==401
    assert client.post("/session",json={"token":"wrong"}).status_code==401
    assert "tok" not in [a["token"] for a in client.get("/public-config").json()["demo_accounts"]]
    response=sign_in(client)
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/session").json()==dict(role="client",client_id=uid)
    assert client.get("/runtime").json()["demo"]
    assert client.delete("/session").status_code==200
    assert client.get("/clients").status_code==401


def test_client_cannot_read_another_profile_feed_card_or_admin_api(web):
    client,uid,models,_=web
    sign_in(client)
    assert [r["client_id"] for r in client.get("/clients").json()]==[uid]
    assert "extra_json" not in client.get(f"/clients/{uid}/profile").json()
    for path in ["/clients/another-company/profile","/feed?client_id=another-company","/budget","/quality/summary","/openapi.json","/internal/review/chunks"]:
        assert client.get(path).status_code==403,path
    assert client.post("/cards/generate",json={"client_id":"another-company","event_doc_id":"new-event"}).status_code==403
    response=client.post("/cards/generate",json={"client_id":uid,"event_doc_id":"new-event"})
    assert response.status_code==200,response.text
    card=response.json()["card_id"]
    assert client.get(f"/cards/{card}/trace").status_code==200
    basis=client.get(f"/cards/{card}/basis")
    assert basis.status_code==200
    assert basis.json()["features"] and basis.json()["calculations"]
    assert uid not in json.dumps(basis.json())
    assert all(set(f)=={"id","feature_id","value"} for f in basis.json()["features"])
    assert client.post(f"/cards/{card}/feedback",json={"helpful":True}).status_code==200
    sign_in(client,"other-token")
    for suffix in ["","/trace","/basis"]:
        assert client.get(f"/cards/{card}"+suffix).status_code==404
    for suffix,body in [("ask",{"question":"Чужая карточка?"}),("feedback",{"helpful":True})]:
        assert client.post(f"/cards/{card}/{suffix}",json=body).status_code==404


def test_private_news_cannot_be_searched_or_generated_by_another_client(web):
    client,uid,models,provider=web
    sign_in(client)
    result=client.post("/events/ingest",json={"text":"Уникальный закрытый текст про ключевую ставку и кредитование.","published_at":"2026-10-01T00:00:00Z"})
    assert result.status_code==200,result.text
    doc=result.json()["doc_id"]
    assert any(r["doc_id"]==doc for r in client.get("/search/news?q=Уникальный").json())
    assert client.post("/events/ingest",json={"text":"Другой текст","client_id":"another-company"}).status_code==403
    sign_in(client,"other-token")
    assert client.get("/search/news?q=Уникальный").json()==[]
    # Even a guessed ID is checked before extraction/model calls.
    before=len(models.calls)
    from pe.production import get_profile
    with connect() as db:
        row=db.execute("SELECT * FROM pe_da_profiles WHERE client_id=?",(uid,)).fetchone()
        from pe.production import client_hash
        raw=db.execute("SELECT * FROM clients WHERE synthetic_u='another-company'").fetchone()
        db.execute("INSERT INTO pe_da_profiles VALUES(?,?,?,?,?,?)",("another-company",row["profile_json"],client_hash(raw),row["revision"],1,0))
    response=client.post("/cards/generate",json={"client_id":"another-company","event_doc_id":doc})
    assert response.status_code==404,response.text
    assert len(models.calls)==before


def test_admin_cookie_and_csrf_protection(web):
    client,uid,_,_=web
    sign_in(client,"tok")
    assert client.get("/budget").status_code==200
    assert client.get("/internal/review/chunks").status_code==200
    assert client.post("/events/ingest",json={"text":"Регулятор опубликовал решение о ставке для компаний."}).status_code==422
    assert client.post("/events/ingest",json={"text":"Регулятор опубликовал решение о ставке для компаний.","client_id":uid},headers={"Origin":"https://evil.invalid"}).status_code==403
    assert client.post("/session",json={"token":"demo-own"},headers={"Origin":"https://evil.invalid"}).status_code==403


def test_bank_excerpt_only_enters_context_after_explicit_original_review(web, conn):
    client,uid,_,provider=web
    conn.execute("INSERT INTO alfa_docs(doc_id,doc_date) VALUES('DOC-07','2026-09-11')")
    for chunk,kind in [("review-text","text"),("review-chart","chart_estimate")]:
        conn.execute("INSERT INTO chunks(chunk_id,doc_id,page,block_type,text,needs_manual_check) VALUES(?,'DOC-07',1,?,'Ключевая ставка сохраняется.',1)",(chunk,kind))
        from app.search import index_chunk
        index_chunk(conn,chunk,"Ключевая ставка сохраняется.")
    event=provider(conn,client_id=uid,event_doc_id="new-event",persona="PER-03").case.event
    assert provider.bank_context(conn,event,None)[2]=="missing"
    sign_in(client,"tok")
    queue=client.get("/internal/review/chunks").json()
    assert [row["chunk_id"] for row in queue]==["review-text"]
    assert client.post("/internal/review/chunks/review-text",json={"confirmed":False}).status_code==422
    assert client.post("/internal/review/chunks/review-chart",json={"confirmed":True}).status_code==422
    page=client.get("/analytics/DOC-07/pages/1")
    assert page.status_code==200 and page.content.startswith(b'\x89PNG'),page.text[:50] if page.status_code!=200 else ""
    assert client.get("/analytics/DOC-07/pages/0").status_code==404
    assert client.post("/internal/review/chunks/review-text",json={"confirmed":True,"text":"Ключевая ставка сохраняется.","numbers":[]}).json()["verified"]
    docs,entries,status=provider.bank_context(conn,event,None)
    assert status=="available" and len(entries)==1 and docs[0]["page"]==1
