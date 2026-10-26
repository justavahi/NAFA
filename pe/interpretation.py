"""Candidate interpretation contract: inspectable evidence links, no extra LLM call."""
from __future__ import annotations

from pydantic import Field,model_validator
from .schemas import Claim,Factor,ImpactClaim,PersonalOverlay,StrictModel
from .numbers import unsupported_numeric_tokens


class EvidenceStep(StrictModel):
    text: str=Field(min_length=1,max_length=350)
    source_ids: list[str]=Field(min_length=1)


class Mechanism(StrictModel):
    id: str=Field(min_length=1)
    factor: Factor
    effect: str=Field(pattern='^(negative|positive|neutral|two_branches)$')
    event: EvidenceStep
    channel: EvidenceStep
    company: EvidenceStep
    consequence: EvidenceStep
    changes_if: EvidenceStep


class DecisionAction(StrictModel):
    id: str=Field(min_length=1)
    resolves: list[str]=Field(min_length=1)
    check: EvidenceStep
    decision_use: EvidenceStep


class InterpretationPlan(StrictModel):
    why_you: list[Claim]=Field(max_length=2)
    impact_chain: list[Mechanism]=Field(min_length=1,max_length=3)
    risks: list[Claim]=Field(max_length=1)
    opportunities: list[Claim]=Field(max_length=1)
    actions_or_questions: list[DecisionAction]=Field(min_length=1,max_length=4)
    unknowns: list[str]

    @model_validator(mode='after')
    def distinct_mechanisms(self):
        ids=[item.id for item in self.impact_chain]
        all_ids=ids+[item.id for item in self.actions_or_questions]+[c.id for c in self.why_you+self.risks+self.opportunities]
        if len(set(all_ids))!=len(all_ids):raise ValueError('all claim IDs must be unique')
        if len({m.factor for m in self.impact_chain})!=len(ids):raise ValueError('one distinct mechanism per factor')
        for action in self.actions_or_questions:
            if not set(action.resolves).issubset(ids):raise ValueError('action must resolve an existing mechanism')
        conclusions=[m.consequence.text.strip().casefold() for m in self.impact_chain]
        if len(set(conclusions))!=len(conclusions):raise ValueError('duplicate consequences are not distinct mechanisms')
        return self


def steps(mechanism):
    return [mechanism.event,mechanism.channel,mechanism.company,mechanism.consequence,mechanism.changes_if]


def joined(parts):
    return dict(text=' '.join(p.text.strip() for p in parts),source_ids=list(dict.fromkeys(ref for p in parts for ref in p.source_ids)))


def to_overlay(plan: InterpretationPlan) -> PersonalOverlay:
    return PersonalOverlay(why_you=plan.why_you,
        impact_chain=[ImpactClaim(id=m.id,factor=m.factor,effect=m.effect,**joined(steps(m))) for m in plan.impact_chain],
        risks=plan.risks,opportunities=plan.opportunities,
        actions_or_questions=[Claim(id=a.id,**joined([a.check,a.decision_use])) for a in plan.actions_or_questions],unknowns=plan.unknowns)


def check_plan(case,plan: InterpretationPlan) -> list[str]:
    from .tasks import evidence_text
    entries={e.id:e for e in case.ledger.entries};issues=[]
    for mechanism in plan.impact_chain:
        if not any(entries.get(ref) is not None and entries[ref].kind=='NEWS_FACT' for ref in mechanism.event.source_ids):
            issues.append(f'{mechanism.id}: event step requires news evidence')
        if not any(entries.get(ref) is not None and entries[ref].kind=='COMPANY_FEATURE' for ref in mechanism.company.source_ids):
            issues.append(f'{mechanism.id}: company step requires company evidence')
        if case.effect_source_ids.get(mechanism.factor) not in mechanism.consequence.source_ids:
            issues.append(f'{mechanism.id}: consequence requires computed effect evidence')
    components=[(m.id,p) for m in plan.impact_chain for p in steps(m)]
    components += [(a.id,p) for a in plan.actions_or_questions for p in (a.check,a.decision_use)]
    for claim_id,part in components:
        try:
            missing=unsupported_numeric_tokens(part.text,evidence_text(case.ledger,part.source_ids))
            if missing:issues.append(f'{claim_id}: number/date/unit missing from this step evidence: {sorted(missing,key=str)}')
        except ValueError as exc:issues.append(f'{claim_id}: {exc}')
    return issues
