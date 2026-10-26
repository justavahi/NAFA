"""Prepare requests, replay fixtures or run via the team's existing LLMGateway."""
from __future__ import annotations

import argparse
import importlib
import ipaddress
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from .pipeline import CaseInput, run_case
from .prompts import prepare_request, PROMPT_DIR
from .gateway_patch import patch_gateway


class ReplayGateway:
    mode = "replay"

    def __init__(self, directory: Path):
        self.directory = directory

    def chat(self, *, operation, **kwargs):
        return SimpleNamespace(
            text=(self.directory / f"{operation}.json").read_text(encoding="utf-8"),
            model=None, tokens_in=None, tokens_out=None, cost_rub=None, cache_hit=None)


def check_gateway_settings(base_url: str, api_key: str, models: dict, *, local: bool, server_budget: bool = False) -> None:
    """Allow zero token tariffs/keyless serving only in explicit loopback mode."""
    if not base_url:
        raise ValueError("LLM_BASE_URL must be configured")
    endpoint = urlsplit(base_url)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
        raise ValueError("LLM_BASE_URL must be an HTTP(S) URL")
    if local:
        host = endpoint.hostname.lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise ValueError("--local requires a loopback LLM endpoint")
    elif not api_key:
        raise ValueError("LLM_API_KEY must be configured for the organiser gateway")
    if server_budget:
        settings = json.loads((PROMPT_DIR.parent / "config" / "organiser_models.json").read_text())
        if local or base_url.rstrip("/") not in settings["base_urls"]:
            raise ValueError("--organiser-budget requires a documented organiser endpoint")
    manifest = json.loads((PROMPT_DIR / "manifest.json").read_text(encoding="utf-8"))
    for spec in manifest["prompts"].values():
        route = models.get(spec["role"], {})
        if route.get("model") != spec["model"]:
            raise ValueError(f"model route differs for {spec['role']}")
        if not route.get("api_name"):
            raise ValueError(f"missing API model ID for {spec['role']}")
        for key in ("p_in_rub_per_1k", "p_out_rub_per_1k"):
            value = route.get(key)
            if type(value) not in (int, float) or not 0 <= value < float("inf") or (not local and not server_budget and value == 0):
                raise ValueError("configure confirmed token prices or use --local/--organiser-budget as applicable")


class BackendAdapter:
    mode = "live"

    def __init__(self, root: Path, database: Path, *, local: bool = False, server_budget: bool = False):
        root = root.resolve()
        if not (root / "app" / "llm_gateway.py").is_file():
            raise ValueError("--backend-root must point to store/ with app/llm_gateway.py")
        if root == PROMPT_DIR.parent / ".local" / "backend" / "store":
            patch_gateway(root / "app/llm_gateway.py")
        sys.path.insert(0, str(root))
        config = importlib.import_module("app.config")
        if root not in Path(config.__file__).resolve().parents:
            raise ValueError("another package named app is already imported")
        models = config.get_models()
        base_url, api_key = os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", "")
        check_gateway_settings(base_url, api_key, models, local=local, server_budget=server_budget)
        self.cost_unknown = server_budget and any(
            route.get("p_in_rub_per_1k") == 0 or route.get("p_out_rub_per_1k") == 0
            for route in models.values())
        db = importlib.import_module("app.db")
        gateway = importlib.import_module("app.llm_gateway")
        # Reuse the backend's shared DB to share the budget and response cache.
        if not database.is_file():
            raise ValueError("--db must identify the existing shared backend database")
        self.conn = db.init_db(db.connect(database))
        self.gateway = gateway.LLMGateway(self.conn, base_url=base_url, api_key=api_key or "local")
        self.expected_errors = (gateway.LLMError, gateway.BudgetExceeded)

    def chat(self, **kwargs):
        try:
            response = self.gateway.chat(**kwargs)
            if self.cost_unknown:
                response.cost_rub = None
            return response
        except self.expected_errors as exc:
            raise RuntimeError(f"Backend gateway: {exc}") from exc

    def close(self):
        self.gateway.close()
        self.conn.close()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_preview(path: Path, result: dict) -> None:
    card = result["card"]
    text = ["# Карточка NAFA", "", f"Режим: **{result['mode']}**. Статус: **{result['status']}**.", ""]
    if result["mode"] == "replay":
        text += ["Синтетический учебный пример. Ответы записаны вручную; модели не вызывались.", ""]
    sections = [
        ("Что произошло", card["confirmed_facts"]),
        ("Почему это касается компании", card["why_you"]),
        ("Возможные последствия", card["model_inference"]["impact_chain"]),
        ("Вопросы для проверки", card["model_inference"]["actions_or_questions"]),
        ("Позиция банка", card["bank_view_items"]),
    ]
    for title, claims in sections:
        text += [f"## {title}", ""]
        text += [f"- {claim['text']} (основание: {', '.join(claim['source_ids'])})" for claim in claims] or ["Данных нет."]
        text += [""]
    text += ["## Ограничения", ""] + [f"- {item}" for item in dict.fromkeys(result["unknowns"])]
    text += ["", card["disclaimer"], ""]
    path.write_text("\n".join(text), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("prompt_id")
    prepare.add_argument("payload", type=Path)
    prepare.add_argument("--out", type=Path, required=True)
    for name in ("replay", "live"):
        command = commands.add_parser(name)
        command.add_argument("case", type=Path)
        command.add_argument("--out", type=Path, required=True)
        if name == "replay":
            command.add_argument("--responses", type=Path, required=True)
        else:
            command.add_argument("--backend-root", type=Path, required=True)
            command.add_argument("--db", type=Path, required=True)
            command.add_argument("--local", action="store_true", help="Use a loopback model server without paid API credentials")
            command.add_argument("--organiser-budget", action="store_true", help="Use documented server budget when token prices are unknown")
    args = parser.parse_args()
    if args.command == "prepare":
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
        write_json(args.out, prepare_request(args.prompt_id, payload))
        print(f"Request prepared: {args.out} (no API call)")
        return
    case = CaseInput.model_validate_json(args.case.read_text(encoding="utf-8"))
    gateway = (ReplayGateway(args.responses) if args.command == "replay"
               else BackendAdapter(args.backend_root, args.db, local=args.local, server_budget=args.organiser_budget))
    try:
        result = run_case(case, gateway)
    finally:
        if isinstance(gateway, BackendAdapter):
            gateway.close()
    for prompt_id, requests in result.pop("requests").items():
        for index, request in enumerate(requests):
            write_json(args.out / "requests" / f"{prompt_id}_{index}.json", request)
    write_json(args.out / "result.json", result)
    if result["card"]:
        write_json(args.out / "card.json", result["card"])
        write_preview(args.out / "preview.md", result)
    print(f"{result['status']}: {args.out / 'result.json'}; publishable={result['publishable']}")
    if result["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
