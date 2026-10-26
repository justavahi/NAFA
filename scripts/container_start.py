"""Load generated credentials, keeping explicit deployment environment overrides."""
import os
from pathlib import Path

runtime = Path(os.getenv("NAFA_RUNTIME_FILE", "/data/runtime/runtime.env"))
for line in runtime.read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)
os.execv("/app/src/store/.venv/bin/uvicorn", ["uvicorn", "pe.backend_app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"])
