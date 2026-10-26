"""Recheck the supplied corpus using only cached extraction; no remote calls."""
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "src/store"
OUTPUT = ROOT / ".local/audit/corpus"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.environ.update(NAFA_ROOT=str(STORE), NAFA_DATA_DIR=str(OUTPUT), NAFA_DB_PATH=str(OUTPUT / "audit.db"), LLM_BASE_URL="")
    sys.path.insert(0, str(STORE))
    shutil.copytree(STORE / "data/corpus_extraction", OUTPUT / "corpus_extraction", dirs_exist_ok=True)
    from app.config import get_config
    from app.db import connect, init_db
    from app.ingest.alfa_corpus import parse_corpus, verify_key_numbers
    from app.search import evaluate_alfa_queries
    config = get_config()
    config.raw["corpus"]["dpi"] = 72
    config.raw["corpus"]["max_side_px"] = 900

    class OfflineOnly:
        base_url = ""
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            raise RuntimeError("no remote model calls allowed in this corpus check")

    gateway = OfflineOnly()
    conn = init_db(connect())
    try:
        parsed = parse_corpus(conn, STORE / "data/input/alpha", gateway)
        numbers = verify_key_numbers(conn, STORE / "data/ref/key_numbers_to_verify.csv")
        search = evaluate_alfa_queries(conn, STORE / "data/ref/alfa_search_golden.csv", 3)
        report = dict(mode="offline_cached_extraction", parsing=parsed, reference_numbers=numbers,
            search=search, network_model_calls=0, missing_cache_attempts=gateway.calls,
            parsed_document_count=conn.execute("SELECT COUNT(*) FROM alfa_docs WHERE parse_status='parsed'").fetchone()[0],
            human_review_performed=False, approved_live_vlm_tested=False)
        # Portable public artifact; runtime DB/pages remain ignored locally.
        public = json.dumps(report, ensure_ascii=False, indent=2).replace(str(ROOT), "<repo>")
        path = ROOT / "docs/verification/corpus_check.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(public+"\n")
        print(public)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
