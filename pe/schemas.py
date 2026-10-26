"""Strict draft contracts from BA docs/04_TZ_prompt_engineer.md §3.3.

These validate shape and provenance. Semantic checks against source text and
DA-computed values belong to the critic and deterministic evaluation harness.
"""
from __future__ import annotations

from datetime import date as CalendarDate
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Factor = Literal["RATE", "FX", "INFL", "ENERGY", "COMMOD", "DEMAND", "LABOR",
                 "CREDIT", "BUDGET", "REG", "GEOPOL", "CPTY"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Fact(StrictModel):
    value: str
    unit: str | None = None
    date: CalendarDate | None = None
    subject: str
    source_id: str = Field(min_length=1)
    source_span: str = Field(min_length=1)
    certainty: Literal["confirmed", "forecast", "opinion"]


class EventExtraction(StrictModel):
    event_type: str = Field(pattern=r"^ET-(0[1-9]|1[0-9]|20)$")
    entities: list[str]
    facts: list[Fact]
    iota: dict[Factor, float]
    direction: dict[Factor, Literal[-1, 0, 1]]
    severity: int = Field(ge=1, le=5)
    geo_scope: Literal["national", "regional", "global", "unknown"]
    horizon: str
    rationale: str

    @model_validator(mode="after")
    def check_factors(self):
        if set(self.iota) != set(self.direction):
            raise ValueError("iota and direction must contain the same factors")
        if any(not 0 <= value <= 1 for value in self.iota.values()):
            raise ValueError("iota values must be in [0, 1]")
        return self


class BankViewRecord(StrictModel):
    indicator: str
    horizon: str
    value: str
    unit: str | None = None
    stance: str
    as_of: CalendarDate
    doc_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    quote_span: str = Field(min_length=1)
    extraction_method: str


class Claim(StrictModel):
    id: str
    text: str
    source_ids: list[str] = Field(min_length=1)


class Inference(StrictModel):
    impact_chain: list[Claim]
    risks: list[Claim]
    opportunities: list[Claim]
    actions_or_questions: list[Claim]
    confidence: float = Field(ge=0, le=1, description="Copied from code FM-20")


class InsightCard(StrictModel):
    confirmed_facts: list[Claim]
    why_you: list[Claim] = Field(default_factory=list)
    bank_view_status: Literal["available", "missing", "outdated", "conflict"]
    bank_view_items: list[Claim]
    model_inference: Inference
    provenance_labels: dict[str, Literal["fact", "bank_view", "ai_inference"]]
    relevance: float = Field(ge=0, le=1, description="Copied from DA code")
    offer: str | None = None
    disclaimer: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_labels(self):
        groups = (
            (self.confirmed_facts, "fact"),
            (self.bank_view_items, "bank_view"),
            (self.why_you + self.model_inference.impact_chain + self.model_inference.risks
             + self.model_inference.opportunities + self.model_inference.actions_or_questions,
             "ai_inference"),
        )
        seen = set()
        for claims, expected in groups:
            for claim in claims:
                if claim.id in seen or self.provenance_labels.get(claim.id) != expected:
                    raise ValueError(f"missing/wrong provenance label or duplicate id: {claim.id}")
                seen.add(claim.id)
        if set(self.provenance_labels) != seen:
            raise ValueError("provenance labels must match claims exactly")
        return self


class CriticClaim(StrictModel):
    id: str
    text: str
    layer: Literal["fact", "bank_view", "ai_inference"]
    verdict: Literal["supported", "unsupported", "contradicted", "outdated"]
    evidence_ids: list[str]


class CriticVerdict(StrictModel):
    claims: list[CriticClaim]
    numbers_check: list[str]
    coverage_6_questions: dict[str, bool]
    overall: Literal["pass", "fix", "retry_search"]
    fix_instructions: list[str]


class ChatClaim(Claim):
    layer: Literal["fact", "bank_view", "ai_inference"]


class ChatAnswer(StrictModel):
    answer: str
    layers: list[Literal["fact", "bank_view", "ai_inference"]]
    citations: list[str]
    out_of_scope: bool
    follow_up_suggestions: list[str]
    claims: list[ChatClaim] = Field(default_factory=list)


class EventCore(StrictModel):
    confirmed_facts: list[Claim]
    bank_view_items: list[Claim]
    common_effects: list[Claim]
    unknowns: list[str]


class ImpactClaim(Claim):
    factor: Factor
    effect: Literal["negative", "positive", "neutral", "two_branches"]


class PersonalOverlay(StrictModel):
    why_you: list[Claim]
    impact_chain: list[ImpactClaim]
    risks: list[Claim]
    opportunities: list[Claim]
    actions_or_questions: list[Claim] = Field(max_length=4)
    unknowns: list[str]


SCHEMAS = {model.__name__: model for model in (
    EventExtraction, BankViewRecord, InsightCard, CriticVerdict, ChatAnswer,
    EventCore, PersonalOverlay,
)}
