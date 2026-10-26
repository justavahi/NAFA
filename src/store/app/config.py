"""Configuration: YAML files in config/ + environment variables. No thresholds in code."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.getenv("NAFA_ROOT", Path(__file__).resolve().parent.parent))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Variables already set in the real environment always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if key.strip() and val != "":
            os.environ.setdefault(key.strip(), val)


_load_dotenv(ROOT / ".env")


def _abs(p: str | Path) -> Path:
    """Relative paths from env/.env are resolved against the project root, not the current directory."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def config_dir() -> Path:
    return Path(os.getenv("NAFA_CONFIG_DIR", ROOT / "config"))


def data_dir() -> Path:
    p = _abs(os.getenv("NAFA_DATA_DIR", "data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    p = _abs(os.getenv("NAFA_DB_PATH") or data_dir() / "nafa.db")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class Config:
    """Thin wrapper over a dict with dotted access: cfg.get('http.timeout_s')."""

    def __init__(self, raw: dict):
        self.raw = raw

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def _load_yaml(name: str) -> dict:
    with open(config_dir() / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_config() -> Config:
    raw = _load_yaml("settings.yaml")
    limit = os.getenv("NAFA_DAILY_TOKEN_LIMIT")
    if limit is not None:
        parsed = int(limit)
        if parsed <= 0:
            raise ValueError("NAFA_DAILY_TOKEN_LIMIT must be positive")
        raw.setdefault("budget", {})["daily_token_limit"] = parsed
    return Config(raw)


@lru_cache(maxsize=1)
def get_sources() -> dict[str, dict]:
    return _load_yaml("sources.yaml")["sources"]


@lru_cache(maxsize=1)
def get_models() -> dict[str, dict]:
    return _load_yaml("models.yaml")["roles"]


@lru_cache(maxsize=1)
def get_series() -> dict[str, dict]:
    return _load_yaml("series.yaml")["series"]


def reload_config() -> None:
    for fn in (get_config, get_sources, get_models, get_series):
        fn.cache_clear()
