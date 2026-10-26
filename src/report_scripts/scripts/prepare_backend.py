"""Export the existing gateway for local integration without merging branches."""
from pathlib import Path
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pe.gateway_patch import patch_gateway

ROOT = Path(__file__).resolve().parent.parent
REVISION = "1e794c8b037b63c4fcbd5c34caeaaf72c8f33f04"
FILES = [
    "store/app/__init__.py", "store/app/config.py", "store/app/util.py",
    "store/app/llm_gateway.py", "store/app/db.py", "store/app/schema.sql",
    "store/config/models.yaml", "store/config/settings.yaml",
]


def main():
    target = ROOT / ".local" / "backend"
    # Never overwrite endpoint/pricing edits in a previously prepared snapshot.
    if target.exists():
        raise SystemExit(f"Snapshot already exists: {target}")
    contents = {name: subprocess.run(
        ["git", "show", f"{REVISION}:{name}"], cwd=ROOT, check=True,
        capture_output=True).stdout for name in FILES}
    for name, content in contents.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    patch_gateway(target / "store/app/llm_gateway.py")
    (target / "snapshot.json").write_text(json.dumps(
        dict(revision=REVISION, files=FILES), indent=2) + "\n", encoding="utf-8")
    print(target / "store")


if __name__ == "__main__":
    main()
