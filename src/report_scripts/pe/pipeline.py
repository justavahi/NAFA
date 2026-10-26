"""First vertical slice: EventCore -> PersonalOverlay -> independent critic.

Upstream supplies extraction and computed DA values. The injected gateway uses
the Backend LLMGateway.chat interface; this module has no HTTP implementation.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .evidence import CompanyFeature, ComputedEvidence, EvidenceLedger, QuotedEvidence, UnknownEffect, check_card_provenance, quoted_text
from .prompts import prepare_request
from .schemas import Claim, CriticVerdict, EventCore, EventExtraction, Factor, Inference, InsightCard, PersonalOverlay, SCHEMAS, StrictModel
from .session import PromptSession
from .numbers import unsupported_numeric_tokens

QUESTIONS = {"what_happened", "why_you", "implications", "actions", "bank_view", "basis"}
DISCLAIMER = "Не является индивидуальной инвестиционной рекомендацией"
EFFECTS = {"negative", "positive", "neutral", "two_branches"}


class CaseInput(StrictModel):
    ledger: EvidenceLedger
    event: EventExtraction
    persona: str = Field(min_length=1)
    bank_view_status: Literal["available", "missing", "outdated", "conflict"]
    relevance_id: str
    confidence_id: str
    effect_source_ids: dict[Factor, str]

    @model_validator(mode="after")
    def validate_computed(self):
        entries = {entry.id: entry for entry in self.ledger.entries}
        for ref, formula in ((self.relevance_id, "FM-06"), (self.confidence_id, "FM-20")):
            entry = entries.get(ref)
            if not isinstance(entry, ComputedEvidence) or entry.formula_id != formula:
                raise ValueError(f"{ref}: requires computed {formula}")
            if type(entry.value) not in (int, float) or not 0 <= entry.value <= 1:
                raise ValueError(f"{ref}: requires numeric value in [0,1]")
        for factor, ref in self.effect_source_ids.items():
            entry = entries.get(ref)
            if not isinstance(entry, (ComputedEvidence, UnknownEffect)) or entry.value not in EFFECTS:
                raise ValueError(f"{factor}: requires computed effect sign")
            if isinstance(entry, UnknownEffect) and entry.factor != factor:
                raise ValueError("unknown effect belongs to another factor")
            if factor not in self.event.iota:
                raise ValueError(f"{factor}: effect without extracted event factor")
        docs = {doc.id: doc for doc in self.ledger.documents}
        for fact in self.event.facts:
            if fact.source_id not in docs or fact.source_span not in docs[fact.source_id].text:
                raise ValueError("extracted fact has no matching document span")
        return self

    def computed(self) -> dict:
        entries = {entry.id: entry for entry in self.ledger.entries}
        return dict(relevance=entries[self.relevance_id].value,
                    confidence=entries[self.confidence_id].value,
                    effect_signs={factor: entries[ref].value for factor, ref in self.effect_source_ids.items()},
                    effect_source_ids=self.effect_source_ids)


def card_claims(card: InsightCard) -> list[tuple[Claim, str]]:
    return ([(c, "fact") for c in card.confirmed_facts]
            + [(c, "bank_view") for c in card.bank_view_items]
            + [(c, "ai_inference") for c in card.why_you + card.model_inference.impact_chain
               + card.model_inference.risks + card.model_inference.opportunities
               + card.model_inference.actions_or_questions])


def assemble_card(case: CaseInput, core: EventCore, overlay: PersonalOverlay) -> InsightCard:
    inference = Inference(
        impact_chain=[Claim(**impact.model_dump(exclude={"factor", "effect"})) for impact in overlay.impact_chain],
        risks=overlay.risks, opportunities=overlay.opportunities,
        actions_or_questions=overlay.actions_or_questions, confidence=case.computed()["confidence"])
    labels = {c.id: "fact" for c in core.confirmed_facts}
    labels.update({c.id: "bank_view" for c in core.bank_view_items})
    labels.update({c.id: "ai_inference" for c in overlay.why_you + inference.impact_chain
                   + inference.risks + inference.opportunities + inference.actions_or_questions})
    return InsightCard(
        confirmed_facts=core.confirmed_facts, why_you=overlay.why_you,
        bank_view_status=case.bank_view_status, bank_view_items=core.bank_view_items,
        model_inference=inference, provenance_labels=labels,
        relevance=case.computed()["relevance"], offer=None, disclaimer=DISCLAIMER,
        trace_id=case.ledger.trace_id)


def check_overlay(case: CaseInput, overlay: PersonalOverlay) -> list[str]:
    issues, signs = [], case.computed()["effect_signs"]
    features = {e.id for e in case.ledger.entries if isinstance(e, CompanyFeature)}
    if not overlay.impact_chain or not overlay.why_you:
        issues.append("no personalised impact or explanation")
    for claim in overlay.why_you:
        if not features.intersection(claim.source_ids):
            issues.append(f"{claim.id}: no known company feature")
    for impact in overlay.impact_chain:
        if signs.get(impact.factor) != impact.effect:
            issues.append(f"{impact.id}: effect differs from computed sign")
        if case.effect_source_ids.get(impact.factor) not in impact.source_ids:
            issues.append(f"{impact.id}: missing effect calculation reference")
    return issues


def check_numeric_tokens(card: InsightCard, ledger: EvidenceLedger) -> list[str]:
    """Check decimal token presence in cited evidence only; critic checks meaning/units.

    Converts decimal comma, but intentionally does not infer unit conversions.
    A model must copy numeric notation from the context rather than compute it.
    """
    entries = {entry.id: entry for entry in ledger.entries}
    def text_for(ref: str) -> str:
        entry = entries.get(ref)
        if isinstance(entry, QuotedEvidence):
            doc = next(d for d in ledger.documents if d.id == entry.source.document_id)
            # Page and document date are valid context when attributing bank view.
            return quoted_text(entry,doc)
        if isinstance(entry, (ComputedEvidence, UnknownEffect)):
            return str(entry.value) + " " + " ".join(text_for(r) for r in entry.input_ids)
        return str(entry.value) if isinstance(entry, CompanyFeature) else ""

    issues = []
    for claim, _ in card_claims(card):
        allowed = " ".join(text_for(ref) for ref in claim.source_ids)
        missing=unsupported_numeric_tokens(claim.text,allowed)
        if missing:
            issues.append(f"{claim.id}: numeric token absent from cited evidence: {sorted(missing,key=str)}")
    return issues


def check_critic(card: InsightCard, critic: CriticVerdict) -> list[str]:
    issues = []
    expected = {c.id: (c, layer) for c, layer in card_claims(card)}
    actual = {c.id: c for c in critic.claims}
    if not expected or set(actual) != set(expected) or len(actual) != len(critic.claims):
        issues.append("critic did not cover exactly all card claims")
    for claim_id, result in actual.items():
        if claim_id not in expected:
            continue
        claim, layer = expected[claim_id]
        if result.text != claim.text or result.layer != layer:
            issues.append(f"{claim_id}: critic checked different text/layer")
        if result.verdict != "supported" or not result.evidence_ids:
            issues.append(f"{claim_id}: critic did not support claim")
        elif not set(result.evidence_ids).issubset(claim.source_ids):
            issues.append(f"{claim_id}: critic cited unreferenced evidence")
    if (set(critic.coverage_6_questions) != QUESTIONS
            or not all(critic.coverage_6_questions.values())):
        issues.append("critic did not confirm all six questions")
    if critic.overall != "pass" or critic.numbers_check or critic.fix_instructions:
        issues.append("critic requested corrections or further search")
    return issues


def run_case(case: CaseInput, gateway, *, max_repairs: int = 1, overrides: dict | None = None, retrieve=None, manifest: dict | None = None, selected_news_ids: list[str] | None = None) -> dict:
    """Generate, check and repair once; evaluation with synthetic inputs never publishes.

    retrieve, when supplied by the host, accepts (case, fix_instructions) and
    returns a validated CaseInput. Models cannot select arbitrary tools or URLs.
    """
    if max_repairs not in (0, 1):
        raise ValueError("max_repairs must be 0 or 1")
    from .context import role_card, fragment_news, check_fact_focus, focused_generation_view
    context = (manifest or {}).get("personalization", {})
    use_role, use_focus = context.get("role_card", False), context.get("fragment_focus", False)
    use_view = context.get("evidence_view") == "quoted"
    if use_view and not use_focus:
        raise ValueError("quoted evidence view requires fragment focus")
    role = role_card(case.persona).model_dump(mode="json") if use_role else None
    if selected_news_ids is not None and not use_focus:
        raise ValueError("selected news requires a fragment-focus profile")
    if use_role or use_focus:
        expected_input = "FocusedPersonalInput" if use_view else "ContextPersonalInput"
        if manifest["prompts"]["PRM-06"]["input_schema"] != expected_input:
            raise ValueError(f"context profile requires {expected_input}")
    if use_focus:
        if retrieve is not None:
            raise ValueError("focused retrieval requires a new reviewed selection; run a separate case")
        if manifest["prompts"]["PRM-05"]["input_schema"] != "ContextCoreInput":
            raise ValueError("focus profile requires ContextCoreInput")
        if not selected_news_ids or len(set(selected_news_ids)) != len(selected_news_ids):
            raise ValueError("nonempty unique reviewed news selection required")
        atomic = {(f.source.document_id, f.source.start, f.source.end, f.source.quote)
                  for d in case.ledger.documents if d.kind == "news" for f in fragment_news(d)}
        entries = {e.id: e for e in case.ledger.entries}
        for ref in selected_news_ids:
            e = entries.get(ref)
            if not isinstance(e, QuotedEvidence) or e.kind != "NEWS_FACT" or (
                e.source.document_id, e.source.start, e.source.end, e.source.quote) not in atomic:
                raise ValueError(f"{ref}: selection requires an exact atomic news span")
    mode = gateway.mode
    if mode not in {"live", "replay", "evaluation"}:
        raise ValueError("gateway mode must be live, evaluation or replay")
    session = PromptSession(gateway, case.ledger.trace_id, max_calls=8, overrides=overrides,manifest=manifest)
    result = dict(mode=mode, trace_id=case.ledger.trace_id, publishable=False,
                  status="needs_fix", calls=session.records, issues=[], requests=session.requests,
                  unknowns=[], card=None, critic=None, repairs=0, check_history=[],discarded_claims=[])
    if use_role or use_focus:
        result["context_ablation"] = dict(role_card=role, selected_news_ids=selected_news_ids,
                                         semantic_selection_verified=False)
    if mode == "live" and any(doc.is_mock for doc in case.ledger.documents):
        result["issues"].append("live run cannot use mock documents")
        return result

    try:
        core = None
        instructions = []
        for cycle in range(max_repairs + 1):
            result["repairs"] = cycle
            if core is None:
                core_payload = dict(event=case.event.model_dump(mode="json"),
                    entries=[e.model_dump(mode="json") for e in case.ledger.entries if isinstance(e, QuotedEvidence)
                             and (not use_focus or e.kind == "BANK_VIEW" or e.id in selected_news_ids)],
                    documents=[d.model_dump(mode="json") for d in case.ledger.documents],
                    bank_view_status=case.bank_view_status, repair_instructions=instructions)
                if use_focus:
                    core_payload["selected_news_ids"] = selected_news_ids
                if use_view:
                    core_payload["documents"] = focused_generation_view(case.ledger, set(selected_news_ids))["documents"]
                core = session.call("PRM-05", core_payload)
            core_for_overlay = core.model_copy(deep=True)
            if instructions:
                core_for_overlay.unknowns += ["Замечания проверки для исправления: " + "; ".join(instructions)]
            personal_payload = dict(event_core=core_for_overlay.model_dump(mode="json"),
                ledger=case.ledger.model_dump(mode="json"), persona=case.persona, computed=case.computed())
            if use_role or use_focus:
                personal_payload.update(role_card=role, selected_news_ids=selected_news_ids)
            if use_view:
                personal_payload.pop("ledger")
                personal_payload["evidence_view"] = focused_generation_view(case.ledger, set(selected_news_ids))
            overlay = session.call("PRM-06", personal_payload)
            from .interpretation import InterpretationPlan,check_plan,to_overlay
            plan_issues=[]
            if isinstance(overlay,InterpretationPlan):
                result['interpretation_plan']=overlay.model_dump(mode='json')
                plan_issues=check_plan(case,overlay)
                overlay=to_overlay(overlay)
            feature_ids={e.id for e in case.ledger.entries if isinstance(e,CompanyFeature)}
            extras=[c for c in overlay.impact_chain if c.factor not in case.effect_source_ids]
            generic=[c for c in overlay.why_you if not feature_ids.intersection(c.source_ids)]
            result["discarded_claims"] += [dict(id=c.id,reason="factor_not_computed") for c in extras]
            result["discarded_claims"] += [dict(id=c.id,reason="not_personalised") for c in generic]
            overlay.impact_chain=[c for c in overlay.impact_chain if c not in extras]
            overlay.why_you=[c for c in overlay.why_you if c not in generic]
            if extras:overlay.unknowns.append("Для дополнительных каналов влияния нет расчётов в переданных данных.")
            result["unknowns"] = list(dict.fromkeys(core.unknowns + overlay.unknowns))
            card = assemble_card(case, core, overlay)
            result["card"] = card.model_dump(mode="json")
            result["impacts"] = [item.model_dump(mode="json") for item in overlay.impact_chain]
            issues = check_card_provenance(card, case.ledger, allow_mock=mode != "live").issues
            issues += plan_issues + check_overlay(case, overlay) + check_numeric_tokens(card, case.ledger)
            if use_focus:
                issues += check_fact_focus(card, case.ledger, set(selected_news_ids))
            critic = None
            if not issues:
                critic = session.call("PRM-07", dict(card=card.model_dump(mode="json"), impacts=result["impacts"],
                    ledger=case.ledger.model_dump(mode="json"), computed=case.computed()))
                result["critic"] = critic.model_dump(mode="json")
                issues += check_critic(card, critic)
            result["check_history"].append(dict(cycle=cycle, issues=list(issues)))
            result["issues"] = issues
            if not issues:
                result["publishable"] = mode == "live"
                result["partial"]=bool(result["discarded_claims"])
                result["status"] = {"live": "verified", "replay": "offline_replay", "evaluation": "evaluated"}[mode]
                break
            instructions = issues + (critic.fix_instructions if critic else [])
            core_ids = {c.id for c in core.confirmed_facts + core.bank_view_items}
            if any(any(issue.startswith(cid + ":") for cid in core_ids) for issue in issues) or (
                    critic and any(c.layer in {"fact", "bank_view"} and c.verdict != "supported" for c in critic.claims)):
                core = None
            if critic and critic.overall == "retry_search" and retrieve and cycle < max_repairs:
                replacement = retrieve(case, instructions)
                case = CaseInput.model_validate(replacement)
                if case.ledger.trace_id != result["trace_id"]:
                    raise ValueError("retrieval cannot change the trace identity")
                core = None
    except ValidationError as exc:
        result["issues"].append("schema validation failed: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))
    except (ValueError, RuntimeError) as exc:
        result["issues"].append(str(exc))
    return result
