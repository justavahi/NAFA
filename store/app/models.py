"""Data contracts from the spec (section 4). Pydantic validates every record before it is stored."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Document(BaseModel):
    doc_id: str
    source_id: str
    kind: Literal["news", "analytics", "release"] = "news"
    origin: Literal["feed", "byo", "synthetic"] = "feed"
    title: str
    url: str
    canonical_url: str
    published_at: datetime                      # must carry tzinfo (acceptance WP-B3)
    fetched_at: datetime
    language: str = "unknown"
    category: Optional[str] = None
    snippet: Optional[str] = None
    text: Optional[str] = None                  # only if the licence allows (sources.yaml legal.store)
    tier_weight: float
    content_hash: str
    cluster_id: Optional[str] = None
    stale: bool = False

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _tz_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must include a time zone")
        return v

    @field_validator("title")
    @classmethod
    def _title_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty title")
        return v.strip()


class MacroPoint(BaseModel):
    series_id: str = Field(pattern=r"^M-\d{2}(\.[a-z_]+)?$")
    date: date
    value: float
    unit: Optional[str] = None
    source_id: str


class NumberFact(BaseModel):
    value: float
    unit: Optional[str] = None
    subject: Optional[str] = None
    date: Optional[str] = None
    is_fact: bool = True                         # False for numbers read off charts without labels


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    page: int
    block_type: Literal["text", "table", "chart_caption", "chart_estimate"]
    text: str
    numbers: list[NumberFact] = []
    as_of_date: Optional[str] = None
    extraction_model: Optional[str] = None
    extraction_confidence: float = 0.0
    needs_manual_check: bool = True
    verified_by: Optional[Literal["manual"]] = None
    image_path: Optional[str] = None


class BankViewIn(BaseModel):
    indicator: str
    horizon: str
    value: Optional[float] = None
    unit: Optional[str] = None
    stance: Optional[str] = None
    as_of: date
    doc_id: Optional[str] = None
    page: Optional[int] = None
    quote_span: Optional[str] = None
    extraction_method: Literal["text", "chart_estimate"] = "text"
    verified_by: Literal["manual", "auto", "seed"] = "auto"
    note: Optional[str] = None
