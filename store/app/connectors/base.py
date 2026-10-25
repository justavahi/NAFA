from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..http import PoliteClient


@dataclass
class ItemError:
    index: int
    error_type: str                              # missing_title | missing_link | missing_date | bad_date | validation | ...
    detail: str = ""


@dataclass
class FetchOutcome:
    items: list[Any]                             # Document or MacroPoint
    items_total: int
    item_errors: list[ItemError] = field(default_factory=list)
    http_status: int | None = None
    attempts: int = 1
    robots: str = "not_checked"


class Connector(ABC):
    """Interface from spec 5.8: Connector.fetch() -> documents. One class per parser type; many sources per class."""

    def __init__(self, source_id: str, cfg: dict, http: PoliteClient):
        self.source_id, self.cfg, self.http = source_id, cfg, http

    @abstractmethod
    def fetch(self, now: datetime | None = None, **kwargs) -> FetchOutcome: ...
