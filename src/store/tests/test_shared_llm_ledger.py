"""Moving a UI runtime must not reset the existing model usage/cache ledger."""
import pytest

from app.config import get_config
from app.db import connect, init_db
from app.llm_gateway import BudgetExceeded, usage_today
from app.quality import q25_tokens
from tests.test_gateway_corpus import gw


def test_new_runtime_preserves_shared_usage_cache_and_hard_limit(conn, tmp_path, monkeypatch):
    path = tmp_path / "shared.db"
    shared = init_db(connect(path))
    get_config().raw["budget"]["daily_token_limit"] = 1500
    messages = [{"role":"user","content":"already paid"}]
    original = gw(shared)
    original.chat(operation="old", role="general", messages=messages)
    original.close()
    monkeypatch.setenv("NAFA_LLM_LEDGER_PATH", str(path))
    calls = {"n":0}
    gateway = gw(conn, counter=calls)
    try:
        assert usage_today(conn)["tokens"] == 1500
        assert gateway.chat(operation="new", role="general", messages=messages).cache_hit
        with pytest.raises(BudgetExceeded):
            gateway.chat(operation="new", role="general", messages=[{"role":"user","content":"new spend"}])
        assert calls["n"] == 0
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
        assert q25_tokens(conn)["today"]["tokens"] == 1500
        assert q25_tokens(conn)["blocked_calls"] == 1
        assert shared.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 3
    finally:
        gateway.close()
        shared.close()
    assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_shared_budget_path_cannot_silently_create_a_fresh_ledger(conn, tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setenv("NAFA_LLM_LEDGER_PATH", str(path))
    get_config().raw["budget"]["daily_token_limit"] = 1500
    with pytest.raises(ValueError, match="already exist"):
        gw(conn)
    assert not path.exists()


def test_shared_budget_requires_explicit_token_ceiling(conn, tmp_path, monkeypatch):
    path = tmp_path / "shared.db"
    shared = init_db(connect(path)); shared.close()
    monkeypatch.setenv("NAFA_LLM_LEDGER_PATH", str(path))
    get_config().raw["budget"]["daily_token_limit"] = None
    with pytest.raises(ValueError, match="positive daily token limit"):
        gw(conn)
