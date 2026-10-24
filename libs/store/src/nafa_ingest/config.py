from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    """Load YAML config and resolve its paths relative to the config directory.

    Returns (settings, sources, config_directory).
    """
    config_path = Path(config_path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    settings = raw.get("settings", {})
    sources = raw.get("sources", [])

    # Permit env values such as ${NAFA_USER_AGENT}; source URLs remain in YAML.
    settings = _expand_environment(settings)
    for source in sources:
        _expand_environment(source, in_place=True)

    user_agent = settings.get("user_agent")
    if not user_agent or ("${" in str(user_agent)):
        raise ValueError(
            "Set NAFA_USER_AGENT in .env (copy .env.example) before collecting remote sources."
        )

    config_dir = config_path.parent
    database = Path(settings.get("database", "../data/nafa.sqlite3"))
    settings["database_path"] = (
        database if database.is_absolute() else (config_dir / database).resolve()
    )
    settings.setdefault("timeout_seconds", 25)
    settings.setdefault("retries", 5)
    settings.setdefault("retry_backoff_seconds", 0.7)
    settings.setdefault("default_timezone", "UTC")
    settings.setdefault("tracking_parameters", [])
    settings.setdefault("tracking_prefixes", ["utm_"])

    for source in sources:
        source.setdefault("enabled", True)
        source.setdefault("tier_weight", 0.4)
        source["_config_dir"] = str(config_dir)

    return settings, sources, config_dir


def _expand_environment(value: Any, *, in_place: bool = False) -> Any:
    if isinstance(value, dict):
        target = value if in_place else {}
        for key, item in list(value.items()):
            target[key] = _expand_environment(item)
        return target
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value
