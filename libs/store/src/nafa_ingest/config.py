from pathlib import Path
import os
import yaml
from dotenv import load_dotenv

def load_config(path="config/sources.yaml"):
    """Return (settings, source list, config directory). All source details live in YAML."""
    load_dotenv()
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    settings = raw.get("settings", {})
    settings["database_path"] = os.getenv(
        "NAFA_DATABASE", settings.get("database_path", "data/nafa.sqlite3")
    )
    settings["user_agent"] = os.getenv("NAFA_USER_AGENT", "")
    if not settings["user_agent"] or "example.org" in settings["user_agent"]:
        raise ValueError("Set NAFA_USER_AGENT to project name and real team contact in .env")
    return settings, raw.get("sources", []), config_path.parent.parent
