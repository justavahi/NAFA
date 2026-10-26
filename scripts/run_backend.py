"""Start the merged Backend with PE installed, or explicit provisional baseline."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["pe", "baseline"], default="pe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--env-file", type=Path, action="append", default=[], help="Private runtime environment; repeat for model credentials")
    args = parser.parse_args()
    store, pe = ROOT / "src/store", ROOT / "src/report_scripts"
    environment = os.environ.copy()
    for path in args.env_file:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            environment[key.strip()] = value.strip().strip('\"').strip("'")
    environment["NAFA_BACKEND_ROOT"] = str(store)
    environment["PYTHONPATH"] = os.pathsep.join([str(store), str(pe), environment.get("PYTHONPATH", "")])
    command = ["uv", "run", "--project", str(store), "uvicorn"]
    command += ["pe.backend_app:create_app", "--factory"] if args.mode == "pe" else ["app.api:app"]
    command += ["--host", args.host, "--port", str(args.port)]
    raise SystemExit(subprocess.call(command, cwd=ROOT, env=environment))
