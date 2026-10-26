"""Export versioned JSON schemas for backend integration."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.contracts import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from pe.evidence import EvidenceLedger
from pe.pipeline import CaseInput
from pe.backend_bridge import PreparedCase
from pe.backend_app import RegistrationInput


def main():
    root = Path(__file__).resolve().parent.parent / "schemas"
    root.mkdir(exist_ok=True)
    for name, model in {**INPUT_SCHEMAS, **OUTPUT_SCHEMAS, "EvidenceLedger": EvidenceLedger,
                        "CaseInput": CaseInput, "PreparedCase": PreparedCase, "RegistrationInput": RegistrationInput}.items():
        (root / f"{name}.json").write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n")
    print(root)


if __name__ == "__main__": main()
