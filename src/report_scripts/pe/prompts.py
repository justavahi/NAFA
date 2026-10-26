"""Build versioned requests for the existing Backend LLMGateway."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

from .contracts import INPUT_SCHEMAS, OUTPUT_SCHEMAS

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def prepare_request(prompt_id: str, payload: dict, *, validate_input: bool = False, manifest: dict | None = None) -> dict:
    manifest = manifest if manifest is not None else json.loads((PROMPT_DIR / "manifest.json").read_text(encoding="utf-8"))
    spec = manifest["prompts"][prompt_id]
    if validate_input:
        payload = INPUT_SCHEMAS[spec["input_schema"]].model_validate(payload).model_dump(mode="json")
    system = "\n\n".join((PROMPT_DIR / name).read_text(encoding="utf-8")
                            for name in (manifest["common_system"], spec["file"]))
    schema = OUTPUT_SCHEMAS[spec["schema"]].model_json_schema()
    # Source strings stay in the user message, never interpolated into system text.
    # JSON encoding does not itself prevent prompt injection; the critic and attack
    # evaluation remain required. No tools are exposed to these generation calls.
    system += "\n\nOUTPUT_JSON_SCHEMA:\n" + json.dumps(schema, ensure_ascii=False)
    data = html.escape(json.dumps(payload, ensure_ascii=False), quote=False)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": 'DATA_JSON:\n<untrusted_source id="request_data">\n' + data + '\n</untrusted_source>'},
    ]
    return dict(
        prompt_id=prompt_id, prompt_version=spec["version"],
        prompt_hash=hashlib.sha256(system.encode()).hexdigest(),
        input_hash=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        role=spec["role"], model=spec["model"], schema=spec["schema"],
        messages=messages, max_tokens=spec["max_tokens"], temperature=0.0,
        response_format={"type": "json_object"},
    )
