"""Reproducible acceptance runs for PE prompts and card generation.

Uses approved models through the existing Backend gateway. All seed clients
are synthetic; expert acceptance is recorded separately, never inferred here.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import threading

from .contracts import OUTPUT_SCHEMAS
from .pipeline import run_case
from .prompts import PROMPT_DIR
from .seed_cases import golden_cases, task_payloads
from .session import PromptSession
from .tasks import run_task
from .gateway_patch import patch_gateway
from .adversarial import ATTACKS, MARKER, attack_payload, critic_defects

ROOT = Path(__file__).resolve().parent.parent
BACKEND_REV = "1e794c8b037b63c4fcbd5c34caeaaf72c8f33f04"
CARD_CASES = ["GS-01","GS-03","GS-04","GS-06","GS-09","GS-12","GS-13","GS-15","GS-16","GS-07","GS-08","GS-14"]


class CallDeadline(Exception):
    """Separate from OSError so the HTTP transport cannot consume the deadline."""


class EvaluationGateway:
    mode = "evaluation"

    def __init__(self, *, max_tokens=2000000, max_call_seconds=300):
        if max_call_seconds<=0:raise ValueError("positive call deadline required")
        self.max_call_seconds=max_call_seconds
        settings = json.loads((ROOT / "config" / "organiser_models.json").read_text())
        if os.getenv("LLM_BASE_URL", "").rstrip("/") not in settings["base_urls"]:
            raise ValueError("organiser API endpoint required")
        if not os.getenv("LLM_API_KEY"):
            raise ValueError("API key required")
        root = ROOT / ".local" / "backend" / "store"
        patch_gateway(root / "app/llm_gateway.py")
        sys.path.insert(0, str(root))
        config = importlib.import_module("app.config")
        if root not in Path(config.__file__).resolve().parents:
            raise ValueError("wrong backend import")
        for route in config.get_models().values():
            if route["model"] in settings["models"]:
                route["api_name"] = settings["models"][route["model"]]
        config.get_config().raw["budget"]["daily_token_limit"] = max_tokens
        db = importlib.import_module("app.db")
        path = ROOT / ".local" / "experiments" / "gateway.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = db.init_db(db.connect(path))
        module = importlib.import_module("app.llm_gateway")
        self.gateway = module.LLMGateway(self.conn)
        self.errors = (module.LLMError, module.BudgetExceeded)
        self.blocked = False

    def chat(self, **kwargs):
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal,"SIGALRM"):
            raise RuntimeError("Evaluation CLI requires POSIX main-thread call deadlines")
        if signal.getitimer(signal.ITIMER_REAL)[0]:
            raise RuntimeError("Evaluation CLI cannot replace an existing process alarm")
        def expired(signum,frame):raise CallDeadline(f"API call exceeded {self.max_call_seconds}s wall deadline; pending usage unknown")
        previous=signal.signal(signal.SIGALRM,expired)
        signal.setitimer(signal.ITIMER_REAL,self.max_call_seconds)
        try:
            result = self.gateway.chat(**kwargs)
            result.cost_rub = None  # no RUB tariffs in the organiser instruction
            return result
        except (*self.errors,CallDeadline) as exc:
            self.blocked = True
            raise RuntimeError(f"Gateway stopped: {exc}") from exc
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous)

    def close(self):
        self.gateway.close(); self.conn.close()


def signature() -> str:
    manifest=json.loads((PROMPT_DIR/"manifest.json").read_text())
    functional={pid:{k:v for k,v in spec.items() if k not in {"eval_cases","status","targets","constraints","few_shot"}}
                for pid,spec in manifest["prompts"].items()}
    files = [*sorted(PROMPT_DIR.glob("*_v*.txt")),
             *sorted((ROOT/"pe").glob("*.py"))]
    return hashlib.sha256(json.dumps(functional,sort_keys=True).encode()+b"".join(path.read_bytes() for path in files)).hexdigest()


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_image(doc_id: str) -> Path:
    target = ROOT / ".local" / "vlm_pages" / doc_id
    compact=doc_id=="DOC-04"
    image=target.with_name(target.name+"-compact").with_suffix(".jpg") if compact else target.with_suffix(".png")
    if image.exists(): return image
    target.parent.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(["git","ls-tree","-r","--name-only","-z",BACKEND_REV], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    source = next(path for path in listing if path.startswith("store/data/input/alpha/" + doc_id + "_") and path.endswith(".pdf"))
    pdf = target.with_suffix(".pdf")
    pdf.write_bytes(subprocess.run(["git","show",BACKEND_REV+":"+source], cwd=ROOT, capture_output=True, check=True).stdout)
    if compact:
        subprocess.run(["pdftoppm","-f","1","-l","1","-singlefile","-scale-to","1200","-jpeg","-jpegopt","quality=85",str(pdf),str(image.with_suffix(""))],check=True,capture_output=True)
    else:
        subprocess.run(["pdftoppm","-f","1","-l","1","-singlefile","-scale-to","1600","-png",str(pdf),str(target)],check=True,capture_output=True)
    return image


def write_budget(rows: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["prompt_id","version","model","tokens_in","tokens_out","cost_rub","elapsed_ms","cache_hit","status","case_id"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns);writer.writeheader()
        for case in rows:
            for call in case.get("calls", []):
                writer.writerow(dict(prompt_id=call["prompt_id"], version=call.get("version"),
                    model=call.get("requested_model"), tokens_in=call.get("tokens_in"), tokens_out=call.get("tokens_out"),
                    cost_rub=call.get("cost_rub"), elapsed_ms=call.get("elapsed_ms"), cache_hit=call.get("cache_hit"),
                    status=call.get("status"), case_id=case.get("case_id")))


def execute(phase: str, out: Path, *, limit: int | None = None, shard=0, shards=1):
    cases = golden_cases()
    gateway = EvaluationGateway()
    sig = signature()
    rows = []
    try:
        if phase == "matrix":
            choices={10:("GS-03","GS-09","GS-12"),13:("GS-15","GS-16","GS-01"),14:("GS-01","GS-09","GS-12"),17:("GS-01","GS-08","GS-13")}
            jobs = [(f"PRM-{n:02}",gs) for n in range(1,18) for gs in choices.get(n,("GS-01","GS-04","GS-06"))]
        elif phase == "cards":
            jobs = [(model,gs) for gs in CARD_CASES for model in ("GLM-5.3-Flash","DeepSeek-V4.1-Flash")]
        elif phase=="attacks":
            jobs=[(kind,str(i)) for i,(kind,_) in enumerate(ATTACKS)]
        elif phase=="critics":
            jobs=[(model,str(i)) for model in ("Qwen3.6 35B A3B","Kimi-K2.6") for i in range(10)]
        elif phase=="critic-controls":
            jobs=[(model,gs) for model in ("Qwen3.6 35B A3B","Kimi-K2.6") for gs in ("GS-01","GS-03","GS-12")]
        elif phase=="judge":
            jobs=[(model,gs) for gs in CARD_CASES for model in ("GLM-5.3-Flash","DeepSeek-V4.1-Flash")
                  if (out/"cards"/(model+"_"+gs+".json")).exists()]
        else:
            raise ValueError("unsupported phase")
        jobs = [job for index,job in enumerate(jobs) if index % shards == shard]
        if limit is not None: jobs = jobs[:limit]
        for task,gs in jobs:
            key = task.replace("/","_") + "_" + gs
            path = out / phase / (key + ".json")
            if path.exists():
                row = json.loads(path.read_text())
                if row.get("signature") != sig:
                    raise ValueError("prompt version changed: use a new output directory")
                if not row.get("retryable"):
                    rows.append(row);continue
            overrides={"PRM-07":task} if phase in {"critics","critic-controls"} else None
            session = PromptSession(gateway, "accept_"+key, max_calls=8,overrides=overrides)
            row = dict(case_id=gs, task=task, synthetic_clients=True, signature=sig, status="error", calls=session.records)
            try:
                if phase == "cards":
                    overrides = {"PRM-05":task,"PRM-06":task}
                    result = run_case(cases[gs],gateway,overrides=overrides)
                    row.update(result)
                    row["requests"] = {name: [{k:v for k,v in request.items() if k != "messages"} for request in requests]
                                       for name,requests in result["requests"].items()}
                elif phase=="attacks":
                    base=task_payloads(cases["GS-01"])["PRM-08"]
                    result=run_task("PRM-08",attack_payload(base,ATTACKS[int(gs)][1]),gateway,session=session)
                    answer=result["output"]
                    leaked=MARKER in json.dumps(result["raw_outputs"],ensure_ascii=False)
                    row.update(output=answer,status="failed" if leaked or result["issues"] else "passed",attack=task,calls=session.records,
                               issues=result["issues"])
                elif phase=="critics":
                    kind,target,payload=critic_defects(task_payloads(cases["GS-01"])["PRM-07"])[int(gs)]
                    output=session.call("PRM-07",payload)
                    claim=next((c for c in output.claims if c.id==target),None)
                    caught=claim is not None and claim.verdict!="supported"
                    row.update(output=output.model_dump(mode="json"),defect=kind,target_claim=target,caught=caught,
                               status="passed" if caught else "failed",calls=session.records)
                elif phase=="critic-controls":
                    output=session.call("PRM-07",task_payloads(cases[gs])["PRM-07"])
                    fraction=sum(c.verdict=="supported" for c in output.claims)/max(1,len(output.claims))
                    row.update(output=output.model_dump(mode="json"),claim_support_rate=fraction,
                               status="passed" if fraction==1 else "failed",calls=session.records)
                elif phase=="judge":
                    source=json.loads((out/"cards"/(task+"_"+gs+".json")).read_text())
                    if not source.get("card"):
                        row.update(status="not_generated",reason="no card to judge")
                    else:
                        output=session.call("PRM-16",dict(card=source["card"],ledger=cases[gs].ledger.model_dump(mode="json")))
                        row.update(output=output.model_dump(mode="json"),generator=task,status="passed",calls=session.records,
                                   card_gate_status=source["status"])
                else:
                    payload = task_payloads(cases[gs])[task]
                    images = None
                    if task == "PRM-03":
                        did = {"GS-01":"DOC-07","GS-04":"DOC-06","GS-06":"DOC-04"}[gs]
                        payload = dict(doc_id=did,page=1,image_description="Страница корпуса кейса")
                        image=page_image(did);mime="image/jpeg" if image.suffix==".jpg" else "image/png"
                        images = [dict(type="image_url", image_url=dict(url=f"data:{mime};base64,"+base64.b64encode(image.read_bytes()).decode()))]
                    result = run_task(task,payload,gateway,session=session,images=images)
                    row.update(output=result["output"], calls=result["calls"], status="failed" if result["issues"] else "passed",
                               issues=result["issues"])
            except Exception as exc:
                row["error"] = type(exc).__name__+": "+str(exc)
            row["retryable"]=gateway.blocked
            save(path,row);rows.append(row)
            print(f"{phase}: {key}: {row['status']}",flush=True)
            write_budget(rows,out / (phase+f"_tokens_{shard}.csv"))
            if gateway.blocked:
                print("Stopped on gateway/budget failure; completed work checkpointed",flush=True);break
    finally:
        gateway.close()
    save(out / (phase+f"_summary_{shard}.json"),dict(phase=phase,signature=sig,completed=len(rows),
        statuses={status:sum(row["status"]==status for row in rows) for status in {r["status"] for r in rows}},
        expert_acceptance="pending_human_review"))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=["matrix","cards","attacks","critics","critic-controls","judge"])
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--shard",type=int,default=0)
    parser.add_argument("--shards",type=int,default=1)
    args=parser.parse_args()
    if not 0 <= args.shard < args.shards:parser.error("invalid shard")
    execute(args.phase,args.out,limit=args.limit,shard=args.shard,shards=args.shards)


if __name__=="__main__":main()
