"""Prepare a separate local runtime from demo or participant CSVs; never calls models.

uv run --project src/store --with matplotlib==3.11.2 --with tabulate==0.10.0 python scripts/prepare_runtime.py --demo
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import csv
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--demo", action="store_true")
    choice.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reuse-if-ready", action="store_true", help="Reuse a previously prepared runtime (container init)")
    args = parser.parse_args()
    output = (args.output or ROOT / ".local" / ("demo" if args.demo else "participant")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    env_path = output / "runtime.env"
    if env_path.exists():
        if args.reuse_if_ready and (output / "nafa.db").is_file():
            import sqlite3
            with sqlite3.connect(output / "nafa.db") as existing:
                ready = existing.execute("SELECT COUNT(*) FROM pe_da_profiles").fetchone()[0] > 0
            if ready:
                print("Existing runtime is ready; database and credentials preserved.")
                return
        raise SystemExit("Runtime already exists. Reuse its runtime.env or choose a new --output; existing database and credentials preserved.")
    store = ROOT / "src/store"
    values = dict(NAFA_BACKEND_ROOT=str(store), NAFA_ROOT=str(store), NAFA_DATA_DIR=str(output / "data"),
        NAFA_DB_PATH=str(output / "nafa.db"), NAFA_PSEUDONYM_SALT=secrets.token_hex(32),
        NAFA_ADMIN_TOKEN=secrets.token_urlsafe(32), NAFA_DEMO_MODE=str(args.demo).lower())
    os.environ.update(values)
    sys.path[:0] = [str(store),str(ROOT / "src/report_scripts"),str(ROOT / "src/da_scripts")]
    from app.db import init_db, sync_sources
    from app.ingest.clients import load_all
    from analytics.data_io import load_clients, load_link, load_payments
    from analytics.run_all import run_pipeline
    from pe.production import import_profiles
    source = args.data_dir.resolve() if args.data_dir else output / "input"
    # Raw identifiers may occur in third-party parser messages: keep all logs private.
    with (output / "prepare.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
        if args.demo:
            from app.synth import generate_clients, load_synthetic_news
            files=generate_clients(source)
            # Match the case's actual category vocabulary; generated data stay synthetic.
            with files["clients"].open(newline="") as stream:
                reader=csv.DictReader(stream); columns=reader.fieldnames; rows=list(reader)
            for row in rows:
                row["CL_TYPE"]={"existing":"CLIENT","prospective":"PROSPECT"}[row["CL_TYPE"]]
            with files["clients"].open("w",newline="") as stream:
                writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(rows)
        clients, cp=load_clients(source); _,lp=load_link(source); _,pp=load_payments(source)
        ambiguous=clients.loc[clients.SYNTHETIC_U.duplicated(keep=False),"SYNTHETIC_U"].astype(str).tolist()
        del clients
        artifacts=run_pipeline(source,output / "processed",do_charts=False,do_quality=False)
        conn=init_db();sync_sources(conn)
        try:
            load_all(conn,cp,lp,pp)
            result=import_profiles(conn,Path(artifacts["features"]).parent,synthetic=args.demo,ambiguous_ids=ambiguous)
            if args.demo:
                load_synthetic_news(conn)
                values["NAFA_CLIENT_TOKENS"]=json.dumps({f"demo-CA-{i:02d}":f"SYN-U-{i:04d}" for i in range(1,9)})
        finally:
            conn.close()
    with env_path.open("x") as stream:
        os.chmod(env_path,0o600)
        stream.write("# Private runtime. Add model credentials in a separate --env-file.\n")
        stream.write("\n".join(key+"="+value for key,value in values.items())+"\n")
    print(json.dumps(dict(**result,model_calls=0,env_file=str(env_path),
        bank_corpus_reviewed=False,quality_labels_generated=False),ensure_ascii=False,indent=2))
    print("Start: uv run --project src/store python scripts/run_backend.py --env-file",env_path)


if __name__ == "__main__":
    main()
