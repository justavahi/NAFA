"""Factory for the existing Backend FastAPI app with PE explicitly installed."""
from __future__ import annotations

import importlib
import inspect
import json
import os
from pathlib import Path
import sys

from .backend_bridge import BackendBridge, PreparedCase, install_backend_bridge, register_prepared_case
from .prompts import PROMPT_DIR
from .run import check_gateway_settings
from .schemas import StrictModel
from pydantic import Field


class RegistrationInput(StrictModel):
    client_id: str = Field(min_length=1, max_length=128)
    event_doc_id: str = Field(min_length=1)
    prepared: PreparedCase
    as_of: str | None = None


class SharedGateway:
    mode = "live"

    def __init__(self, native, *, cost_unknown=False):
        self.native, self.cost_unknown = native, cost_unknown

    def chat(self, **kwargs):
        try:
            result = self.native.chat(**kwargs)
        except Exception as exc:
            # Keep credentials/HTTP response bodies out of model repair feedback.
            raise RuntimeError("Backend LLM call failed: " + type(exc).__name__) from exc
        if self.cost_unknown:
            result.cost_rub = None
        return result

    def close(self):
        self.native.close()  # Never close the host's SQLite connection.


def create_app(*, gateway_factory=None, case_provider=None):
    root = Path(os.environ["NAFA_BACKEND_ROOT"]).resolve()
    if not (root / "app/api.py").is_file():
        raise ValueError("NAFA_BACKEND_ROOT must point to the Backend store/ directory")
    os.environ.setdefault("NAFA_ROOT", str(root))
    sys.path.insert(0, str(root))
    config = importlib.import_module("app.config")
    if root not in Path(config.__file__).resolve().parents:
        raise RuntimeError("a different package named app is already imported")
    api = importlib.import_module("app.api")
    orchestrator = importlib.import_module("app.agent.orchestrator")
    util = importlib.import_module("app.util")
    search = importlib.import_module("app.search")
    demo = os.getenv("NAFA_DEMO_MODE", "").lower() == "true"
    if gateway_factory is None:
        gateway_module = importlib.import_module("app.llm_gateway")
        if "request_options" not in inspect.signature(gateway_module.LLMGateway.chat).parameters:
            raise RuntimeError("apply pe.gateway_patch.patch_gateway to Backend app/llm_gateway.py before startup")
        local = os.getenv("NAFA_PE_LOCAL", "").lower() == "true"
        models = config.get_models()
        if not local:
            settings = json.loads((PROMPT_DIR.parent / "config/organiser_models.json").read_text())
            for route in models.values():
                if route["model"] in settings["models"]:
                    route["api_name"] = settings["models"][route["model"]]
        unknown = not local and any(r.get("p_in_rub_per_1k") == 0 or r.get("p_out_rub_per_1k") == 0 for r in models.values())
        def gateway_factory(conn):
            try:
                check_gateway_settings(os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", ""),
                    models, local=local, server_budget=not local)
            except ValueError as exc:
                raise RuntimeError("LLM_NOT_CONFIGURED: check model API settings") from exc
            gateway = SharedGateway(gateway_module.LLMGateway(conn), cost_unknown=unknown)
            gateway.mode = "evaluation" if demo else "live"
            return gateway
    if case_provider is None and os.getenv("NAFA_AUTOMATIC_CASES", "true").lower() == "true":
        from .production import ProductionProvider
        case_provider = ProductionProvider(gateway_factory=gateway_factory, pseudonym=util.pseudonym, store_root=root)
    profile = os.getenv("NAFA_PE_PROFILE")
    manifest = None
    if profile:
        path = PROMPT_DIR / "profiles" / (profile + ".json")
        if Path(profile).name != profile or not path.is_file():
            raise ValueError("unknown PE profile")
        manifest = json.loads(path.read_text())
    bridge = BackendBridge(gateway_factory=gateway_factory, pseudonym=util.pseudonym,
        search_news=search.search_news, case_provider=case_provider, manifest=manifest, demo=demo)
    install_backend_bridge(api.app, orchestrator, bridge)
    from fastapi import Depends, HTTPException

    @api.app.post("/internal/pe/cases", dependencies=[Depends(api.require_admin)])
    def register(body: RegistrationInput, conn=Depends(api.get_conn)):
        try:
            register_prepared_case(conn, client_id=body.client_id, event_doc_id=body.event_doc_id,
                prepared=body.prepared, pseudonym=util.pseudonym, as_of=body.as_of)
        except KeyError as exc:
            raise HTTPException(404, "client or event not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return dict(status="ready", persona=body.prepared.case.persona)

    api.app.state.pe_bridge = bridge
    from .web_ui import install_web
    install_web(api.app, root=root, demo=demo)
    if os.getenv("NAFA_AUTH_DISABLED", "").lower() != "true":
        from .web_auth import install_auth
        db = importlib.import_module("app.db")
        install_auth(api.app, db.connect)
    return api.app
