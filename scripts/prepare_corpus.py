"""Import supplied cached PDF extraction into a runtime; no remote model calls.

Unreviewed excerpts enter the admin queue, not the bank-position evidence.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",type=Path,required=True)
    args=parser.parse_args()
    for line in args.env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key,value=line.split("=",1);os.environ[key]=value.strip().strip('\"').strip("'")
    store=ROOT / "src/store";sys.path.insert(0,str(store))
    from app.config import data_dir
    from app.db import init_db
    from app.ingest.alfa_corpus import parse_corpus
    from app.llm_gateway import LLMError
    shutil.copytree(store / "data/corpus_extraction",data_dir() / "corpus_extraction",dirs_exist_ok=True)
    class Offline:
        base_url=""
        calls=0
        def chat(self,**kwargs):
            self.calls+=1
            raise LLMError("offline cache missing")
    gateway=Offline();conn=init_db()
    try:
        result=parse_corpus(conn,store / "data/input/alpha",gateway)
        # Cached confidence alone never establishes a human review.
        conn.execute("UPDATE chunks SET needs_manual_check=1 WHERE verified_by IS NULL OR verified_by!='manual'")
        (data_dir() / "corpus_import.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(dict(documents=conn.execute("SELECT COUNT(*) FROM alfa_docs WHERE parse_status='parsed'").fetchone()[0],
            pending_review=conn.execute("SELECT COUNT(*) FROM chunks WHERE needs_manual_check=1").fetchone()[0],
            human_review_performed=False,remote_model_calls=0,missing_cache_attempts=gateway.calls),indent=2))
    finally:conn.close()


if __name__=="__main__":main()
