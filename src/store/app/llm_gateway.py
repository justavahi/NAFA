"""WP-B9 (minimal, enough for corpus parsing): single client for the allowed models; role -> model routing from config/models.yaml;
retry with backoff; cache by request hash; tokens and RUB logged for every call; daily budget guard (soft 80%, hard 100%).
Cache hits stay available after the hard limit, so the system can answer from cache or refuse honestly."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass

import httpx

from .config import get_config, get_models
from .db import llm_ledger
from .util import iso, local_tz, now_utc


class BudgetExceeded(Exception):
    pass


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_rub: float
    cache_hit: bool
    soft_limit_reached: bool = False


def _today() -> str:
    return now_utc().astimezone(local_tz()).date().isoformat()


def usage_today(conn: sqlite3.Connection) -> dict:
    with llm_ledger(conn) as ledger:
        return _usage_today(ledger)


def _usage_today(conn: sqlite3.Connection) -> dict:
    """Spend of the current (Moscow) day, excluding cache hits. Drives the soft (80%) and hard (100%) limits."""
    cfg = get_config()
    day = _today()
    row = conn.execute("SELECT COALESCE(SUM(cost_rub),0) c, COALESCE(SUM(tokens_in+tokens_out),0) t FROM llm_calls WHERE day=? AND cache_hit=0 AND status='ok'",
                       (day,)).fetchone()
    limit = cfg.get("budget.daily_limit_rub", 500)
    tlimit = cfg.get("budget.daily_token_limit")
    ratio = max(row["c"] / limit if limit else 0.0, (row["t"] / tlimit) if tlimit else 0.0)
    return {"day": day, "cost_rub": round(row["c"], 4), "tokens": row["t"], "limit_rub": limit, "token_limit": tlimit,
            "ratio": round(ratio, 4), "soft": ratio >= cfg.get("budget.soft_ratio", 0.8), "hard": ratio >= cfg.get("budget.hard_ratio", 1.0)}


def image_message(prompt: str, png_bytes: bytes) -> list[dict]:
    """OpenAI-compatible multimodal content."""
    b64 = base64.b64encode(png_bytes).decode()
    return [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]


class LLMGateway:
    def __init__(self, conn: sqlite3.Connection, transport: httpx.BaseTransport | None = None, base_url: str | None = None,
                 api_key: str | None = None, sleep=time.sleep):
        self._ledger_context = llm_ledger(conn)
        self.conn = self._ledger_context.__enter__()
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self._sleep = sleep
        self._http = httpx.Client(timeout=120, transport=transport)

    def close(self) -> None:
        try:
            self._http.close()
        finally:
            self._ledger_context.__exit__(None, None, None)

    # ---------- budget ----------
    def usage_today(self) -> dict:
        return usage_today(self.conn)

    def _day(self) -> str:
        return _today()

    # ---------- main call ----------
    def chat(self, *, operation: str, role: str, messages: list[dict], trace_id: str | None = None, max_tokens: int = 2000,
             temperature: float = 0.0, response_format: dict | None = None, use_cache: bool = True, request_options: dict | None = None) -> LLMResult:
        models = get_models()
        if role not in models:                                   # whitelist: only models from Appendix 3 are routable
            raise ValueError(f"role '{role}' is not in config/models.yaml")
        m = models[role]
        payload = {"model": m["api_name"], "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if response_format:
            payload["response_format"] = response_format
        # PE_REQUEST_OPTIONS_V1: explicit controls, included before cache hashing.
        if request_options:
            if set(request_options) - {"reasoning_effort", "reasoning", "thinking"}:
                raise ValueError("unsupported request option")
            payload.update(request_options)
        req_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        if use_cache:
            hit = self.conn.execute("SELECT response FROM llm_cache WHERE request_hash=?", (req_hash,)).fetchone()
            if hit:
                self._log(trace_id, operation, role, m["model"], 0, 0, 0.0, True, "ok", None, req_hash)
                return LLMResult(hit["response"], m["model"], 0, 0, 0.0, True)

        usage = self.usage_today()
        if usage["hard"]:
            self._log(trace_id, operation, role, m["model"], 0, 0, 0.0, False, "blocked", "budget hard limit", req_hash)
            raise BudgetExceeded(f"daily budget exhausted: {usage}")
        if not self.base_url:
            raise LLMError("LLM_BASE_URL is not configured")

        text, tin, tout = self._post(payload, trace_id, operation, role, m, req_hash)
        cost = tin / 1000 * m.get("p_in_rub_per_1k", 0.0) + tout / 1000 * m.get("p_out_rub_per_1k", 0.0)
        if text and getattr(self, "_pe_finish_reason", None) != "length":
            self.conn.execute("INSERT OR REPLACE INTO llm_cache(request_hash,response,model,created_at) VALUES(?,?,?,?)",
                              (req_hash, text, m["model"], iso(now_utc())))
        self._log(trace_id, operation, role, m["model"], tin, tout, cost, False, "ok", None, req_hash)
        return LLMResult(text, m["model"], tin, tout, cost, False, usage["soft"])

    def _post(self, payload, trace_id, operation, role, m, req_hash) -> tuple[str, int, int]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = "unknown"
        for attempt in range(1, 4):
            try:
                r = self._http.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"] or ""  # PE_RESPONSE_V2: empty reasoning-only response is not SQL NULL
                    if isinstance(content, list):
                        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                    usage = data.get("usage") or {}
                    tin = int(usage.get("prompt_tokens") or len(json.dumps(payload["messages"])) // 4)
                    tout = int(usage.get("completion_tokens") or len(content) // 4)
                    self._pe_finish_reason = data["choices"][0].get("finish_reason")
                    return content, tin, tout
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < 3:
                self._sleep(2 ** attempt)
        self._log(trace_id, operation, role, m["model"], 0, 0, 0.0, False, "error", last, req_hash)
        raise LLMError(last)

    def _log(self, trace_id, operation, role, model, tin, tout, cost, cache_hit, status, error, req_hash) -> None:
        self.conn.execute(
            """INSERT INTO llm_calls(trace_id,ts,day,operation,role,model,tokens_in,tokens_out,cost_rub,cache_hit,status,error,request_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trace_id, iso(now_utc()), self._day(), operation, role, model, tin, tout, cost, int(cache_hit), status, error, req_hash))
