"""Bounded calls, schema repair and auditable routing over Backend LLMGateway."""
from __future__ import annotations

import hashlib
import json
import time

from pydantic import ValidationError

from .contracts import OUTPUT_SCHEMAS
from .prompts import PROMPT_DIR, prepare_request
from .schemas import CriticVerdict

MODEL_ROLES = {
    "Gemma 4 26B A4B": "extract", "GLM-5.3-Flash": "reasoning",
    "Qwen3.6 35B A3B": "general", "DeepSeek-V4.1-Flash": "synthesis",
    "Qwen3-VL-30B-A3B-Instruct": "vlm", "Kimi-K2.6": "judge",
}


def family(model: str) -> str:
    return model.split()[0].split("-")[0].lower()


class PromptSession:
    def __init__(self, gateway, trace_id: str, *, max_calls: int = 8, overrides: dict | None = None, manifest: dict | None = None):
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self.gateway, self.trace_id, self.max_calls = gateway, trace_id, max_calls
        self.overrides = overrides or {}
        if any(model not in MODEL_ROLES for model in self.overrides.values()):
            raise ValueError("model outside the allowed list")
        self.records, self.requests = [], {}
        manifest = manifest if manifest is not None else json.loads((PROMPT_DIR / "manifest.json").read_text())
        self.manifest=json.loads(json.dumps(manifest))
        for generator in ("PRM-05", "PRM-06"):
            writer = self.overrides.get(generator, manifest["prompts"][generator]["model"])
            for checker in ("PRM-07", "PRM-16"):
                critic = self.overrides.get(checker, manifest["prompts"][checker]["model"])
                if family(writer) == family(critic):
                    raise ValueError("generator and critic/judge must use different model families")

    def call(self, prompt_id: str, payload: dict, *, images: list[dict] | None = None, feedback: str | None = None):
        request = prepare_request(prompt_id, payload, validate_input=True,manifest=self.manifest)
        model = self.overrides.get(prompt_id, request["model"])
        request["model"], request["role"] = model, MODEL_ROLES[model]
        self.requests.setdefault(prompt_id, []).append(request)
        repair_errors = ""
        for attempt in range(2):
            if len(self.records) >= self.max_calls:
                raise RuntimeError("global prompt call limit reached")
            messages = list(request["messages"])
            if feedback:
                messages.append(dict(role="user",content="Проверка приложения отклонила предыдущий результат. Исправь весь JSON по исходной схеме. "+feedback))
            if attempt:
                shape_hint=("Каждый EvidenceStep — объект {text,source_ids}. У Mechanism и DecisionAction нет верхнеуровневых text/source_ids: ссылки находятся внутри event/channel/company/consequence/changes_if или check/decision_use. Не сворачивай шаги в строки." if request['schema']=='InterpretationPlan' else "source_ids обязательны также у действий и вопросов.")
                messages.append(dict(role="user", content="Предыдущий ответ не прошёл JSON-схему. Ошибки: " + repair_errors + ". Верни полный корректный JSON без Markdown. "+shape_hint+" Соблюдай исходные правила."))
            if images:
                if prompt_id != "PRM-03":
                    raise ValueError("image input is restricted to PRM-03")
                messages[-1] = dict(role="user", content=[dict(type="text", text=messages[-1]["content"]), *images])
            start = time.monotonic()
            record = dict(prompt_id=prompt_id, version=request["prompt_version"], attempt=attempt,
                          prompt_hash=request["prompt_hash"], requested_model=model, status="started",
                          input_hash=request['input_hash'],
                          max_tokens=min(request["max_tokens"]*(attempt+1),8192))
            self.records.append(record)
            try:
                response = self.gateway.chat(operation=prompt_id, role=request["role"], messages=messages,
                                             trace_id=self.trace_id, max_tokens=record["max_tokens"],
                                             temperature=0.0, response_format=request["response_format"],
                                             request_options=({"reasoning_effort": "low"} if model == "GLM-5.3-Flash"
                                                              else {"reasoning":{"effort":"low"}} if model in {"Qwen3.6 35B A3B","DeepSeek-V4.1-Flash","Kimi-K2.6"} else None))
                record.update(executed_model=response.model, tokens_in=response.tokens_in,
                              tokens_out=response.tokens_out, cost_rub=response.cost_rub,
                              cache_hit=response.cache_hit, response_hash=hashlib.sha256((response.text or "").encode()).hexdigest())
                if self.gateway.mode != "replay" and response.model != model:
                    raise RuntimeError("gateway route differs from requested model")
                if prompt_id=="PRM-03":
                    from .vlm import normalize_page
                    normalized,changes=normalize_page(response.text)
                    record["page_normalization"]=changes
                    output=OUTPUT_SCHEMAS[request["schema"]].model_validate(normalized)
                else:
                    output = OUTPUT_SCHEMAS[request["schema"]].model_validate_json(response.text)
                if isinstance(output,CriticVerdict):
                    card=payload["card"]
                    claims=card["confirmed_facts"]+card["bank_view_items"]+card.get("why_you",[])
                    inference=card["model_inference"]
                    for field in ("impact_chain","risks","opportunities","actions_or_questions"):
                        claims+=inference[field]
                    by_id={claim["id"]:claim["text"] for claim in claims}
                    record["bound_claim_hashes"]={cid:hashlib.sha256(text.encode()).hexdigest() for cid,text in by_id.items()}
                    for claim in output.claims:
                        if claim.id in by_id and claim.text=="":
                            claim.text=by_id[claim.id]
                record["status"] = "ok"
                return output
            except (ValidationError, TypeError, json.JSONDecodeError) as exc:
                record["status"] = "invalid_schema"
                repair_errors = ("; ".join(".".join(map(str,e["loc"]))+": "+e["msg"] for e in exc.errors())
                                 if isinstance(exc,ValidationError) else "invalid response type")
                if attempt:
                    raise
            except Exception:
                record["status"] = "error"
                raise
            finally:
                record["elapsed_ms"] = round((time.monotonic() - start) * 1000)
