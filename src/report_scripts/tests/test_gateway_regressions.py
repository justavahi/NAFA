"""Regression for the observed reasoning-only HTTP 200 -> SQL NULL failure."""
import importlib
from pathlib import Path
import sys

import pytest


def test_empty_output_is_accounted_but_never_cached(tmp_path):
    httpx=pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    root=Path(__file__).resolve().parent.parent/".local/backend/store"
    if not root.exists():pytest.skip("prepare_backend.py needed for optional gateway integration test")
    from pe.gateway_patch import patch_gateway
    patch_gateway(root/"app/llm_gateway.py")
    sys.path.insert(0,str(root))
    module=importlib.import_module("app.llm_gateway")
    db=importlib.import_module("app.db")
    conn=db.init_db(db.connect(tmp_path/"test.db"))
    requests=[]
    def handle(request):
        import json
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content=None if len(requests)==1 else '{"ok":true}'),
            finish_reason="length" if len(requests)==1 else "stop")],usage=dict(prompt_tokens=10,completion_tokens=32)))
    gateway=module.LLMGateway(conn,transport=httpx.MockTransport(handle),base_url="https://model.invalid/v1",api_key="unit-test")
    kwargs=dict(operation="test",role="reasoning",messages=[dict(role="user",content="test")],
                request_options={"reasoning_effort":"low"})
    try:
        first=gateway.chat(**kwargs)
        assert first.text==""
        assert conn.execute('SELECT COUNT(*) FROM llm_cache').fetchone()[0]==0
        assert conn.execute('SELECT tokens_out FROM llm_calls').fetchone()[0]==32
        second=gateway.chat(**kwargs)
        assert second.text=='{"ok":true}'
        assert len(requests)==2
        assert requests[0]["reasoning_effort"]=="low"
        assert conn.execute('SELECT COUNT(*) FROM llm_cache').fetchone()[0]==1
    finally:
        gateway.close();conn.close()
