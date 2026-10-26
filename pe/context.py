"""Explicit role context and source-span tools for controlled ablations.

Segmentation is syntactic, not a fact/forecast classifier. It must be reviewed
before fragments replace evidence used by DA calculations.
"""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from pydantic import Field

from .evidence import Document, EvidenceLedger, Span, QuotedEvidence
from .schemas import Factor, StrictModel


class RoleCard(StrictModel):
    persona: str
    name: str
    jtbd: str
    key_topics: str
    horizon: str
    weights: dict[Factor, float] = Field(min_length=12, max_length=12)
    reference_sha256: str


def role_card(persona: str) -> RoleCard:
    path = Path(__file__).resolve().parent.parent / "reference/persona_weights.csv"
    raw = path.read_bytes()
    rows = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    for row in rows:
        if row["persona"] == persona:
            keys = ("name", "jtbd", "key_topics", "horizon")
            return RoleCard(persona=persona, **{k: row[k] for k in keys},
                weights={k: float(v) for k, v in row.items() if k not in (*keys, "persona")},
                reference_sha256=hashlib.sha256(raw).hexdigest())
    raise ValueError(f"unknown persona: {persona}")


def fragment_news(document: Document) -> list[QuotedEvidence]:
    """Exact sentence/line spans, retaining decimals and common abbreviations.

    A table row remains one line. Complex abbreviations and cross-sentence
    attribution need manual review; no fragment is automatically a verified fact.
    """
    if document.kind != "news":
        raise ValueError("news document required; bank extraction stays separate")
    text = document.text
    cuts = [0]
    abbreviations = {"г", "гг", "млн", "млрд", "руб", "т", "д", "п", "им", "др"}
    for match in re.finditer(r"\n+|[.!?](?=\s|$)", text):
        if match.group() == ".":
            word = re.search(r"([\w]+)$", text[:match.start()])
            if word and word.group(1).lower() in abbreviations:
                continue
        cuts.append(match.end())
    cuts.append(len(text))
    result = []
    for start, end in zip(cuts, cuts[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        if start == end:
            continue
        quote = text[start:end]
        identity = f"{document.id}\0{hashlib.sha256(text.encode()).hexdigest()}\0{start}\0{end}"
        result.append(QuotedEvidence(id="FR_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            kind="NEWS_FACT", source=Span(document_id=document.id, start=start, end=end, quote=quote)))
    return result


def check_fact_focus(card, ledger, selected_ids: set[str]) -> list[str]:
    """Reference connectivity only; the independent critic still checks meaning.

    Deliberately require a direct NEWS reference in why_you/impact_chain. A broad
    computed dependency does not prove that a particular sentence was used.
    """
    entries = {e.id: e for e in ledger.entries}
    used = {ref for c in card.why_you + card.model_inference.impact_chain for ref in c.source_ids}
    issues = []
    for claim in (card.why_you + card.model_inference.impact_chain + card.model_inference.risks
                  + card.model_inference.opportunities + card.model_inference.actions_or_questions):
        if any(isinstance(entries.get(ref), QuotedEvidence) and entries[ref].kind == "NEWS_FACT"
               and ref not in selected_ids for ref in claim.source_ids):
            issues.append(f"{claim.id}: inference cites news outside selected fragments")
    for claim in card.confirmed_facts:
        refs = {r for r in claim.source_ids if isinstance(entries.get(r), QuotedEvidence)
                and entries[r].kind == "NEWS_FACT"}
        if not refs or not refs <= selected_ids:
            issues.append(f"{claim.id}: fact cites news outside selected fragments")
        if refs - used:
            issues.append(f"{claim.id}: orphan news fragment; cite it directly in why_you or impact_chain, or omit fact")
    return issues


def attach_news_fragments(ledger: EvidenceLedger) -> EvidenceLedger:
    """Add atomic references without rewriting existing DA input dependencies.

    Original quotations/documents stay available to check attribution and context.
    This is a new ledger; nothing is persisted or marked human-reviewed here.
    """
    result = ledger.model_dump(mode="json")
    existing = {e.id: e for e in ledger.entries}
    for doc in ledger.documents:
        if doc.kind != "news":
            continue
        for fragment in fragment_news(doc):
            if fragment.id in existing:
                if existing[fragment.id] != fragment:
                    raise ValueError("fragment ID collision")
            else:
                result["entries"].append(fragment.model_dump(mode="json"))
                existing[fragment.id] = fragment
    return EvidenceLedger.model_validate(result)


def focused_generation_view(ledger: EvidenceLedger, selected_ids: set[str]) -> dict:
    """A view for generation only; full immutable evidence stays with the critic.

    Derived values retain their IDs, formula and value. Their source closure is
    deliberately not expanded here, so a coarse parent cannot reintroduce an
    unrelated paragraph. This view must never replace the provenance ledger.
    """
    from .evidence import CompanyFeature, ComputedEvidence
    docs = {d.id: d for d in ledger.documents}
    entries, spans = [], {}
    for entry in ledger.entries:
        if isinstance(entry, QuotedEvidence):
            if entry.kind == "NEWS_FACT" and entry.id not in selected_ids:
                continue
            entries.append(entry.model_dump(mode="json"))
            spans.setdefault(entry.source.document_id, []).append(entry.source)
        elif isinstance(entry, CompanyFeature):
            entries.append(entry.model_dump(mode="json"))
        elif isinstance(entry, ComputedEvidence):
            entries.append({**entry.model_dump(mode="json", exclude={"input_ids"}),
                            "derivation_in_full_ledger": True})
    documents = []
    for did, quotes in spans.items():
        doc = docs[did]
        # Retain original offsets and only the quoted content. Metadata remains.
        text = [" "] * len(doc.text)
        for span in quotes:
            text[span.start:span.end] = span.quote
        documents.append({**doc.model_dump(mode="json"), "text": "".join(text)})
    return dict(entries=entries, documents=documents,
                limitation="Generation view only. Full derivation/source context is checked independently; do not infer facts from hidden calculation inputs.")
