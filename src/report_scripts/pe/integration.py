"""Pure adapters for BA card shape, Backend payloads and DA quality traces."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .evidence import CompanyFeature, ComputedEvidence, EvidenceLedger, QuotedEvidence, UnknownEffect
from .pipeline import CaseInput, card_claims
from .schemas import InsightCard
from .tasks import evidence_text


def canonical_card(card: InsightCard, ledger: EvidenceLedger, *, document_ids: dict[str,str] | None = None) -> dict:
    entries = {e.id: e for e in ledger.entries}
    docs = {d.id: d for d in ledger.documents}
    bank_items = []
    for claim in card.bank_view_items:
        for ref in claim.source_ids:
            evidence = entries[ref]
            if not isinstance(evidence, QuotedEvidence) or evidence.kind != "BANK_VIEW":
                raise ValueError("bank claim cannot be mapped without bank provenance")
            doc = docs[evidence.source.document_id]
            known=re.match(r"^(DOC-\d+)(?:_p\d+_\d+)?$",doc.id)
            original=(document_ids or {}).get(doc.id,known[1] if known else doc.id)
            bank_items.append(dict(id=claim.id, claim=claim.text, doc_id=original, page=doc.page,
                                   as_of=str(doc.as_of), quote_span=evidence.source.quote,
                                   source_ids=claim.source_ids, stale=card.bank_view_status == "outdated"))
    return dict(confirmed_facts=[c.model_dump() | {"certainty": "confirmed"} for c in card.confirmed_facts],
                bank_view=dict(status=card.bank_view_status, items=bank_items),
                model_inference=card.model_inference.model_dump(),
                why_you=[c.model_dump() for c in card.why_you],
                provenance_labels=card.provenance_labels, relevance=card.relevance,
                offer=dict(shown=card.offer is not None, text=card.offer),
                disclaimer=card.disclaimer, trace_id=card.trace_id)


def backend_payload(card: InsightCard, case: CaseInput, *, card_id: str | None = None, document_ids: dict[str,str] | None = None) -> dict:
    result = canonical_card(card, case.ledger,document_ids=document_ids)
    fact_docs = [d.model_dump(mode="json") for d in case.ledger.documents if d.kind == "news"]
    result.update(card_id=card_id, persona=case.persona, client_pseudonym=case.ledger.client_id,
                  score=dict(relevance=card.relevance),
                  layers=dict(fact=dict(label="Факт", sources=fact_docs),
                              bank_position=dict(label="Позиция Альфа-Банка", **result["bank_view"]),
                              ai_conclusion=dict(label="Вывод AI", confidence=card.model_inference.confidence,
                                                 method="PE prompts + independent critic")),
                  sections=dict(what_happened=[c.text for c in card.confirmed_facts],
                                why_you=[c.text for c in card.why_you],
                                possible_implications=[c.text for c in card.model_inference.impact_chain],
                                actions_to_check=[c.text for c in card.model_inference.actions_or_questions],
                                bank_position=result["bank_view"], basis=dict(trace_id=card.trace_id)))
    return result


def quality_trace(card: InsightCard, ledger: EvidenceLedger, *, synthetic: bool = False) -> dict:
    """Keep synthetic/mock/computed origin explicit; never convert it to external fact."""
    docs = {d.id: d for d in ledger.documents}
    entries = {e.id: e for e in ledger.entries}
    def mock(ref):
        entry = entries[ref]
        if isinstance(entry, QuotedEvidence): return docs[entry.source.document_id].is_mock
        if isinstance(entry, (ComputedEvidence, UnknownEffect)): return any(mock(i) for i in entry.input_ids)
        return False
    sources = [dict(id=e.id, text=evidence_text(ledger, [e.id]), is_mock=mock(e.id),
                    origin=e.kind, synthetic=synthetic,
                    formula_id=e.formula_id if isinstance(e, ComputedEvidence) else None)
               for e in ledger.entries]
    return dict(trace_id=card.trace_id, synthetic=synthetic,
                insight=dict(claims=[c.model_dump() | {"layer": layer} for c, layer in card_claims(card)],
                             sources=sources))


DA_FIELDS = {
    "is_large": "F-01", "share_okved": "F-02", "pctl_industry": "F-02",
    "sector_profile": "F-03", "cl_product_loa_mark": "F-08", "cl_product_dep_mark": "F-08",
    "loan_flag": "F-08", "dep_flag": "F-08", "fts_flag": "F-09", "client_type": "F-10",
    "completeness": "F-13", "rate_net": "E-15", "hhi_out": "G-03", "hhi_in": "G-03",
}


def load_da_features(directory: Path, source_client_id: str, client_alias: str) -> list[CompanyFeature]:
    """Read only allowlisted aggregate columns; never send physical-person fields."""
    output = []
    for filename in ("client_features.csv", "payment_features.csv", "exposures.csv"):
        path = directory / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(4096); stream.seek(0)
            try: dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error: dialect = csv.excel
            rows = csv.DictReader(stream, dialect=dialect)
            selected = [row for row in rows if any(row.get(key) == source_client_id
                        for key in ("client_id", "SYNTHETIC_U", "synthetic_u"))]
        if len(selected) > 1:
            raise ValueError(f"duplicate client rows in {filename}")
        if not selected:
            continue
        for column, raw in selected[0].items():
            lower = column.lower()
            metric = DA_FIELDS.get(lower)
            conventional=re.match(r"^([fge])(\d{2})_",lower)
            if conventional:
                group,number=conventional.groups()
                upper_limit={"f":13,"g":10,"e":15}[group]
                if 1<=int(number)<=upper_limit:
                    metric=group.upper()+"-"+number
            if lower.startswith("eps_") and lower[4:].upper() in {"RATE", "FX", "INFL", "ENERGY", "COMMOD", "DEMAND", "LABOR", "CREDIT", "BUDGET", "REG", "GEOPOL", "CPTY"}:
                metric = "E-14"
            if metric is None:
                continue
            value = None if raw is None or raw.strip().lower() in {"", "null", "nan", "none"} else raw
            output.append(CompanyFeature(id=f"DA_{len(output)}", kind="COMPANY_FEATURE", client_id=client_alias,
                                          feature_id=metric, value=value,
                                          source_record_id=f"{filename}:{client_alias}:{column}"))
    if not output:
        raise ValueError("no allowlisted DA aggregates found for this client")
    return output
