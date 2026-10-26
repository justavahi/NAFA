"""One new development case: narrow selection to the actual 2026 revision.

Keeps v1 failures and all prompts unchanged; no holdout or quota reset.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pe.improvement import Allowance, AXES, digest, fingerprint
from pe.pipeline import CaseInput, run_case
from pe.session import PromptSession


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def prepare(out, prior):
    if out.exists():
        raise ValueError("new experiment directory required")
    old = json.loads((prior / "protocol.json").read_text())
    payload = json.loads((prior / "case.json").read_text())
    selected = [e["id"] for e in payload["ledger"]["entries"] if e["kind"] == "NEWS_FACT"
                and e["id"].startswith("FR_") and "Если раньше мы ориентировались" in e["source"]["quote"]]
    if len(selected) != 1:
        raise ValueError("one exact revision span required")
    protocol = dict(code_hash=fingerprint(), runner_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        case_hash=digest(payload), prior_protocol_hash=digest(old), prior_directory=str(prior.relative_to(ROOT)),
        profile=old["profiles"]["both"], rubric=old["rubric"], selected_news_ids=selected,
        limits=dict(gateway_calls=12, additional_tokens=60000, shared_daily_tokens=3000000, call_wall_seconds=180),
        independent_events=1, independent_clients=1, roles=1, development_only=True, production_promoted=False,
        changes="Only source selection: 2026 upward forecast revision. Unchanged 2027 forecast is retained as BANK_VIEW but not selected as event fact. Same source/profile/critic/rubric.",
        score_jobs=["DeepSeek-V4.1-Flash", "Kimi-K2.6"], human_reviews=0)
    save(out / "case.json", payload)
    save(out / "protocol.json", protocol)
    conn = sqlite3.connect(out / "budget.sqlite")
    conn.execute("create table allowance (id integer primary key,calls integer,tokens integer,reserved integer,stopped text)")
    conn.execute("insert into allowance values(1,0,0,0,null)"); conn.commit(); conn.close()
    print("Frozen one revised-selection generation and two independent judge jobs. Original study unchanged.")


def run(out):
    protocol = json.loads((out / "protocol.json").read_text())
    payload = json.loads((out / "case.json").read_text())
    prior = ROOT / protocol["prior_directory"]
    if (protocol["case_hash"] != digest(payload) or protocol["code_hash"] != fingerprint()
        or protocol["runner_hash"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        or protocol["prior_protocol_hash"] != digest(json.loads((prior / "protocol.json").read_text()))):
        raise ValueError("frozen revision changed")
    for line in (ROOT / ".env").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"LLM_BASE_URL", "LLM_API_KEY"}:
            os.environ[key] = value.strip().strip("\"'")
    gateway = Allowance(out, protocol["limits"])
    summary = dict(independent_events=1, independent_clients=1, production_promoted=False, scores={})
    try:
        path = out / "card.json"
        if path.exists():
            result = json.loads(path.read_text())
        else:
            case = CaseInput.model_validate(payload)
            start = time.monotonic()
            result = run_case(case, gateway, manifest=protocol["profile"], selected_news_ids=protocol["selected_news_ids"])
            result["end_to_end_seconds"] = time.monotonic()-start
            result.pop("requests", None)
            save(path, result)
        summary["generation"] = {k: result.get(k) for k in ("status", "partial", "issues", "check_history", "end_to_end_seconds")}
        print("revised generation", result["status"], result["issues"], flush=True)
        if result["status"] != "evaluated" or gateway.base.blocked:
            return
        for judge in protocol["score_jobs"]:
            path = out / (judge+".json")
            if path.exists():
                score = json.loads(path.read_text())
            else:
                session = PromptSession(gateway, "context_revision_score", max_calls=2, manifest=protocol["rubric"], overrides={"PRM-16": judge})
                try:
                    output = session.call("PRM-16", dict(card=result["card"], ledger=payload["ledger"]))
                    score = dict(status="scored", mean=sum(getattr(output, k) for k in AXES)/5, axes=output.model_dump())
                except Exception as exc:
                    score = dict(status="error", mean=None, error=str(exc))
                score["calls"] = session.records
                save(path, score)
            summary["scores"][judge] = score
            print("revised judge", judge, score["status"], score.get("mean"), flush=True)
            if gateway.base.blocked:
                return
    finally:
        summary["budget"] = dict(zip(("id", "calls", "tokens", "reserved", "stopped"), gateway.conn.execute("select * from allowance").fetchone()))
        save(out / "summary.json", summary)
        gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prior", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.out, args.prior.resolve())
    else:
        run(args.out)
