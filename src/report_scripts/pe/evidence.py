"""Local provenance checks; successful checks do not establish semantic truth.

source_ids on PE claims refer to ledger entry IDs. The ledger resolves these
to exact source spans or DA-computed values. LLM output is never evidence.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
import re

from pydantic import Field, model_validator

from .schemas import Factor, InsightCard, StrictModel


class Document(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["news", "bank"]
    text: str = Field(min_length=1)
    as_of: date | None = None
    page: int | None = Field(default=None, ge=1)
    is_mock: bool = False


class Span(StrictModel):
    document_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class QuotedEvidence(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["NEWS_FACT", "BANK_VIEW"]
    source: Span
    number: str | float | None = None
    unit: str | None = None


class CompanyFeature(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["COMPANY_FEATURE"]
    client_id: str
    feature_id: str = Field(min_length=1)
    value: str | float | bool | None
    source_record_id: str = Field(min_length=1)


class ComputedEvidence(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["COMPUTED_VALUE"]
    formula_id: str = Field(pattern=r"^FM-(0[1-9]|1[0-9]|2[0-4])$")
    value: str | float | bool | None
    input_ids: list[str] = Field(min_length=1)
    calculation_ref: str = Field(min_length=1)


class UnknownEffect(StrictModel):
    """An explicit missing sign rule, never disguised as a DA formula."""
    id: str = Field(min_length=1)
    kind: Literal["UNKNOWN_EFFECT"]
    factor: Factor
    value: Literal["two_branches"] = "two_branches"
    input_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


Evidence = Annotated[
    QuotedEvidence | CompanyFeature | ComputedEvidence | UnknownEffect,
    Field(discriminator="kind"),
]


class EvidenceLedger(StrictModel):
    trace_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    documents: list[Document]
    entries: list[Evidence]

    @model_validator(mode="after")
    def check_provenance(self):
        documents = {doc.id: doc for doc in self.documents}
        entries = {entry.id: entry for entry in self.entries}
        if len(documents) != len(self.documents) or len(entries) != len(self.entries):
            raise ValueError("duplicate document or evidence ID")
        for entry in self.entries:
            if isinstance(entry, QuotedEvidence):
                span = entry.source
                doc = documents.get(span.document_id)
                if doc is None:
                    raise ValueError(f"unknown source document: {span.document_id}")
                if not 0 <= span.start < span.end <= len(doc.text):
                    raise ValueError(f"invalid span bounds: {entry.id}")
                if doc.text[span.start:span.end] != span.quote:
                    raise ValueError(f"source quote mismatch: {entry.id}")
                if entry.number is not None:
                    from .numbers import unsupported_numeric_tokens
                    if unsupported_numeric_tokens(str(entry.number),span.quote):
                        raise ValueError(f"structured number absent from quote: {entry.id}")
                    if entry.unit:
                        unit=entry.unit.lower();quote=span.quote.lower()
                        if "%" in unit and "%" not in quote and "процент" not in quote:
                            raise ValueError(f"percentage unit absent from quote: {entry.id}")
                        if ("rub" in unit or "руб" in unit or "₽" in unit) and not any(x in quote for x in ("rub","руб","₽")):
                            raise ValueError(f"currency unit absent from quote: {entry.id}")
                expected_kind = "news" if entry.kind == "NEWS_FACT" else "bank"
                if doc.kind != expected_kind:
                    raise ValueError(f"wrong source type: {entry.id}")
                if entry.kind == "BANK_VIEW" and (doc.page is None or doc.as_of is None):
                    raise ValueError(f"bank source needs page and as_of: {entry.id}")
            elif isinstance(entry, CompanyFeature):
                if entry.client_id != self.client_id:
                    raise ValueError(f"feature belongs to a different client: {entry.id}")
            else:
                if any(ref not in entries for ref in entry.input_ids):
                    raise ValueError(f"unknown calculation input: {entry.id}")

        visiting, visited = set(), set()

        def visit(entry_id: str) -> None:
            if entry_id in visiting:
                raise ValueError(f"cyclic calculation provenance: {entry_id}")
            if entry_id in visited:
                return
            visiting.add(entry_id)
            entry = entries[entry_id]
            if isinstance(entry, (ComputedEvidence, UnknownEffect)):
                for ref in entry.input_ids:
                    visit(ref)
            visiting.remove(entry_id)
            visited.add(entry_id)

        for entry_id in entries:
            visit(entry_id)
        return self


class ProvenanceReport(StrictModel):
    ok: bool
    issues: list[str]
    semantic_support: Literal["not_checked"] = "not_checked"


def quoted_text(entry: QuotedEvidence, doc: Document) -> str:
    text=entry.source.quote
    if entry.number is not None:
        text+=f" {entry.number} {entry.unit or ''}"
    # A table row explicitly labels one unit for all year/value columns.
    # Project only values directly following year labels, not table/page numbers.
    if entry.unit:
        for value in re.findall(r"\b\d{4}[Пп]?\s+([+-]?\d+(?:[.,]\d+)?)",entry.source.quote):
            text+=f" {value} {entry.unit}"
    if doc.as_of:text+=f" {doc.as_of} {doc.as_of.year}"
    if doc.page:text+=f" {doc.page}"
    return text


def check_card_provenance(card: InsightCard, ledger: EvidenceLedger, *, allow_mock: bool = False) -> ProvenanceReport:
    """Check references and layer types, not whether claims follow from sources.

    Unknown client data remains unknown. The caller must run independent
    semantic criticism and numeric/sign checks before publication.
    """
    entries = {entry.id: entry for entry in ledger.entries}
    docs = {doc.id: doc for doc in ledger.documents}
    issues = []
    if card.trace_id != ledger.trace_id:
        issues.append("trace_id mismatch")

    def uses_mock(entry_id: str) -> bool:
        entry = entries[entry_id]
        if isinstance(entry, QuotedEvidence):
            return docs[entry.source.document_id].is_mock
        if isinstance(entry, (ComputedEvidence, UnknownEffect)):
            return any(uses_mock(ref) for ref in entry.input_ids)
        return False

    groups = (
        (card.confirmed_facts, "NEWS_FACT"),
        (card.bank_view_items, "BANK_VIEW"),
        (card.why_you + card.model_inference.impact_chain + card.model_inference.risks
         + card.model_inference.opportunities + card.model_inference.actions_or_questions, None),
    )
    for claims, required_kind in groups:
        for claim in claims:
            if not claim.id.strip() or not claim.text.strip():
                issues.append("empty claim ID or text")
            for ref in claim.source_ids:
                if ref not in entries:
                    issues.append(f"{claim.id}: unknown evidence {ref}")
                elif required_kind is not None and entries[ref].kind != required_kind:
                    issues.append(f"{claim.id}: expected {required_kind}, got {entries[ref].kind}")
                elif not allow_mock and uses_mock(ref):
                    issues.append(f"{claim.id}: mock evidence {ref}")
    if card.bank_view_status == "available" and not card.bank_view_items:
        issues.append("bank_view marked available without claims")
    if card.bank_view_status == "missing" and card.bank_view_items:
        issues.append("bank_view marked missing with claims")
    return ProvenanceReport(ok=not issues, issues=issues)
