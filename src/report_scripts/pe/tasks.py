"""Validated role tasks, including dialogue, chart explanations and offers."""
from __future__ import annotations

from .contracts import BankViewExtraction, Chunk, GroundedExplanation, OfferText
from .evidence import ComputedEvidence, EvidenceLedger, QuotedEvidence, UnknownEffect, quoted_text
from .numbers import unsupported_numeric_tokens
from .schemas import ChatAnswer, Claim, EventExtraction, InsightCard
from .session import PromptSession


def evidence_text(ledger: EvidenceLedger, ids: list[str]) -> str:
    entries = {e.id: e for e in ledger.entries}
    docs = {d.id: d for d in ledger.documents}
    def resolve(ref):
        if ref not in entries:
            raise ValueError(f"unknown evidence: {ref}")
        entry = entries[ref]
        if isinstance(entry, QuotedEvidence):
            doc = docs[entry.source.document_id]
            return quoted_text(entry,doc)
        if isinstance(entry, (ComputedEvidence, UnknownEffect)):
            return str(entry.value) + " " + " ".join(resolve(i) for i in entry.input_ids)
        return str(entry.value)
    return " ".join(resolve(ref) for ref in ids)


def offer_gate(*, relevance_passed: bool, relevant_product: bool, active_client: bool,
               product_known: bool, product_absent_or_expandable: bool,
               sensitive_segment: bool = False, cooldown_passed: bool = True,
               intro_for_prospect: bool = False) -> dict:
    conditions = dict(relevance_passed=relevance_passed, relevant_product=relevant_product,
                      client_condition=(product_known and product_absent_or_expandable) if active_client else intro_for_prospect,
                      not_sensitive=not sensitive_segment, cooldown_passed=cooldown_passed)
    return dict(allowed=all(conditions.values()), checks=conditions)


def validate_task_output(prompt_id: str, output, payload: dict, *, images=None) -> None:
    if isinstance(output, EventExtraction):
        document = payload["document"]
        for fact in output.facts:
            if fact.source_id != document["id"] or fact.source_span not in document["text"]:
                raise ValueError("extraction contains an invented source span")
            if unsupported_numeric_tokens(fact.value, fact.source_span):
                raise ValueError("extraction contains an unsupported numeric value")
    if isinstance(output, Chunk):
        if (output.doc_id, output.page) != (payload["doc_id"], payload["page"]):
            raise ValueError("page identity mismatch")
        if not images and (output.blocks or not output.needs_manual_check):
            raise ValueError("VLM cannot invent a page without an image")
    if isinstance(output, BankViewExtraction):
        for item in output.items:
            chunks = [c for c in payload["chunks"] if c["doc_id"] == item.doc_id and c["page"] == item.page]
            if not any(item.quote_span in block["text"] for c in chunks for block in c["blocks"]):
                raise ValueError("BankView quote not found on the cited page")
            if not any(c.get("as_of_date")==item.as_of.isoformat() for c in chunks):
                raise ValueError("BankView date differs from the cited document")
            if unsupported_numeric_tokens(item.value, item.quote_span):
                raise ValueError("BankView value absent from quote")
    if isinstance(output, (GroundedExplanation, ChatAnswer, OfferText)):
        ledger = EvidenceLedger.model_validate(payload["ledger"])
        entries = {e.id: e for e in ledger.entries}
        if isinstance(output, GroundedExplanation):
            for claim in output.claims:
                text = evidence_text(ledger, claim.source_ids)
                missing=unsupported_numeric_tokens(claim.text, text)
                if missing:
                    raise ValueError(f"{claim.id}: unsupported number, date or unit: {sorted(missing,key=str)}")
                if claim.layer == "bank_view" and any(entries[ref].kind != "BANK_VIEW" for ref in claim.source_ids):
                    raise ValueError("bank attribution requires only bank evidence")
            if prompt_id == "PRM-10" and "two_branches" in str(payload["computed"]) and not output.questions:
                raise ValueError("unknown effect requires a clarifying question")
        elif isinstance(output, ChatAnswer):
            text = evidence_text(ledger, output.citations)
            if unsupported_numeric_tokens(output.answer, text):
                raise ValueError("dialogue introduced unsupported numeric data")
            if not output.out_of_scope and not output.claims:
                raise ValueError("in-scope dialogue requires atomic grounded claims")
            for claim in output.claims:
                evidence_text(ledger, claim.source_ids)
                if not set(claim.source_ids).issubset(output.citations):
                    raise ValueError("dialogue claim missing from citations")
                if claim.text not in output.answer:
                    raise ValueError("answer must preserve the checked claim text")
        else:
            if output.shown and not payload["context"].get("offer_gate", {}).get("allowed"):
                raise ValueError("offer not permitted by host rule")
            if output.shown:
                text = evidence_text(ledger, output.source_ids)
                if unsupported_numeric_tokens(output.text, text):
                    raise ValueError("offer invented a price or numeric condition")


