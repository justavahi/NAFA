"""Input/output contracts for all seventeen PE tasks."""
from __future__ import annotations

from datetime import date as CalendarDate
from typing import Any, Literal

from pydantic import Field, model_validator

from .evidence import Document, EvidenceLedger
from .schemas import (BankViewRecord, ChatAnswer, Claim, CriticVerdict, EventCore,
                      EventExtraction, Factor, InsightCard, PersonalOverlay, StrictModel, SCHEMAS)
from .interpretation import InterpretationPlan
from .context import RoleCard


class ImpactVector(StrictModel):
    iota: dict[Factor, float]
    direction: dict[Factor, Literal[-1, 0, 1]]
    severity: int = Field(ge=1, le=5)
    rationale: str

    @model_validator(mode="after")
    def factors(self):
        if set(self.iota) != set(self.direction) or any(not 0 <= x <= 1 for x in self.iota.values()):
            raise ValueError("matching factor keys and iota in [0,1] required")
        return self


class PageNumber(StrictModel):
    value: str | float
    unit: str | None
    subject: str
    date: CalendarDate | None = None


class SourcePageBlock(StrictModel):
    block_type: Literal["text", "table", "chart_caption", "chart_estimate"]
    text: str
    numbers: list[PageNumber]
    needs_manual_check: bool = False

    @model_validator(mode="after")
    def chart_numbers(self):
        if self.block_type == "chart_estimate" and self.numbers:
            raise ValueError("unlabelled chart estimates cannot populate confirmed numbers")
        return self


class PageBlock(SourcePageBlock):
    text: str = Field(max_length=300)


class SourceChunk(StrictModel):
    doc_id: str
    page: int = Field(ge=1)
    title: str
    as_of_date: CalendarDate | None
    blocks: list[SourcePageBlock]
    needs_manual_check: bool


class Chunk(SourceChunk):
    blocks: list[PageBlock] = Field(max_length=6)

    @model_validator(mode="after")
    def bounded_numbers(self):
        if sum(len(block.numbers) for block in self.blocks)>12:
            raise ValueError("at most twelve key numbers per page")
        return self


class BankViewExtraction(StrictModel):
    items: list[BankViewRecord]
    unknowns: list[str]


class GroundedClaim(Claim):
    layer: Literal["fact", "bank_view", "ai_inference"]


class GroundedExplanation(StrictModel):
    claims: list[GroundedClaim]
    limitations: list[str]
    questions: list[str]


class OfferText(StrictModel):
    shown: bool
    label: Literal["Предложение банка"]
    text: str | None
    source_ids: list[str]
    reason: str

    @model_validator(mode="after")
    def content(self):
        if self.shown and (not self.text or not self.source_ids):
            raise ValueError("shown offer requires text and evidence")
        if not self.shown and self.text is not None:
            raise ValueError("hidden offer must not contain promotional text")
        return self


class JudgeScore(StrictModel):
    coverage: int = Field(ge=1, le=5)
    mechanisms: int = Field(ge=1, le=5)
    personalization: int = Field(ge=1, le=5)
    actions: int = Field(ge=1, le=5)
    grounding: int = Field(ge=1, le=5)
    overall: float = Field(ge=1, le=5)
    notes: list[str]


class ExtractionInput(StrictModel):
    document: Document
    event_types: list[dict[str, Any]]


class ImpactInput(StrictModel):
    event: EventExtraction
    prototype: dict[Factor, float]


class PageInput(StrictModel):
    doc_id: str
    page: int = Field(ge=1)
    image_description: str = ""


class BankInput(StrictModel):
    chunks: list[SourceChunk]

    @model_validator(mode="after")
    def reviewed_sources(self):
        if any(chunk.needs_manual_check or any(block.needs_manual_check for block in chunk.blocks) for chunk in self.chunks):
            raise ValueError("review VLM/source chunks before bank extraction")
        return self


class CoreInput(StrictModel):
    event: EventExtraction
    entries: list[dict[str, Any]]
    documents: list[Document]
    bank_view_status: Literal["available", "missing", "outdated", "conflict"]
    repair_instructions: list[str] = Field(default_factory=list)


class PersonalInput(StrictModel):
    event_core: EventCore
    ledger: EvidenceLedger
    persona: str
    computed: dict[str, Any]


class ContextCoreInput(CoreInput):
    selected_news_ids: list[str] = Field(min_length=1)


class ContextPersonalInput(PersonalInput):
    role_card: RoleCard | None = None
    selected_news_ids: list[str] | None = None

    @model_validator(mode="after")
    def matching_role(self):
        if self.role_card and self.role_card.persona != self.persona:
            raise ValueError("role card differs from persona")
        return self


class FocusedPersonalInput(StrictModel):
    event_core: EventCore
    evidence_view: dict[str, Any]
    persona: str
    computed: dict[str, Any]
    role_card: RoleCard | None = None
    selected_news_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def matching_role(self):
        if self.role_card and self.role_card.persona != self.persona:
            raise ValueError("role card differs from persona")
        return self


class CriticInput(StrictModel):
    card: InsightCard
    impacts: list[dict[str, Any]]
    ledger: EvidenceLedger
    computed: dict[str, Any]
    scope: Literal["card", "dialogue"] = "card"
    question: str | None = None


class ChatInput(StrictModel):
    card: InsightCard
    ledger: EvidenceLedger
    question: str = Field(min_length=1, max_length=4000)
    computed: dict[str, Any]


class ExplanationInput(StrictModel):
    ledger: EvidenceLedger
    computed: dict[str, Any]
    context: dict[str, Any]


class JudgeInput(StrictModel):
    card: InsightCard
    ledger: EvidenceLedger


INPUT_SCHEMAS = {cls.__name__: cls for cls in (
    ExtractionInput, ImpactInput, PageInput, BankInput, CoreInput, PersonalInput,
    CriticInput, ChatInput, ExplanationInput, JudgeInput, ContextCoreInput, ContextPersonalInput, FocusedPersonalInput,
)}
OUTPUT_SCHEMAS = {**SCHEMAS, **{cls.__name__: cls for cls in (
    ImpactVector, Chunk, BankViewExtraction, GroundedExplanation, OfferText, JudgeScore,InterpretationPlan,
)}}
