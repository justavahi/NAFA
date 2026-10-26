"""Generate, run and score 1,000 synthetic claim-auditing cases.

LLM mode reuses the Backend gateway; rules mode never calls a model.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import importlib
import json
import os
from pathlib import Path
import random
import re
import sys
import time

from pydantic import BaseModel, ConfigDict, StrictBool

from .audit_cases import make_cases
from .metrics import audit_quality, group_bootstrap_interval
from .prompts import PROMPT_DIR

ROOT = Path(__file__).resolve().parent.parent
MODELS = {
    "z-ai/glm-5.3-flash": "GLM-5.3-Flash",
    "qwen/qwen3.6-35b-a3b": "Qwen3.6 35B A3B",
    "google/gemma-4-26b-a4b-it": "Gemma 4 26B A4B",
    "deepseek/deepseek-v4.1-flash": "DeepSeek-V4.1-Flash",
    "moonshotai/kimi-k2.6": "Kimi-K2.6",
}


def rule_audit(data: dict) -> bool:
    """Independent reference implementation for the five benchmark conditions."""
    statement = data["statement"]
    sources = {s["id"]: s for s in data["sources"]}
    if statement["effect"] != data["computed"]["effect"]:
        return False
    if not statement["source_ids"] or any(ref not in sources for ref in statement["source_ids"]):
        return False
    event_source = sources.get(data["event"]["source_id"])
    if not event_source:
        return False
    source_rate = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", event_source["text"])
    try:
        if not source_rate or Decimal(source_rate[1].replace(",", ".")) != Decimal(statement["rate_percent"].replace(",", ".")):
            return False
    except InvalidOperation:
        return False
    bank = statement["bank_view"]
    if bank is not None:
        source = sources.get(bank["source_id"], {})
        if source.get("kind") != "bank" or any(source.get(k) != bank[k] for k in ("as_of", "page")):
            return False
    if data["company"]["loan"] is None and statement["loan_assertion"] != "unknown":
        return False
    return True


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    accept: StrictBool


class AuditBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[AuditResult]


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(cases: list[dict], predictions: dict[str, dict], metadata: dict) -> dict:
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    report = dict(metadata, cases_total=len(cases),
                  metrics=audit_quality([c["expected_accept"] for c in cases],
                                        [predictions.get(c["id"], {}).get("accept") for c in cases]),
                  by_split={}, by_scenario={}, failures=[])
    attack_cases = [c for c in cases if "Игнорируй правила" in c["input"]["event"]["text"]]
    if attack_cases:
        report["injection_cases"] = audit_quality(
            [c["expected_accept"] for c in attack_cases],
            [predictions.get(c["id"], {}).get("accept") for c in attack_cases])
    for field, target in (("split", "by_split"), ("mutation", "by_scenario")):
        for value in sorted({c[field] for c in cases}):
            subset = [c for c in cases if c[field] == value]
            report[target][value] = audit_quality(
                [c["expected_accept"] for c in subset],
                [predictions.get(c["id"], {}).get("accept") for c in subset])
    for case in cases:
        prediction = predictions.get(case["id"], {}).get("accept")
        if prediction is not case["expected_accept"]:
            report["failures"].append(dict(id=case["id"], group_id=case["group_id"],
                                           mutation=case["mutation"], expected=case["expected_accept"], predicted=prediction))
    return report


def make_gateway(model: str, max_tokens_total: int):
    """Use a local cache DB; the documented organiser server enforces shared RUB cap.

    Token prices are absent from the instruction. SQLite zero prices are legacy
    placeholders; reports always mark RUB cost unknown, never free.
    """
    endpoint = os.getenv("LLM_BASE_URL", "").rstrip("/")
    allowed = {"https://changellenge.up.railway.app/v1", "https://api.alfahack-gateway26.ru/v1"}
    if endpoint not in allowed:
        raise ValueError("Use the organiser endpoint documented for this benchmark")
    if not os.getenv("LLM_API_KEY", "").startswith("sk-"):
        raise ValueError("Valid organiser API credentials are required")
    backend = ROOT / ".local" / "backend" / "store"
    if not (backend / "app" / "llm_gateway.py").exists():
        raise ValueError("Run scripts/prepare_backend.py first")
    sys.path.insert(0, str(backend))
    config = importlib.import_module("app.config")
    if backend not in Path(config.__file__).resolve().parents:
        raise ValueError("Unexpected app.config import")
    config.get_models()["eval"] = dict(model=MODELS[model], api_name=model,
                                       p_in_rub_per_1k=0.0, p_out_rub_per_1k=0.0)
    # This bound is additional to the server's shared 500 RUB/day hard limit.
    config.get_config().raw["budget"]["daily_token_limit"] = max_tokens_total
    db_module = importlib.import_module("app.db")
    db_path = ROOT / ".local" / "experiments" / "gateway.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = db_module.init_db(db_module.connect(db_path))
    gateway_module = importlib.import_module("app.llm_gateway")
    return conn, gateway_module.LLMGateway(conn), (gateway_module.LLMError, gateway_module.BudgetExceeded)


def run(args) -> None:
    cases = load_rows(args.cases)
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    cases = cases[:args.limit]
    if not cases:
        raise ValueError("no selected cases")
    prompt = (PROMPT_DIR / "PRM-AUDIT_v1.txt").read_text(encoding="utf-8")
    signature = hashlib.sha256(json.dumps(dict(cases=cases, prompt=prompt, engine=args.engine,
                                               model=args.model if args.engine == "llm" else None),
                                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text())["signature"] != signature:
        raise ValueError("output directory belongs to another benchmark; use a different --out")
    metadata = dict(signature=signature, seed=42, synthetic=True, engine=args.engine,
                    model=args.model if args.engine == "llm" else None,
                    metric="NAFA Audit Score v1", quality_scope="structured claim auditing only",
                    cost_rub=None if args.engine == "llm" else 0,
                    pricing_status="unknown_server_enforced_500_rub_day" if args.engine == "llm" else "no_model_calls",
                    source="organiser instruction 1ly8GsI_-oPnCQWvtZ4nGevnnDP2MGYIfpn2zOUFxZ_4")
    write_json(manifest_path, metadata)
    pred_path = args.out / "predictions.jsonl"
    predictions = {row["id"]: row for row in load_rows(pred_path)} if pred_path.exists() else {}
    pending = [c for c in cases if c["id"] not in predictions]
    conn = gateway = None
    if args.engine == "llm" and pending:
        conn, gateway, gateway_errors = make_gateway(args.model, args.max_total_tokens)
    started = time.monotonic()
    try:
        for offset in range(0, len(pending), args.batch_size):
            batch = pending[offset:offset + args.batch_size]
            usage, error = {}, None
            if args.engine == "rules":
                outputs = {c["id"]: rule_audit(c["input"]) for c in batch}
            else:
                messages = [dict(role="system", content=prompt),
                            dict(role="user", content=json.dumps(dict(cases=[c["input"] for c in batch]), ensure_ascii=False))]
                try:
                    response = gateway.chat(operation="audit_benchmark_v1", role="eval", messages=messages,
                                            trace_id=signature[:16], max_tokens=args.max_output_tokens,
                                            temperature=0.0, response_format={"type": "json_object"})
                    usage = dict(tokens_in=response.tokens_in, tokens_out=response.tokens_out, cache_hit=response.cache_hit)
                    parsed = AuditBatch.model_validate_json(response.text)
                    outputs = {r.id: r.accept for r in parsed.results}
                    if len(outputs) != len(parsed.results) or set(outputs) != {c["id"] for c in batch}:
                        raise ValueError("model returned missing, duplicate or foreign case IDs")
                except gateway_errors as exc:
                    # Stop on transport/auth/budget errors rather than burning repeated batches.
                    error = type(exc).__name__ + ": " + str(exc)
                    write_json(args.out / "stopped.json", dict(error=error, completed=len(predictions), remaining=len(cases)-len(predictions)))
                    print(f"Stopped: {error}", flush=True)
                    break
                except (ValueError, TypeError) as exc:
                    outputs, error = {}, type(exc).__name__
            with pred_path.open("a", encoding="utf-8") as stream:
                for case in batch:
                    row = dict(id=case["id"], accept=outputs.get(case["id"]), error=error)
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    predictions[case["id"]] = row
            if usage:
                with (args.out / "calls.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(dict(case_ids=[c["id"] for c in batch], **usage)) + "\n")
            # Checkpoint after every batch; restart resumes without repeating completed IDs.
            report = summarize(cases, predictions, metadata)
            write_json(args.out / "report.json", report)
            processed_cases = [c for c in cases if c["id"] in predictions]
            processed_accuracy = sum(predictions[c["id"]]["accept"] is c["expected_accept"]
                                     for c in processed_cases) / len(processed_cases)
            print(f"{args.engine}: {len(predictions)}/{len(cases)}; processed_accuracy={processed_accuracy:.3f}", flush=True)
    finally:
        if gateway is not None:
            gateway.close()
            conn.close()
    report = summarize(cases, predictions, metadata)
    report["group_bootstrap_95pct"] = group_bootstrap_interval(
        [c["expected_accept"] for c in cases], [predictions.get(c["id"], {}).get("accept") for c in cases],
        [c["group_id"] for c in cases])
    report["wall_seconds_this_run"] = round(time.monotonic() - started, 2)
    report["calls"] = load_rows(args.out / "calls.jsonl") if (args.out / "calls.jsonl").exists() else []
    write_json(args.out / "report.json", report)
    lines = ["# NAFA: синтетический аудит утверждений", "",
             f"Исполнитель: {args.engine}. Модель: {metadata['model'] or 'не вызывалась'}.",
             "", f"Случаев: {len(cases)}. Обработано: {len(predictions)}.",
             f"Точность: {report['metrics']['accuracy']:.3f}.",
             f"Сбалансированная точность: {report['metrics']['balanced_accuracy']}.",
             f"NAFA Audit Score v1: {report['metrics']['audit_score_v1']}.",
             f"Пропущено ответов: {report['metrics']['missing']}.", "",
             "Это синтетическая проверка пяти структурированных правил. Она не измеряет",
             "качество финансовых рекомендаций, глубину объяснений или качество полного пайплайна.", "",
             "Стоимость LLM в рублях неизвестна: в инструкции нет тарифов; сервер ограничивает общий бюджет.", ""]
    (args.out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("make")
    generate.add_argument("--out", type=Path, required=True)
    export = commands.add_parser("export-sft")
    export.add_argument("--cases", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--cases", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--engine", choices=["rules", "llm"], default="rules")
    run_parser.add_argument("--model", choices=MODELS, default="z-ai/glm-5.3-flash")
    run_parser.add_argument("--split", choices=["train", "validation", "test", "all"], default="all")
    run_parser.add_argument("--limit", type=int, default=1000)
    run_parser.add_argument("--batch-size", type=int, default=20)
    run_parser.add_argument("--max-output-tokens", type=int, default=1600)
    run_parser.add_argument("--max-total-tokens", type=int, default=600000)
    args = parser.parse_args()
    if args.command == "make":
        cases = make_cases()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
        print(f"Generated {len(cases)} cases; splits: {dict(Counter(c['split'] for c in cases))}")
    elif args.command == "export-sft":
        cases = load_rows(args.cases)
        prompt = (PROMPT_DIR / "PRM-AUDIT_v1.txt").read_text(encoding="utf-8")
        args.out.mkdir(parents=True, exist_ok=True)
        memberships = {}
        for case in cases:
            memberships.setdefault(case["group_id"], set()).add(case["split"])
        if any(len(value) != 1 for value in memberships.values()):
            raise ValueError("group leakage across splits")
        counts = {}
        for split in ("train", "validation"):
            rows = []
            for case in cases:
                if case["split"] != split:
                    continue
                # Hide the original mutation-index suffix, which encodes labels.
                # Fresh opaque IDs carry no stable relation to the target label.
                sample = json.loads(json.dumps(case["input"]))
                sample["id"] = "train_" + hashlib.sha256(case["id"].encode()).hexdigest()[:20]
                answer = dict(results=[dict(id=sample["id"], accept=case["expected_accept"])])
                rows.append(dict(messages=[dict(role="system", content=prompt),
                                           dict(role="user", content=json.dumps(dict(cases=[sample]), ensure_ascii=False)),
                                           dict(role="assistant", content=json.dumps(answer, ensure_ascii=False))]))
            (args.out / f"{split}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            counts[split] = len(rows)
        write_json(args.out / "dataset_card.json", dict(synthetic=True, human_reviewed=False, counts=counts,
                   test_exported=False, task="structured auditing", loss="assistant-token cross entropy for SFT",
                   limitation="not a financial advice training set; no training has been executed"))
        print(f"Exported synthetic SFT data: {counts}; test excluded")
    else:
        if min(args.limit, args.batch_size, args.max_output_tokens, args.max_total_tokens) <= 0:
            parser.error("limits and batch size must be positive")
        run(args)


if __name__ == "__main__":
    main()
