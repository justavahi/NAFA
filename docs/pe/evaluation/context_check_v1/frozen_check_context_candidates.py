"""Frozen, bounded development pilot; one publication/client, three roles.

No test split is accessed. Assistant PDF review is not human annotation.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pe.context import attach_news_fragments, fragment_news
from pe.improvement import Allowance, AXES, digest, fingerprint
from pe.pipeline import CaseInput, run_case
from pe.session import PromptSession


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def prepare(out):
    if out.exists():
        raise ValueError("new study directory required")
    source_pdf = ROOT / ".local/vlm_pages/DOC-07.pdf"
    original = json.loads((ROOT / "out/real_profile_smoke_v1/cases.json").read_text())["PARTICIPANT_01"]["case"]
    # Visually checked against original DOC-07 p1. Whitespace normalised from
    # the raster page. Do not change archived corpus or old experiment results.
    budget = "Хотя в августе федеральный бюджет сведен с профицитом 0,7 трлн руб., накопленный дефицит бюджета остается 5,8 трлн руб., что существенно выше прогнозов."
    forecast = "Мы ожидаем сохранения паузы на заседании 23 октября: С учетом риторики ЦБ РФ мы меняем наш взгляд на динамику ключевой ставки на этот год. Если раньше мы ориентировались на ставку на уровне 13,5% на конец 2026 г., то теперь мы ожидаем ее сохранения на текущем значении 14,0% до конца года. При этом наш прогноз по ставке на конец 2027 г. остается на уровне 12,0%."
    news = budget + "\n" + forecast
    original["ledger"]["trace_id"] = "context_pdf_review"
    original["ledger"]["documents"] = [dict(id="PUBLICATION", kind="news", text=news,
        as_of="2026-09-11", is_mock=False), dict(id="BANK_ORIGINAL", kind="bank", text=forecast,
        as_of="2026-09-11", page=1, is_mock=False)]
    for entry in original["ledger"]["entries"]:
        if entry["id"] == "N1":
            entry["source"].update(start=0, end=len(news), quote=news)
        elif entry["kind"] == "BANK_VIEW":
            quote = forecast.split(" Если раньше")[1].split(" При этом")[0] if entry["id"] == "B1" else forecast.split(" При этом")[1]
            quote = ("Если раньше" + quote) if entry["id"] == "B1" else ("При этом" + quote)
            start = forecast.index(quote)
            entry["source"] = dict(document_id="BANK_ORIGINAL", start=start, end=start+len(quote), quote=quote)
        elif entry["id"] == "C_LIMITS":
            entry["value"] += " Дата клиентских агрегатов неизвестна: не является историческим бэктестом."
    case = CaseInput.model_validate(original)
    # Give all variants the same atomic + original ledger. Only focus variants
    # receive the selection. Role-only comparisons use exactly the same v7 text.
    case = CaseInput.model_validate({**case.model_dump(mode="json"),
        "ledger": attach_news_fragments(case.ledger).model_dump(mode="json")})
    fragments = fragment_news(case.ledger.documents[0])
    selected = [f.id for f in fragments if f.source.start > len(budget)]
    profiles = {k: json.loads((ROOT / f"prompts/profiles/{v}_v1.json").read_text()) for k, v in
        [("control", "context_control"), ("role", "role_context"), ("focus", "fragment_focus"), ("both", "role_focus")]}
    rubric = json.loads((ROOT / "prompts/profiles/baseline_v1.json").read_text())
    rubric["prompts"]["PRM-16"].update(file="PRM-16_v2.txt", version="2")
    jobs = [("PER-03", v) for v in ("control", "role", "focus", "both")]
    jobs += [(role, v) for role in ("PER-01", "PER-04") for v in ("control", "role")]
    # Paired CFO judgement from both families first, then remaining DeepSeek.
    scores = [("PER-03", v, j) for j in ("DeepSeek-V4.1-Flash", "Kimi-K2.6") for v in ("control", "both")]
    scores += [(role, v, "DeepSeek-V4.1-Flash") for role, v in jobs if (role, v) not in [("PER-03", "control"), ("PER-03", "both")]]
    protocol = dict(code_hash=fingerprint(), runner_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        case_hash=digest(case.model_dump(mode="json")), profiles=profiles, rubric=rubric,
        role_reference_sha256=hashlib.sha256((ROOT / "reference/persona_weights.csv").read_bytes()).hexdigest(),
        source_pdf_sha256=hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
        generation_jobs=jobs, score_jobs=scores, selected_news_ids=selected,
        limits=dict(gateway_calls=48, additional_tokens=250000, shared_daily_tokens=3000000, call_wall_seconds=180),
        independent_events=1, independent_clients=1, roles=3, development_only=True,
        production_promoted=False, human_reviews=0, assistant_visual_pdf_review=True,
        interpretation="Attributed bank publication/forecast; no external verification or historical client backtest. Same corrected source for all arms; do not compare with legacy scores as a source-only effect.")
    save(out / "case.json", case.model_dump(mode="json"))
    save(out / "protocol.json", protocol)
    save(out / "source_review.json", dict(assistant_visual_review=True, human_review=False,
        source_pdf_sha256=protocol["source_pdf_sha256"], page=1, bank_authored=True,
        source_transcription=[budget, forecast], selected_fragment_ids=selected,
        issues_in_old_extraction=["6.0 to 6.1 described as decrease, original says increase", "Budget and forecast combined into one block; these are separate paragraphs in original"],
        original_corpus_modified=False, truth_independently_verified=False))
    conn = sqlite3.connect(out / "budget.sqlite")
    conn.execute("create table allowance (id integer primary key,calls integer,tokens integer,reserved integer,stopped text)")
    conn.execute("insert into allowance values(1,0,0,0,null)")
    conn.commit(); conn.close()
    print("Frozen 8 generation jobs, 10 judge jobs; 1 event/client, 3 roles. No holdout accessed.")


def run(out):
    protocol = json.loads((out / "protocol.json").read_text())
    payload = json.loads((out / "case.json").read_text())
    if (protocol["code_hash"] != fingerprint() or protocol["case_hash"] != digest(payload)
        or protocol["runner_hash"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        or protocol["role_reference_sha256"] != hashlib.sha256((ROOT / "reference/persona_weights.csv").read_bytes()).hexdigest()):
        raise ValueError("frozen study changed")
    # Load credentials without shell interpolation or printing their values.
    for line in (ROOT / ".env").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"LLM_API_KEY", "LLM_BASE_URL"}:
            os.environ[key] = value.strip().strip("\"'")
    gateway = Allowance(out, protocol["limits"])
    summary = dict(independent_events=1, independent_clients=1, roles=3,
                   production_promoted=False, human_reviews=0, generations={}, scores={})
    try:
        for role, variant in protocol["generation_jobs"]:
            name = role+"_"+variant
            path = out / "cards" / (name+".json")
            if path.exists():
                result = json.loads(path.read_text())
            else:
                case = CaseInput.model_validate({**payload, "persona": role})
                selected = protocol["selected_news_ids"] if variant in {"focus", "both"} else None
                start = time.monotonic()
                result = run_case(case, gateway, manifest=protocol["profiles"][variant], selected_news_ids=selected)
                result["end_to_end_seconds"] = time.monotonic()-start
                result["protocol_hash"] = digest(protocol)
                result.pop("requests", None)
                save(path, result)
            summary["generations"][name] = {k: result.get(k) for k in ("status", "partial", "issues", "repairs", "end_to_end_seconds")}
            print("generation", name, result["status"], flush=True)
            if gateway.base.blocked:
                return
        for role, variant, judge in protocol["score_jobs"]:
            name = role+"_"+variant
            card_result = json.loads((out / "cards" / (name+".json")).read_text())
            score_name = name+"_"+judge
            path = out / "scores" / (score_name+".json")
            if path.exists():
                result = json.loads(path.read_text())
            elif card_result["status"] != "evaluated":
                result = dict(status="generation_failed", mean=None)
                save(path, result)
            else:
                # Judges receive no variant name, role card, target score or prior score.
                case = CaseInput.model_validate({**payload, "persona": role})
                session = PromptSession(gateway, "context_score", max_calls=2,
                    manifest=protocol["rubric"], overrides={"PRM-16": judge})
                try:
                    output = session.call("PRM-16", dict(card=card_result["card"], ledger=case.ledger.model_dump(mode="json")))
                    result = dict(status="scored", axes=output.model_dump(), mean=sum(getattr(output, k) for k in AXES)/5)
                except Exception as exc:
                    result = dict(status="error", mean=None, error=str(exc))
                result["calls"] = session.records
                save(path, result)
            summary["scores"][score_name] = result
            print("judge", name, judge, result["status"], result.get("mean"), flush=True)
            if gateway.base.blocked:
                return
    finally:
        summary["budget"] = dict(zip(("id", "calls", "tokens", "reserved", "stopped"), gateway.conn.execute("select * from allowance").fetchone()))
        save(out / "summary.json", summary)
        gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.out) if args.action == "prepare" else run(args.out)
