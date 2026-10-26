import json

import pytest

from pe.prompts import PROMPT_DIR
from pe.run import check_gateway_settings


def configured_models(price=0):
    prompts = json.loads((PROMPT_DIR / "manifest.json").read_text())["prompts"]
    return {spec["role"]: dict(model=spec["model"], api_name=spec["model"],
                              p_in_rub_per_1k=price, p_out_rub_per_1k=price)
            for spec in prompts.values()}


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_serving_allows_keyless_zero_tariffs(host):
    check_gateway_settings(f"http://{host}:11434/v1", "", configured_models(), local=True)


def test_paid_serving_requires_prices_and_credentials():
    with pytest.raises(ValueError, match="token prices"):
        check_gateway_settings("https://models.example/v1", "test", configured_models(), local=False)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        check_gateway_settings("https://models.example/v1", "", configured_models(1), local=False)


def test_remote_endpoint_cannot_use_local_billing_mode():
    with pytest.raises(ValueError, match="loopback"):
        check_gateway_settings("https://models.example/v1", "", configured_models(), local=True)


def test_unapproved_model_cannot_replace_local_route():
    models = configured_models()
    models["reasoning"]["model"] = "qwen2.5-coder:7b"
    with pytest.raises(ValueError, match="model route"):
        check_gateway_settings("http://localhost:11434/v1", "", models, local=True)


def test_documented_server_budget_allows_unknown_prices_without_calling_them_free():
    check_gateway_settings("https://changellenge.up.railway.app/v1", "test", configured_models(),
                           local=False, server_budget=True)


def test_server_budget_override_is_not_accepted_for_arbitrary_host():
    with pytest.raises(ValueError, match="documented organiser endpoint"):
        check_gateway_settings("https://models.example/v1", "test", configured_models(),
                               local=False, server_budget=True)
