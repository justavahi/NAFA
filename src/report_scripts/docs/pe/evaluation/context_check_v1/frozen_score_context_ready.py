"""Score an already registered, completed card using the same shared allowance.

Lets judging overlap generation without waiting for all eight development cards.
Existing score files are reused by the main runner; no extra job is introduced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pe.improvement import Allowance, AXES, digest, fingerprint
from pe.pipeline import CaseInput
from pe.session import PromptSession


def score(out, role, variant, judge):
    protocol = json.loads((out / "protocol.json").read_text())
    payload = json.loads((out / "case.json").read_text())
    if ([role, variant, judge] not in protocol["score_jobs"] or protocol["code_hash"] != fingerprint()
        or protocol["case_hash"] != digest(payload)
        or protocol["runner_hash"] != hashlib.sha256((ROOT / "scripts/check_context_candidates.py").read_bytes()).hexdigest()):
        raise ValueError("not a frozen registered job")
    path = out / "scores" / (role+"_"+variant+"_"+judge+".json")
    if path.exists():
        print("Already scored; no API call")
        return
    card_result = json.loads((out / "cards" / (role+"_"+variant+".json")).read_text())
    if card_result["status"] != "evaluated":
        raise ValueError("generation did not pass; do not score")
    for line in (ROOT / ".env").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"LLM_API_KEY", "LLM_BASE_URL"}:
            os.environ[key] = value.strip().strip("\"'")
    gateway = Allowance(out, protocol["limits"])
    session = PromptSession(gateway, "context_score", max_calls=2,
        manifest=protocol["rubric"], overrides={"PRM-16": judge})
    try:
        output = session.call("PRM-16", dict(card=card_result["card"], ledger=payload["ledger"]))
        result = dict(status="scored", axes=output.model_dump(), mean=sum(getattr(output, k) for k in AXES)/5)
    except Exception as exc:
        result = dict(status="error", mean=None, error=str(exc))
    finally:
        result["calls"] = session.records
        result["protocol_hash"] = digest(protocol)
        result["card_hash"] = digest(card_result["card"])
        result["scorer_hash"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result["scheduling"] = "registered judge job on a ready card, overlapping generation; same allowance and unchanged rubric"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
        gateway.close()
    print(role, variant, judge, result["status"], result.get("mean"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--judge", required=True)
    args = parser.parse_args()
    score(args.out, args.role, args.variant, args.judge)
