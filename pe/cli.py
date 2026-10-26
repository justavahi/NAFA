"""Export a JSON schema or validate an LLM response without calling an API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evidence import EvidenceLedger, check_card_provenance
from .schemas import SCHEMAS
from .contracts import INPUT_SCHEMAS, OUTPUT_SCHEMAS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    schemas = {**INPUT_SCHEMAS, **OUTPUT_SCHEMAS, "EvidenceLedger": EvidenceLedger}
    parser.add_argument("schema", choices=schemas)
    parser.add_argument("path", nargs="?", type=Path, help="JSON response to validate")
    parser.add_argument("--ledger", type=Path, help="Check InsightCard references against this ledger")
    args = parser.parse_args()
    if args.ledger and (args.schema != "InsightCard" or args.path is None):
        parser.error("--ledger requires InsightCard and a response path")
    model = schemas[args.schema]
    if args.path:
        response = model.model_validate_json(args.path.read_text(encoding="utf-8"))
        if args.ledger:
            ledger = EvidenceLedger.model_validate_json(args.ledger.read_text(encoding="utf-8"))
            report = check_card_provenance(response, ledger)
            print(report.model_dump_json(indent=2))
            if not report.ok:
                raise SystemExit(1)
        else:
            print(f"OK: {args.path} matches {args.schema}")
    else:
        print(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
