"""Local prototype sessions and client isolation, installed only by PE factory."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


@dataclass
class Principal:
    role: str
    client_id: str | None = None


class Login(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def init_auth(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS pe_sessions (session_hash TEXT PRIMARY KEY, role TEXT, client_id TEXT, expires INTEGER, policy TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS pe_event_owners (doc_id TEXT NOT NULL, client_id TEXT NOT NULL, PRIMARY KEY(doc_id,client_id))")


def event_visible(conn, doc_id, client_id):
    row = conn.execute("SELECT origin FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if row is None:
        return False
    if row["origin"] != "byo":
        return True
    return conn.execute("SELECT 1 FROM pe_event_owners WHERE doc_id=? AND client_id IN (?, '*')", (doc_id, client_id)).fetchone() is not None


def install_auth(app, connect):
    admin = os.getenv("NAFA_ADMIN_TOKEN", "")
    if not admin:
        raise ValueError("NAFA_ADMIN_TOKEN required for authenticated prototype")
    tokens = json.loads(os.getenv("NAFA_CLIENT_TOKENS", "{}"))
    if not isinstance(tokens, dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in tokens.items()):
        raise ValueError("NAFA_CLIENT_TOKENS must map token strings to client IDs")
    policy = hashlib.sha256(json.dumps([admin,tokens],sort_keys=True).encode()).hexdigest()

    def from_token(token):
        if hmac.compare_digest(token, admin):
            return Principal("admin")
        return Principal("client", tokens[token]) if token in tokens else None

    @app.post("/session")
    def login(body: Login, request: Request):
        principal = from_token(body.token)
        if principal is None:
            raise HTTPException(401, "Неверный код доступа")
        session = secrets.token_urlsafe(32)
        conn = connect()
        try:
            init_auth(conn)
            conn.execute("DELETE FROM pe_sessions WHERE expires<?", (int(time.time()),))
            conn.execute("INSERT INTO pe_sessions VALUES(?,?,?,?,?)", (hashlib.sha256(session.encode()).hexdigest(),
                principal.role, principal.client_id, int(time.time())+8*3600, policy))
        finally:
            conn.close()
        response = JSONResponse(dict(role=principal.role, client_id=principal.client_id))
        response.set_cookie("nafa_session", session, httponly=True, samesite="strict", secure=request.url.scheme=="https", max_age=8*3600)
        return response

    @app.delete("/session")
    def logout(request: Request):
        conn = connect()
        try:
            init_auth(conn)
            conn.execute("DELETE FROM pe_sessions WHERE session_hash=?", (hashlib.sha256(request.cookies.get("nafa_session", "").encode()).hexdigest(),))
        finally:
            conn.close()
        response = JSONResponse(dict(status="signed_out")); response.delete_cookie("nafa_session")
        return response

    @app.get("/session")
    def session(request: Request):
        principal = request.state.principal
        return dict(role=principal.role, client_id=principal.client_id)

    @app.middleware("http")
    async def authenticate(request, call_next):
        if request.method not in {"GET","HEAD","OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse(dict(detail="cross-origin mutation denied"),status_code=403)
        public = request.url.path in {"/", "/health", "/public-config"} or request.url.path.startswith("/assets/") or (request.url.path=="/session" and request.method=="POST")
        if not public:
            principal = None
            supplied_admin = request.headers.get("x-admin-token", "")
            authorization = request.headers.get("authorization", "")
            if supplied_admin and hmac.compare_digest(supplied_admin, admin):
                principal = Principal("admin")
            elif authorization.startswith("Bearer "):
                principal = from_token(authorization[7:])
            conn = connect()
            try:
                init_auth(conn)
                if principal is None:
                    session_hash = hashlib.sha256(request.cookies.get("nafa_session", "").encode()).hexdigest()
                    row = conn.execute("SELECT * FROM pe_sessions WHERE session_hash=? AND expires>? AND policy=?", (session_hash,int(time.time()),policy)).fetchone()
                    if row:
                        principal = Principal(row["role"], row["client_id"])
                if principal is None:
                    return JSONResponse(dict(detail="Войдите в сервис"),status_code=401)
                request.state.principal = principal
                path = request.url.path
                if principal.role != "admin":
                    if path.startswith(("/internal/", "/quality", "/budget", "/docs", "/openapi")) or (path=="/bank-view" and request.method!="GET"):
                        return JSONResponse(dict(detail="admin access required"),status_code=403)
                    if path.startswith("/clients/") and path.split("/")[2] != principal.client_id:
                        return JSONResponse(dict(detail="client access denied"),status_code=403)
                    if path=="/feed" and request.query_params.get("client_id") not in (None,principal.client_id):
                        return JSONResponse(dict(detail="client access denied"),status_code=403)
                    if path=="/cards/generate":
                        try:
                            body = await request.json()
                        except ValueError:
                            return JSONResponse(dict(detail="invalid JSON"),status_code=422)
                        if not isinstance(body,dict) or body.get("client_id") != principal.client_id:
                            return JSONResponse(dict(detail="client access denied"),status_code=403)
                    if path.startswith("/cards/") and path!="/cards/generate":
                        row = conn.execute("SELECT client_id FROM cards WHERE card_id=?",(path.split("/")[2],)).fetchone()
                        if row is None or row["client_id"] != principal.client_id:
                            return JSONResponse(dict(detail="card not found"),status_code=404)
            finally:
                conn.close()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="same-origin"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"]="no-store"
        return response
