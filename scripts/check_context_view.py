"""One scoped-context verification after development failures; no holdout."""
import argparse
import hashlib
import json
from pathlib import Path

import check_context_focus_revision as study


def prepare(out, prior):
    study.prepare(out, prior.resolve())
    protocol = json.loads((out / "protocol.json").read_text())
    protocol["profile"] = json.loads((study.ROOT / "prompts/profiles/role_focus_v2.json").read_text())
    protocol["score_jobs"] = ["DeepSeek-V4.1-Flash"]
    protocol["wrapper_hash"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    protocol["changes"] = "Quoted generation view + v8 explicit schema/one factor one mechanism. Original ledger is retained for critic. Selection is the single 2026 revision as in previous failed development attempt. No Kimi retry after two 180s timeouts."
    study.save(out / "protocol.json", protocol)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prior", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.out, args.prior)
    else:
        protocol = json.loads((args.out / "protocol.json").read_text())
        if protocol["wrapper_hash"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
            raise ValueError("frozen wrapper changed")
        study.run(args.out)
