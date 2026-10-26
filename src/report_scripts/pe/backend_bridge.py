"""PE orchestration behind the existing Backend API and shared SQLite store.

Prepared cases are registered by trusted DA/backend code, never by HTTP clients.
Only live, verified results enter the public cards table.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import uuid
from typing import Literal
from pydantic import Field

from .evidence import check_card_provenance
from .integration import backend_payload
from .pipeline import CaseInput, run_case
from .schemas import InsightCard, StrictModel
from .session import PromptSession
from .tasks import run_task


class PreparedCase(StrictModel):
    case: CaseInput
    selected_news_ids: list[str] | None = None
    profile: Literal["connected_v1"] | None = None
    rank_score: float | None = Field(default=None, ge=0, le=1)
    preparation_calls: list[dict] = Field(default_factory=list)
    profile_revision: str | None = None
    synthetic: bool = False


class Withheld(RuntimeError):
    def __init__(self, trace_id, reason="Проверка ответа не пройдена. Карточка не опубликована.", *, code="verification_failed"):
        super().__init__(reason)
        self.trace_id, self.reason = trace_id, reason
        self.code = code


def stamp():
    return datetime.now(timezone.utc).isoformat()


def generation_signature(manifest):
    from .prompts import PROMPT_DIR, prepare_request
    prompts = ["PRM-05", "PRM-06", "PRM-07"]
    value = [manifest, [prepare_request(p, {}, manifest=manifest)["prompt_hash"] for p in prompts]]
    files = [PROMPT_DIR / manifest["common_system"]] + [PROMPT_DIR / manifest["prompts"][p]["file"] for p in prompts]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), max(p.stat().st_mtime for p in files)


def interpretation_inputs(case):
    value = case.model_dump(mode="json")
    value["ledger"]["trace_id"] = ""
    # Seen-item novelty and freshness change the feed's rank, not the interpretation.
    value["ledger"]["entries"] = [e for e in value["ledger"]["entries"] if e["id"] != "SCORE"]
    return value


@contextmanager
def atomic(conn):
    if conn.in_transaction:
        raise RuntimeError("call PE outside an existing write transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def init_bridge_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS pe_event_owners (doc_id TEXT NOT NULL,client_id TEXT NOT NULL,PRIMARY KEY(doc_id,client_id))")
    conn.execute("""CREATE TABLE IF NOT EXISTS pe_prepared_cases (
        client_id TEXT NOT NULL, event_doc_id TEXT NOT NULL, persona TEXT NOT NULL,
        as_of TEXT NOT NULL, prepared_json TEXT NOT NULL,
        PRIMARY KEY(client_id,event_doc_id,persona,as_of))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pe_card_contexts (
        card_id TEXT PRIMARY KEY REFERENCES cards(card_id), case_json TEXT NOT NULL,
        card_json TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pe_runs (
        trace_id TEXT PRIMARY KEY, card_id TEXT, kind TEXT NOT NULL,
        result_json TEXT NOT NULL, created_at TEXT NOT NULL)""")


def validate_binding(conn, case, client_id, event_doc_id, persona, pseudonym, as_of=None):
    if not conn.execute("SELECT 1 FROM clients WHERE synthetic_u=?", (client_id,)).fetchone():
        raise KeyError("client not found")
    row = conn.execute("SELECT * FROM documents WHERE doc_id=? AND ready_at IS NOT NULL", (event_doc_id,)).fetchone()
    if row is None:
        raise KeyError("event not found or unavailable")
    if case.persona != persona or case.ledger.client_id != pseudonym(client_id):
        raise ValueError("prepared case belongs to another client or persona")
    if row["origin"] == "byo" and not conn.execute("SELECT 1 FROM pe_event_owners WHERE doc_id=? AND client_id IN (?, '*')",(event_doc_id,client_id)).fetchone():
        raise KeyError("event not available for this client")
    news = [d for d in case.ledger.documents if d.kind == "news" and d.id == event_doc_id]
    if len(news) != 1 or news[0].text != (row["text"] or row["snippet"] or ""):
        raise ValueError("prepared event text differs from the current stored source")
    if as_of:
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        cutoff = cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff.astimezone(timezone.utc)
        published = datetime.fromisoformat(row["published_at_utc"].replace("Z", "+00:00"))
        published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published.astimezone(timezone.utc)
        if published > cutoff or any(d.as_of and d.as_of > cutoff.date() for d in case.ledger.documents):
            raise ValueError("prepared sources contain information after requested as_of")
    return dict(row)


def register_prepared_case(conn, *, client_id, event_doc_id, prepared, pseudonym, as_of=None):
    """Internal DA handoff; prepared values/lineage must come from DA, not heuristics."""
    prepared = PreparedCase.model_validate(prepared)
    init_bridge_schema(conn)
    validate_binding(conn, prepared.case, client_id, event_doc_id, prepared.case.persona, pseudonym, as_of)
    init_bridge_schema(conn)
    with atomic(conn):
        conn.execute("""INSERT INTO pe_prepared_cases VALUES(?,?,?,?,?)
            ON CONFLICT(client_id,event_doc_id,persona,as_of) DO UPDATE SET prepared_json=excluded.prepared_json""",
            (client_id, event_doc_id, prepared.case.persona, as_of or "", prepared.model_dump_json()))


class BackendBridge:
    def __init__(self, *, gateway_factory, pseudonym, search_news, case_provider=None, manifest=None, demo=False):
        self.gateway_factory, self.pseudonym, self.search_news = gateway_factory, pseudonym, search_news
        self.case_provider = case_provider
        self.demo = demo
        self.manifest = json.loads(json.dumps(manifest)) if manifest else None

    def _prepared(self, conn, *, client_id, event_doc_id, persona, as_of, trace_id):
        init_bridge_schema(conn)
        row = conn.execute("""SELECT prepared_json FROM pe_prepared_cases
            WHERE client_id=? AND event_doc_id=? AND persona=? AND as_of=?""",
            (client_id, event_doc_id, persona, as_of or "")).fetchone()
        if row is None:
            if self.case_provider:
                kwargs = dict(client_id=client_id, event_doc_id=event_doc_id, persona=persona, as_of=as_of)
                if getattr(self.case_provider, "accepts_trace_id", False):
                    kwargs["trace_id"] = trace_id
                return PreparedCase.model_validate(self.case_provider(conn, **kwargs))
            raise ValueError("PE_CASE_NOT_READY: backend/DA must register a CaseInput for this client, event, persona and as_of")
        return PreparedCase.model_validate_json(row["prepared_json"])

    def _audit(self, conn, trace_id, kind, result, card_id=None):
        safe = {k: v for k, v in result.items() if k != "requests"}
        conn.execute("INSERT INTO pe_runs VALUES(?,?,?,?,?)",
            (trace_id, card_id, kind, json.dumps(safe, ensure_ascii=False), stamp()))
        for call in result.get("calls", []):
            # No raw client IDs, prompts, user text or source text in public trace.
            conn.execute("""INSERT INTO trace_events
                (trace_id,card_id,step,tool,args_redacted,started_at,duration_ms,model,tokens_in,tokens_out,cost_rub,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (trace_id, card_id, kind, call.get("prompt_id"),
                json.dumps({k: call.get(k) for k in ("version", "attempt", "prompt_hash", "input_hash")}), stamp(),
                call.get("elapsed_ms"), call.get("executed_model"), call.get("tokens_in"), call.get("tokens_out"),
                call.get("cost_rub"), call.get("status")))

    def generate(self, conn, *, client_id, persona="PER-03", event_doc_id=None, query=None, as_of=None):
        if conn.in_transaction:
            raise RuntimeError("call PE outside an existing write transaction")
        init_bridge_schema(conn)
        if not conn.execute("SELECT 1 FROM clients WHERE synthetic_u=?", (client_id,)).fetchone():
            raise KeyError("client not found")
        if bool(event_doc_id) == bool(query):
            raise ValueError("provide exactly one of event_doc_id or query")
        if query:
            hits = self.search_news(conn, query, as_of=as_of, k=1, client_id=client_id)
            if not hits:
                raise KeyError("event not found")
            event_doc_id = hits[0]["doc_id"]
        trace_id = "tr_" + uuid.uuid4().hex
        try:
            prepared = self._prepared(conn, client_id=client_id, event_doc_id=event_doc_id, persona=persona, as_of=as_of, trace_id=trace_id)
        except RuntimeError as exc:
            with atomic(conn):
                self._audit(conn, trace_id, "preparation_withheld", dict(status="needs_fix", issues=[type(exc).__name__], calls=[]))
            unconfigured = str(exc).startswith("LLM_NOT_CONFIGURED")
            raise Withheld(trace_id,
                "Модели не подключены. Нужна настройка API." if unconfigured else "Не удалось обработать событие. Попробуйте позже.",
                code="model_not_configured" if unconfigured else "preparation_failed") from exc
        case = prepared.case.model_copy(deep=True)
        event = validate_binding(conn, case, client_id, event_doc_id, persona, self.pseudonym, as_of)
        case.ledger.trace_id = trace_id
        manifest = self.manifest
        if manifest is None and prepared.profile:
            from .prompts import PROMPT_DIR
            manifest = json.loads((PROMPT_DIR / "profiles" / (prepared.profile+".json")).read_text())
        signature = None
        if prepared.profile and prepared.profile_revision:
            signature, changed_at = generation_signature(manifest)
            rows = conn.execute("""SELECT c.payload_json,c.as_of,c.created_at,p.case_json,p.card_json
                FROM cards c JOIN pe_card_contexts p ON p.card_id=c.card_id
                WHERE c.client_id=? AND c.event_doc_id=? AND c.persona=? ORDER BY c.created_at DESC LIMIT 5""",
                (client_id,event_doc_id,persona)).fetchall()
            for saved in rows:
                payload = json.loads(saved["payload_json"])
                metadata = payload.get("pe", {})
                expected_status = "demo" if self.demo else "verified"
                if payload.get("status") != expected_status or payload.get("publishable") != (not self.demo):
                    continue
                if as_of and saved["as_of"] != as_of:
                    continue
                if metadata.get("profile_revision") != prepared.profile_revision:
                    continue
                previous_signature = metadata.get("generation_signature")
                if previous_signature != signature:
                    # Migrate a pre-signature result only when it postdates the
                    # current prompts and has exactly the same curated inputs.
                    if previous_signature or datetime.fromisoformat(saved["created_at"]).timestamp() < changed_at:
                        continue
                if any(payload["layers"]["fact"].get(k) != event[k] for k in ("title","url","published_at","source_id")):
                    continue
                previous = CaseInput.model_validate_json(saved["case_json"])
                if interpretation_inputs(previous) != interpretation_inputs(case):
                    continue
                checked = InsightCard.model_validate_json(saved["card_json"])
                if not check_card_provenance(checked, previous.ledger, allow_mock=self.demo).ok:
                    continue
                payload["pe"]["generation_signature"] = signature
                with atomic(conn):
                    conn.execute("UPDATE cards SET payload_json=? WHERE card_id=?", (json.dumps(payload,ensure_ascii=False),payload["card_id"]))
                    self._audit(conn,trace_id,"generation_reused",dict(status="reused",calls=[]),payload["card_id"])
                return payload | {"reused": True}
        try:
            gateway = self.gateway_factory(conn)
        except RuntimeError as exc:
            with atomic(conn):
                self._audit(conn, trace_id, "generation_withheld", dict(status="needs_fix", issues=[type(exc).__name__], calls=[]))
            raise Withheld(trace_id, "Модели недоступны. Попробуйте позже.") from exc
        try:
            result = run_case(case, gateway, manifest=manifest, selected_news_ids=prepared.selected_news_ids)
        finally:
            gateway.close()  # HTTP client only; the host owns conn and its budget DB.
        result["calls"] = prepared.preparation_calls + result["calls"]
        card = InsightCard.model_validate(result["card"]) if result.get("card") else None
        allowed = (result.get("mode") == "live" and result.get("status") == "verified"
                   and result.get("publishable") is True and not result.get("issues") and card is not None)
        is_demo = self.demo and result.get("mode") == "evaluation" and result.get("status") == "evaluated" and not result.get("issues") and card is not None
        allowed = allowed or is_demo
        if allowed:
            allowed = check_card_provenance(card, case.ledger, allow_mock=is_demo).ok
        if not allowed:
            with atomic(conn):
                self._audit(conn, trace_id, "generation_withheld", result)
            raise Withheld(trace_id)
        card_id = "card_" + uuid.uuid4().hex
        payload = backend_payload(card, case, card_id=card_id)
        payload["layers"]["fact"].update({key: event[key] for key in ("doc_id", "title", "url", "published_at", "source_id")})
        payload["layers"]["ai_conclusion"]["text"] = " ".join(c.text for c in card.model_inference.impact_chain)
        payload.update(status="demo" if is_demo else "verified", publishable=not is_demo, demo=is_demo, partial=bool(result.get("partial")),
            unknowns=result.get("unknowns", []), discarded_claims=result.get("discarded_claims", []),
            pe=dict(schema_version=1, mode="evaluation" if is_demo else "live", profile_revision=prepared.profile_revision,
                generation_signature=signature))
        # No fabricated ranking score: FM-06 relevance is not the feed's FM-13.
        payload["score"].update(score=prepared.rank_score, rank_available=prepared.rank_score is not None)
        with atomic(conn):
            conn.execute("""INSERT INTO cards(card_id,trace_id,client_id,persona,event_doc_id,as_of,score,payload_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (card_id, trace_id, client_id, persona, event_doc_id,
                as_of or stamp(), prepared.rank_score, json.dumps(payload, ensure_ascii=False), stamp()))
            conn.execute("INSERT INTO pe_card_contexts VALUES(?,?,?)", (card_id, case.model_dump_json(), card.model_dump_json()))
            self._audit(conn, trace_id, "generation_verified", result, card_id)
        return payload

    def ask(self, conn, card_id, question):
        if conn.in_transaction:
            raise RuntimeError("call PE outside an existing write transaction")
        row = conn.execute("SELECT * FROM pe_card_contexts WHERE card_id=?", (card_id,)).fetchone()
        if row is None:
            raise Withheld("tr_" + uuid.uuid4().hex, "Отсутствует проверенный контекст карточки.")
        case = CaseInput.model_validate_json(row["case_json"])
        trace_id = case.ledger.trace_id
        audit_id = "ask_" + uuid.uuid4().hex
        try:
            gateway = self.gateway_factory(conn)
        except RuntimeError as exc:
            with atomic(conn):
                self._audit(conn, audit_id, "dialogue_withheld", dict(status="needs_fix", issues=[type(exc).__name__], calls=[]), card_id)
            raise Withheld(trace_id, "Модели недоступны. Попробуйте позже.") from exc
        session = PromptSession(gateway, trace_id, max_calls=4, overrides={"PRM-08": "GLM-5.3-Flash"})
        try:
            # GLM writer + Qwen critic: different families also for dialogue.
            result = run_task("PRM-08", dict(card=json.loads(row["card_json"]), ledger=case.ledger.model_dump(mode="json"),
                question=question, computed=case.computed()), gateway, session=session)
        except (ValueError, RuntimeError) as exc:
            result = dict(status="needs_fix", publishable=False, issues=[type(exc).__name__], calls=session.records)
        finally:
            gateway.close()
        is_demo = self.demo and result.get("mode") == "evaluation" and result.get("status") == "checked" and not result.get("issues")
        allowed = (result.get("publishable") is True and result.get("mode") == "live" and not result.get("issues")) or is_demo
        with atomic(conn):
            # Audit rows have unique IDs; calls still join the original card trace.
            self._audit(conn, audit_id, "dialogue_verified" if allowed else "dialogue_withheld", result, card_id)
            conn.execute("UPDATE trace_events SET trace_id=? WHERE trace_id=?", (trace_id, audit_id))
            if allowed:
                answer = dict(mode="pe_grounded_answer", card_id=card_id, trace_id=trace_id,
                    publishable=not is_demo, status="demo" if is_demo else "verified", demo=is_demo, **result["output"])
                conn.execute("INSERT INTO card_questions(card_id,question,answer_json,trace_id,created_at) VALUES(?,?,?,?,?)",
                    (card_id, question, json.dumps(answer, ensure_ascii=False), trace_id, stamp()))
        if not allowed:
            raise Withheld(trace_id, "Ответ не прошёл проверку и не опубликован.")
        return answer


def install_backend_bridge(api, orchestrator, bridge):
    """Install explicitly at startup; existing URLs and request schemas remain."""
    from fastapi.responses import JSONResponse
    if getattr(orchestrator, "_pe_bridge", None) is not None:
        raise RuntimeError("PE bridge already installed")
    old_generate, old_ask, old_feed = orchestrator.generate_card, orchestrator.ask_card, orchestrator.feed

    def generate(conn, **kwargs):
        kwargs["persona"] = orchestrator._persona_code(kwargs.get("persona"))
        return bridge.generate(conn, **kwargs)

    def ask(conn, card_id, question, scenario=None):
        card = orchestrator.get_card(conn, card_id)
        if card is None:
            raise KeyError("card not found")
        if not card.get("pe") or scenario is not None:
            return old_ask(conn, card_id, question, scenario)
        return bridge.ask(conn, card_id, question)

    async def withheld_handler(request, exc):
        return JSONResponse(status_code=409, content=dict(detail=dict(status="withheld", publishable=False,
            card=None, trace_id=exc.trace_id, message=exc.reason, code=exc.code)))

    old_handler = api.exception_handlers.get(Withheld)
    api.add_exception_handler(Withheld, withheld_handler)
    orchestrator.generate_card, orchestrator.ask_card = generate, ask
    if bridge.case_provider and hasattr(bridge.case_provider, "feed"):
        orchestrator.feed = bridge.case_provider.feed
    orchestrator._pe_bridge = bridge

    def uninstall():
        orchestrator.generate_card, orchestrator.ask_card = old_generate, old_ask
        orchestrator.feed = old_feed
        del orchestrator._pe_bridge
        if old_handler:
            api.exception_handlers[Withheld] = old_handler
        else:
            api.exception_handlers.pop(Withheld, None)
    return uninstall
