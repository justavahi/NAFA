"""Browser UI and read-only source page links over the existing API."""
import csv
import json
import os
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class Review(BaseModel):
    confirmed: bool
    text: str | None = None
    numbers: list[dict] | None = None


def install_web(app, *, root, demo):
    from app.api import get_conn, require_admin
    from app.config import get_config
    from app.ingest.alfa_corpus import scan_dir, pages_of, verify_chunk
    from .production import init_profiles
    from .web_auth import init_auth
    assets = Path(root) / "web"
    app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(assets / "index.html")

    @app.get("/public-config")
    def public_config(conn=Depends(get_conn)):
        init_profiles(conn)
        accounts=[]
        if demo:
            tokens=json.loads(os.getenv("NAFA_CLIENT_TOKENS", "{}"))
            for token,uid in tokens.items():
                row=conn.execute("SELECT c.mainokved_name FROM pe_da_profiles p JOIN clients c ON c.synthetic_u=p.client_id WHERE p.client_id=? AND p.synthetic=1",(uid,)).fetchone()
                if row and token.startswith("demo-"):
                    accounts.append(dict(label=(row["mainokved_name"] or uid), token=token))
        return dict(demo=demo, demo_accounts=accounts, name="NAFA · Финансовая аналитика")

    @app.get("/runtime")
    def runtime():
        configured=bool(os.getenv("LLM_BASE_URL") and (os.getenv("LLM_API_KEY") or os.getenv("NAFA_PE_LOCAL")=="true"))
        return dict(demo=demo, model_endpoint_configured=configured, quality_acceptance_complete=False)

    @app.get("/personas")
    def personas():
        with (Path(root) / "data/ref/persona_weights.csv").open(encoding="utf-8-sig") as stream:
            return [{k:r[k] for k in ("persona","name","jtbd","horizon")} for r in csv.DictReader(stream)]

    @app.get("/cards/{card_id}/basis")
    def card_basis(card_id: str, conn=Depends(get_conn)):
        # The session middleware applies the same ownership check as GET/ask/trace.
        # Expose only the curated evidence already sent to PE, never raw records.
        from .backend_bridge import init_bridge_schema
        init_bridge_schema(conn)
        row = conn.execute("SELECT case_json FROM pe_card_contexts WHERE card_id=?", (card_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "card context not found")
        ledger = json.loads(row["case_json"])["ledger"]
        features, calculations = [], []
        for entry in ledger["entries"]:
            if entry["kind"] == "COMPANY_FEATURE":
                features.append({k: entry[k] for k in ("id", "feature_id", "value")})
            elif entry["kind"] in {"COMPUTED_VALUE", "UNKNOWN_EFFECT"}:
                calculations.append({k:v for k,v in entry.items() if k in {"id", "kind", "formula_id", "value", "input_ids", "calculation_ref", "reason", "factor"}})
        return dict(trace_id=ledger["trace_id"], features=features, calculations=calculations)

    @app.get("/analytics/{doc_id}/pages/{page}")
    def original_page(doc_id: str, page: int):
        files=scan_dir(Path(root) / "data/input/alpha").get(doc_id)
        if not files:
            raise HTTPException(404,"source document not found")
        pages=pages_of(files)
        if page<1 or page>len(pages):
            raise HTTPException(404,"source page not found")
        image=pages[page-1].render(110,1600)
        return Response(content=image,media_type="image/png")

    @app.get("/internal/review/chunks", dependencies=[Depends(require_admin)])
    def review_queue(conn=Depends(get_conn)):
        return [dict(r) for r in conn.execute("SELECT chunk_id,doc_id,page,text,numbers_json,block_type,as_of_date,needs_manual_check FROM chunks WHERE needs_manual_check=1 AND block_type!='chart_estimate' ORDER BY doc_id,page LIMIT 100")]

    @app.post("/internal/review/chunks/{chunk_id}", dependencies=[Depends(require_admin)])
    def confirm(chunk_id: str, body: Review, conn=Depends(get_conn)):
        if not body.confirmed:
            raise HTTPException(422,"source page must be reviewed before confirmation")
        row=conn.execute("SELECT block_type FROM chunks WHERE chunk_id=?",(chunk_id,)).fetchone()
        if row is None:
            raise HTTPException(404,"chunk not found")
        if row["block_type"]=="chart_estimate":
            raise HTTPException(422,"unlabelled chart estimate cannot become confirmed fact")
        return dict(verified=verify_chunk(conn,chunk_id,body.text,body.numbers))
