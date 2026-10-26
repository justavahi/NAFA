"""Copy pinned BA/DA handover references; no branch merge or source modification."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {
    "f844ed647035836f5199f031ca28b22cd4be30e9": {
        **{f"docs/data/{name}.csv": f"reference/{name}.csv" for name in (
            "metrics_catalog", "factors", "event_types", "golden_set_seed", "synthetic_archetypes", "persona_weights")},
        "docs/04_TZ_prompt_engineer.md": "reference/pe_requirements.md",
    },
    "a623ed3e3c73315d2d94d70f11fd37324452dd95": {
        "docs/da/01_metrics_descriptions.md": "reference/da_metrics.md",
        "docs/da/02_formulas_and_calculations.md": "reference/da_formulas.md",
        "docs/da/03_graphics.md": "reference/da_charts.md",
        **{f"docs/assets/charts/CH-{n}.json": f"reference/charts/CH-{n}.json" for n in ("06", "07", "14", "15", "23", "25")},
    },
    "1e794c8b037b63c4fcbd5c34caeaaf72c8f33f04": {
        "store/data/ref/bank_view_manual.csv": "reference/bank_view_manual.csv",
        **{f"store/data/corpus_extraction/DOC-{n}/p1.json": f"reference/corpus/DOC-{n}_p1.json" for n in ("03", "04", "05", "06", "07", "08", "09")},
    },
}


def main():
    index = []
    for revision, paths in SOURCES.items():
        for source, target in paths.items():
            content = subprocess.run(["git", "show", f"{revision}:{source}"], cwd=ROOT,
                                     capture_output=True, check=True).stdout
            path = ROOT / target; path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != content:
                raise SystemExit(f"Reference edited locally: {target}")
            path.write_bytes(content)
            index.append(dict(revision=revision, source=source, target=target))
    (ROOT / "reference" / "snapshot.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared {len(index)} pinned reference files")


if __name__ == "__main__":
    main()