def run_task(prompt_id: str, payload: dict, gateway, *, session=None, images=None, overrides=None,
             _critic_repair=False, _feedback=None) -> dict:
    trace_id = payload.get("ledger", {}).get("trace_id", "task_" + prompt_id)
    session = session or PromptSession(gateway, trace_id, max_calls=4 if prompt_id=="PRM-08" else 2, overrides=overrides)
    feedback=_feedback
    raw_outputs=[];repairs=[]
    for attempt in range(2):
        output=session.call(prompt_id,payload,images=images,**({"feedback":feedback} if feedback else {}))
        raw_output=output.model_dump(mode="json")
        raw_outputs.append(raw_output)
        try:
            validate_task_output(prompt_id,output,payload,images=images)
            break
        except ValueError as exc:
            if attempt or len(session.records)>=session.max_calls:raise
            ids=[entry['id'] for entry in payload.get('ledger',{}).get('entries',[])]
            feedback=(str(exc)+'. Допустимые source_ids: '+str(ids)+
                '. Проверь ВСЕ ссылки сразу: ID документа, question, context и computed не являются ID evidence. '+
                'Каждое число должно подтверждаться source_ids именно своего claim; добавь правильный источник или убери число. '+
                'Оставь максимум четыре кратких утверждения по вопросу, без пересказа остальных данных.')
            repairs.append(dict(kind="validation",issue=str(exc)))
    issues=[]
    if isinstance(output,ChatAnswer) and output.out_of_scope and not output.claims:
        output.answer="В переданных данных недостаточно сведений для ответа. Нужны дополнительные данные или источник."
        output.layers=[];output.citations=[]
        output.follow_up_suggestions=["Какие данные или источник нужно добавить к вопросу?"]
    if isinstance(output,ChatAnswer) and output.claims:
        output.answer=" ".join(claim.text for claim in output.claims)
        from .pipeline import check_critic
        old=InsightCard.model_validate(payload["card"])
        temp=old.model_copy(deep=True)
        temp.confirmed_facts=[Claim(id=c.id,text=c.text,source_ids=c.source_ids) for c in output.claims if c.layer=="fact"]
        temp.bank_view_items=[Claim(id=c.id,text=c.text,source_ids=c.source_ids) for c in output.claims if c.layer=="bank_view"]
        temp.why_you=[]
        temp.model_inference.impact_chain=[Claim(id=c.id,text=c.text,source_ids=c.source_ids) for c in output.claims if c.layer=="ai_inference"]
        temp.model_inference.risks=[];temp.model_inference.opportunities=[];temp.model_inference.actions_or_questions=[]
        temp.provenance_labels={c.id:c.layer for c in output.claims}
        critic=session.call("PRM-07",dict(card=temp.model_dump(mode="json"),impacts=[],ledger=payload["ledger"],
            computed=payload["computed"],scope="dialogue",question=payload["question"]))
        issues=check_critic(temp,critic)
        if issues and not _critic_repair and len(session.records)+2<=session.max_calls:
            repaired=run_task(prompt_id,payload,gateway,session=session,images=images,
                _critic_repair=True,_feedback="Замечания независимого критика: "+str(issues)+
                ". Верни только подтверждённый ответ по вопросу. При недостатке данных out_of_scope=true, claims=[], citations=[].")
            repaired["raw_outputs"]=raw_outputs+repaired["raw_outputs"]
            repaired["repairs"]=repairs+[dict(kind="critic",issues=issues)]+repaired["repairs"]
            return repaired
    return dict(output=output.model_dump(mode="json"), raw_output=raw_output,raw_outputs=raw_outputs,
                repairs=repairs,calls=session.records,
                requests=session.requests, mode=gateway.mode,issues=issues,
                publishable=gateway.mode=="live" and prompt_id=="PRM-08" and not issues,
                status="needs_fix" if issues else "checked")
