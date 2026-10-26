from __future__ import annotations

from ..config import get_sources
from ..http import PoliteClient
from .base import Connector
from .cbr import CbrDailyConnector, CbrKeyRateConnector
from .rss import RssConnector

PARSERS: dict[str, type[Connector]] = {
    "rss": RssConnector,
    "cbr_daily": CbrDailyConnector,
    "cbr_keyrate": CbrKeyRateConnector,
}
MACRO_PARSERS = {"cbr_daily", "cbr_keyrate"}


def build_connector(source_id: str, http: PoliteClient) -> Connector:
    cfg = get_sources().get(source_id)
    if cfg is None:
        raise KeyError(f"unknown source {source_id}")
    if not cfg.get("enabled", True):
        raise NotImplementedError(f"{source_id}: source is disabled in config/sources.yaml")
    if not cfg.get("url") and cfg.get("parser") != "internal":
        raise NotImplementedError(f"{source_id}: no verified URL configured")
    parser = cfg.get("parser")
    if parser not in PARSERS:
        raise NotImplementedError(f"{source_id}: parser '{parser}' has no connector yet")
    return PARSERS[parser](source_id, cfg, http)


def is_macro(source_id: str) -> bool:
    return get_sources()[source_id].get("parser") in MACRO_PARSERS
