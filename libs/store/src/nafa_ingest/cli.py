from __future__ import annotations

import argparse
import json

from .pipeline import ingest_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect configured NAFA sources into SQLite")
    parser.add_argument("--config", default="config/sources.yaml", help="Path to sources YAML")
    parser.add_argument("--source", action="append", help="Source ID to run; can be repeated")
    parser.add_argument("--include-disabled", action="store_true", help="Also run disabled sources")
    args = parser.parse_args()
    results = ingest_all(
        args.config,
        source_ids=set(args.source) if args.source else None,
        include_disabled=args.include_disabled,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(result["status"] != "success" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
