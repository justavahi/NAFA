"""Publish aggregate development results and a blank blind human review pack."""
import csv
import hashlib
import json
from pathlib import Path
import random
import sqlite3

ROOT = Path(__file__).resolve().parent.parent


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def main():
    output = ROOT / "docs/pe/evaluation/context_checks"
    source = ROOT / "out/context_check_v1"
    revision = ROOT / "out/context_check_revision_v1"
    view = ROOT / "out/context_view_check_v1"
    studies, accepted = {}, []
    for name, path in [("initial", source), ("narrow_selection", revision), ("scoped_generation", view)]:
        protocol = json.loads((path / "protocol.json").read_text())
        summary = json.loads((path / "summary.json").read_text())
        save(output / name / "protocol.json", protocol)
        save(output / name / "summary.json", summary)
        paths = sorted((path / "cards").glob("*.json")) if name == "initial" else [path / "card.json"]
        generations = {}
        for card_path in paths:
            result = json.loads(card_path.read_text())
            role = card_path.stem[:6] if name == "initial" else "PER-03"
            generations[card_path.stem] = dict(status=result["status"], partial=result.get("partial"),
                issues=result["issues"], check_history=result["check_history"],
                end_to_end_seconds=result["end_to_end_seconds"],
                calls=[{k: c.get(k) for k in ("prompt_id", "status", "tokens_in", "tokens_out", "cache_hit", "elapsed_ms")} for c in result["calls"]])
            save(output / name / card_path.name, result)
            if result["status"] == "evaluated":
                accepted.append(dict(origin=name+"/"+card_path.stem, role=role, card=result["card"]))
        studies[name] = dict(generations=generations, budget=summary["budget"], scores=summary.get("scores", {}))
    save(output / "source_review.json", json.loads((source / "source_review.json").read_text()))
    save(output / "case.json", json.loads((view / "case.json").read_text()))
    c = sqlite3.connect("file:"+str(ROOT / ".local/experiments/gateway.db")+"?mode=ro", uri=True)
    used = c.execute("select sum(tokens_in+tokens_out) from llm_calls where day='2026-10-26' and cache_hit=0 and status='ok'").fetchone()[0]
    c.close()
    snapshot = dict(independent_events=1, independent_clients=1, roles=3, generations=10,
        accepted_by_code_and_critic=len(accepted), withheld=10-len(accepted), human_annotations=0,
        quality_improvement_established=False, target_4_5_established=False, production_promoted=False,
        latest_candidate="prompts/profiles/role_focus_v2.json", local_tests_passed=1107,
        shared_daily_token_ceiling=3000000, shared_reported_tokens_before=2677553, shared_reported_tokens_after=used,
        cost_rub=None, unreported_timeout_usage=True,
        reservation_note="Local study tokens include cache responses. Two Kimi calls timed out; usage may be unreported. The final judge request was denied by the study's conservative reservation before API submission. No allowance was increased or reset.",
        observed_judge_scores=[dict(study="initial", role="PER-03", variant="control", judge="DeepSeek-V4.1-Flash", mean=3.8)],
        judge_calibration_complete=False, studies=studies)
    save(output / "results.json", snapshot)
    # Randomized identifiers and no variant/origin labels in the public pack.
    random.Random(26102026).shuffle(accepted)
    blinded, key = [], {}
    for i, row in enumerate(accepted, 1):
        bid=f"CARD-{i:03}"
        blinded.append(dict(id=bid, expected_role=row["role"], card=row["card"]))
        key[bid] = row["origin"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "blind_human_pack.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in blinded))
    with (output / "human_scores_blank.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields=["id", "reviewer", "coverage", "mechanisms", "personalization", "actions", "grounding", "role_fit", "critical_error", "notes"]
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({"id": row["id"]} for row in blinded)
    save(ROOT / "out/context_blind_key.json", key)
    print(json.dumps({k:v for k,v in snapshot.items() if k!="studies"},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
