"""Deterministic, traceable card orchestration baseline.

The pipeline deliberately labels heuristic outputs provisional. It does not claim an LLM critic
or a DA-owned FM-06..FM-13 implementation ran when that dependency is not configured.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import bank_view as bv
from ..config import ROOT
from ..ingest.clients import get_client_record, pseudonym
from ..search import search_alfa_analytics, search_news
from ..util import iso, new_id, now_utc, parse_iso_utc
from .tools import compute_scenario, write_trace

PERSONA_ALIASES = {
    "owner": "PER-01", "собственник": "PER-01", "ceo": "PER-02", "генеральный директор": "PER-02",
    "cfo": "PER-03", "финансовый директор": "PER-03", "treasury": "PER-04", "казначей": "PER-04",
    "accountant": "PER-05", "главный бухгалтер": "PER-05", "operations": "PER-06", "c-level": "PER-06",
}
FACTOR_TERMS = {
    "RATE": ["ключевую ставку", "ключевая ставка", "ставку", "ставка", "ruonia", "облигац", "офз", "стоимость долга", "процентн"],
    "FX": ["курс", "валют", "доллар", "юан", "евро", "usd", "cny", "eur", "вэд"],
    "INFL": ["инфляц", "ипц", "потребительск цены", "индексац"],
    "ENERGY": ["топлив", "бензин", "дизел", "энерги", "логистик", "перевозк"],
    "COMMOD": ["золот", "сталь", "металл", "нефт", "сырь", "зерн", "удобр"],
    "DEMAND": ["спрос", "розничн", "продаж", "потреблен", "автомобил"],
    "LABOR": ["зарплат", "безработиц", "ваканси", "рынок труда", "кадр"],
    "CREDIT": ["кредит", "рефинанс", "заем", "заём", "плавающ ставк", "кредитован"],
    "BUDGET": ["бюджет", "минфин", "заимствован", "аукцион", "госзаказ", "офз"],
    "REG": ["регулятор", "налог", "регулирован", "требован", "закон"],
    "GEOPOL": ["санкц", "геополит", "фрс", "федеральн резерв", "торгов ограничен"],
    "CPTY": ["контрагент", "поставщик", "покупател", "цепочк поставок"],
}
FACTOR_NAMES = {
    "RATE": "стоимость денег и долгового финансирования", "FX": "валютный курс и ВЭД",
    "INFL": "инфляция затрат и цен", "ENERGY": "топливо, энергия и логистика",
    "COMMOD": "цены сырья и товаров", "DEMAND": "потребительский и деловой спрос",
    "LABOR": "рынок труда", "CREDIT": "доступность кредитования", "BUDGET": "бюджет и госзаказ",
    "REG": "регуляторные изменения", "GEOPOL": "внешняя среда и геополитика", "CPTY": "контрагенты и цепочки поставок",
}


def _read_csv(name: str) -> list[dict[str, str]]:
    path = ROOT / "data" / "ref" / name
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _persona_code(persona: str | None) -> str:
    if not persona:
        return "PER-03"  # documented default persona: financial director
    return PERSONA_ALIASES.get(persona.strip().casefold(), persona if persona.startswith("PER-") else "PER-03")


def _as_utc(value: str | None) -> datetime:
    if not value:
        return now_utc()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("invalid ISO as_of/publication date") from exc


def _event_factors(text: str) -> dict[str, float]:
    t = text.casefold().replace("ё", "е")
    found = {code: 1.0 for code, terms in FACTOR_TERMS.items() if any(term in t for term in terms)}
    # If a clear primary factor exists, some adjacent topics are supporting, not primary.
    if "RATE" in found:
        found.setdefault("CREDIT", 0.5)
        found.setdefault("INFL", 0.5)
    elif "INFL" in found:
        found.setdefault("RATE", 0.5)
        found.setdefault("ENERGY", 0.5)
    elif "FX" in found:
        found.setdefault("GEOPOL", 0.5)
    return found


def _client_exposure(conn: sqlite3.Connection, client_id: str, factors: dict[str, float]) -> tuple[dict[str, float], dict]:
    row = conn.execute("SELECT mainokved_code, revenue_company, cl_product_loa_mark, cl_product_lea_mark, factoring_mark,\
                               fts_flag, fts_country_name, region, city FROM clients WHERE synthetic_u=?", (client_id,)).fetchone()
    if not row:
        raise KeyError("client not found")
    sector_map = {r["okved_division"].zfill(2): r["sector_profile"] for r in _read_csv("okved_to_sector_profile.csv")}
    matrix = {r["sector_profile"]: r for r in _read_csv("sector_sensitivity_matrix.csv")}
    okved = str(row["mainokved_code"] or "").split(".")[0][:2].zfill(2)
    sp = sector_map.get(okved)
    sens = matrix.get(sp, {})

    def flag(col: str) -> float | None:
        val = row[col]
        if val is None or str(val).strip() == "": return None
        v = str(val).strip().upper()
        if v in {"1", "Y", "YES", "TRUE", "ДА"}: return 1.0
        if v in {"0", "N", "NO", "FALSE", "НЕТ"}: return 0.0
        return None

    loa, lea, factoring, fts = (flag(x) for x in ("cl_product_loa_mark", "cl_product_lea_mark", "factoring_mark", "fts_flag"))
    own: dict[str, float | None] = {k: None for k in factors}
    debt_vals = [(0.6, loa), (0.3, lea), (0.3, factoring)]
    if any(v is not None for _, v in debt_vals):
        own["RATE"] = 1 - math.prod(1 - w * v for w, v in debt_vals if v is not None)
        own["CREDIT"] = own["RATE"]
    if fts is not None or row["fts_country_name"]:
        own["FX"] = 1.0 if fts == 1.0 or row["fts_country_name"] else 0.0
    exposure: dict[str, float] = {}
    for factor in factors:
        prior = None
        if sens and sens.get(factor) not in (None, ""):
            try: prior = min(1.0, max(0.0, float(sens[factor]) / 3.0))
            except ValueError: prior = None
        o = own.get(factor)
        available = []
        if o is not None: available.append((0.4, o))
        if prior is not None: available.append((0.4, prior))
        if available:
            exposure[factor] = sum(w*v for w, v in available) / sum(w for w, _ in available)
    meta = {"client_pseudonym": pseudonym(client_id), "okved_code": row["mainokved_code"], "sector_profile": sp,
            "region": row["region"], "city": row["city"], "revenue_available": row["revenue_company"] is not None,
            "exposure_data_available": bool(exposure), "exposure": exposure,
            "exposure_method": "FM-03/04 partial baseline: product flags + sector prior; payment-chain term omitted when unavailable"}
    return exposure, meta


def _personality(persona: str, factors: dict[str, float]) -> float:
    code = _persona_code(persona)
    row = next((r for r in _read_csv("persona_weights.csv") if r["persona"] == code), None)
    if not row or not factors: return 0.5
    denom = sum(factors.values())
    return sum(float(row.get(f, 0) or 0) * weight for f, weight in factors.items()) / denom if denom else 0.5


def score_event(conn: sqlite3.Connection, doc: sqlite3.Row | dict, client_id: str, persona: str | None = None,
                as_of: str | None = None) -> dict:
    title = str(doc["title"] or "")
    snippet = str(doc["snippet"] or "")
    factors = _event_factors(f"{title} {snippet}")
    exposure, profile = _client_exposure(conn, client_id, factors) if factors else ({}, {"client_pseudonym": pseudonym(client_id), "exposure_data_available": False, "exposure": {}})
    denom = sum(factors.values())
    cov = sum(exposure.get(f, 0.0) * w for f, w in factors.items()) / denom if denom else 0.0
    relevance = 0.7 * cov  # FM-06; Match is 0 because a validated entity/region matcher is not available in this backend
    as_dt = _as_utc(as_of)
    published = _as_utc(doc["published_at_utc"] if "published_at_utc" in doc.keys() else doc.get("published_at_utc"))
    age_days = max(0.0, (as_dt - published).total_seconds() / 86400.0)
    fresh = 0.5 ** (age_days / 3.0)  # FM-08, news half-life
    cred = float(doc["tier_weight"] or 0.4)
    pers = _personality(_persona_code(persona), factors)
    # Imp=0.5 is neutral because a validated severity/surprise classifier is not configured.
    # Nov=1 unless already generated for this client/event; Urg=0 without an event calendar.
    seen = conn.execute("SELECT 1 FROM cards WHERE client_id=? AND event_doc_id=? LIMIT 1", (client_id, doc["doc_id"])).fetchone() if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'").fetchone() else None
    nov = 0.0 if seen else 1.0
    imp, urg = 0.5, 0.0
    score = fresh * (0.40 * relevance + 0.25 * imp + 0.10 * urg + 0.10 * nov + 0.15 * cred) * (0.5 + 0.5 * pers)
    return {"score": round(score, 6), "relevance": round(relevance, 6), "freshness": round(fresh, 6), "impact": imp,
            "urgency": urg, "novelty": nov, "credibility": cred, "persona_interest": round(pers, 6),
            "event_factors": factors, "client_profile": profile,
            "provisional": True, "provisional_reasons": ["Impact defaulted to neutral 0.5 without calibrated classifier", "Urgency is 0 without a decision-event calendar", "Relevance uses partial FM-03/04 inputs; entity Match and payment-chain exposure are unavailable"]}


def _bank_context(conn: sqlite3.Connection, factors: dict[str, float], title: str, as_of: str | None = None) -> dict:
    candidates = []
    mapping = [("RATE", "key_rate", "ключевая ставка"), ("INFL", "inflation", "инфляция"), ("FX", "fx", "валютный курс"),
               ("COMMOD", "gold", "золото цены сырья")]
    for factor, indicator, query in mapping:
        if factor not in factors: continue
        try:
            records = bv.get_bank_view(conn, indicator, None, as_of)
        except Exception:
            records = []
        if records:
            candidates.extend([{"indicator": indicator, "record": r} for r in records])
        hits = search_alfa_analytics(conn, query, k=3, as_of=as_of)
        candidates.extend([{"indicator": indicator, "chunk": h} for h in hits])
    return {"label": "Позиция Альфа-Банка", "records": candidates[:8],
            "available": bool(candidates), "as_of": None,
            "note": "Только найденные записи с привязкой к документу; отсутствие записи не трактуется как позиция банка."}


def _build_payload(conn: sqlite3.Connection, doc: sqlite3.Row, client_id: str, persona: str, as_of: str | None) -> dict:
    title, snippet = doc["title"] or "Без заголовка", doc["snippet"] or ""
    score = score_event(conn, doc, client_id, persona, as_of)
    factors = score["event_factors"]
    bank = _bank_context(conn, factors, title, as_of)
    factor_names = [FACTOR_NAMES.get(f, f) for f in factors]
    client = score["client_profile"]
    why = (f"Для компании {client.get('client_pseudonym')} отраслевой профиль {client.get('sector_profile') or 'не определён'}; "
           f"событие связано с факторами: {', '.join(factor_names) or 'не классифицированы'}. "
           "Релевантность предварительная и рассчитана по доступным признакам.")
    if "RATE" in factors:
        actions = ["Проверьте долю плавающего долга и ближайшие даты пересмотра ставок.", "Сопоставьте график рефинансирования с актуальной позицией банка."]
    elif "FX" in factors:
        actions = ["Сопоставьте валютные поступления и платежи по валютам и срокам.", "Проверьте естественное хеджирование и условия пересмотра цен."]
    elif "INFL" in factors or "ENERGY" in factors or "COMMOD" in factors:
        actions = ["Проверьте долю соответствующих затрат в себестоимости.", "Сверьте договорные механизмы индексации и возможность переноса затрат в цену."]
    else:
        actions = ["Проверьте источник и дату события.", "Уточните, затрагивает ли событие ключевые рынки или контрагентов компании."]
    fact = {"label": "Факт", "doc_id": doc["doc_id"], "title": title, "snippet": snippet, "published_at": doc["published_at"],
            "source_id": doc["source_id"], "url": doc["url"], "source_weight": doc["tier_weight"]}
    ai = {"label": "Вывод AI", "method": "deterministic heuristic baseline", "confidence": min(float(doc["tier_weight"] or 0.4), 0.55),
          "text": "Факторы и действия сформированы детерминированными правилами. Это не подтверждённый прогноз и не заменяет анализ риска/финансовую консультацию.",
          "provisional": True, "factor_codes": list(factors)}
    return {"card_id": None, "persona": _persona_code(persona), "client_pseudonym": client.get("client_pseudonym"),
            "status": "provisional", "publishable": False,
            "score": score, "layers": {"fact": fact, "bank_position": bank, "ai_conclusion": ai},
            "sections": {"what_happened": title, "why_you": why,
                         "possible_implications": [f"Возможная связь: {FACTOR_NAMES.get(f, f)}" for f in factors],
                         "actions_to_check": actions, "bank_position": bank,
                         "basis": {"source": fact, "client_features": {k: v for k, v in client.items() if k != "exposure"},
                                   "exposure": client.get("exposure"), "factor_codes": list(factors), "provisional": True}},
            "trace_id": None}


def _trace(conn, trace_id: str, card_id: str | None, step: str, tool: str, args: dict, result_ref: str | None = None,
           status: str = "ok", duration_ms: int = 0) -> None:
    write_trace(conn, {"trace_id": trace_id, "card_id": card_id, "step": step, "tool": tool,
                       "args": args, "result_ref": result_ref, "status": status, "duration_ms": duration_ms})


def generate_card(conn: sqlite3.Connection, *, client_id: str, persona: str | None = None,
                  event_doc_id: str | None = None, query: str | None = None, as_of: str | None = None) -> dict:
    if not get_client_record(conn, client_id): raise KeyError("client not found")
    if not event_doc_id and not query: raise ValueError("provide event_doc_id or query")
    as_of = _as_utc(as_of).strftime("%Y-%m-%dT%H:%M:%SZ") if as_of else None
    if event_doc_id:
        doc = conn.execute("SELECT * FROM documents WHERE doc_id=? AND ready_at IS NOT NULL AND (? IS NULL OR published_at_utc<=?)", (event_doc_id, as_of, as_of)).fetchone()
    else:
        hits = search_news(conn, query or "", as_of=as_of, k=1)
        doc = conn.execute("SELECT * FROM documents WHERE doc_id=?", (hits[0]["doc_id"],)).fetchone() if hits else None
    if not doc: raise KeyError("event not found or unavailable")
    trace_id, card_id = new_id("tr_"), new_id("card_")
    start = now_utc()
    _trace(conn, trace_id, card_id, "select_event", "search_news", {"event_doc_id": event_doc_id, "query_present": bool(query)}, doc["doc_id"])
    payload = _build_payload(conn, doc, client_id, _persona_code(persona), as_of)
    payload["card_id"], payload["trace_id"] = card_id, trace_id
    with conn:
        conn.execute("INSERT INTO cards(card_id,trace_id,client_id,persona,event_doc_id,as_of,score,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (card_id, trace_id, client_id, _persona_code(persona), doc["doc_id"], as_of or iso(now_utc()), payload["score"]["score"],
                      json.dumps(payload, ensure_ascii=False), iso(now_utc())))
    _trace(conn, trace_id, card_id, "assemble_card", "deterministic_card_builder", {"factor_count": len(payload["score"]["event_factors"]), "client": pseudonym(client_id)}, card_id,
           duration_ms=int((now_utc()-start).total_seconds()*1000))
    return payload


def get_card(conn: sqlite3.Connection, card_id: str) -> dict | None:
    row = conn.execute("SELECT payload_json FROM cards WHERE card_id=?", (card_id,)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def feed(conn: sqlite3.Connection, client_id: str, persona: str | None = None, as_of: str | None = None, k: int = 5) -> list[dict]:
    if not get_client_record(conn, client_id): raise KeyError("client not found")
    as_of = _as_utc(as_of).strftime("%Y-%m-%dT%H:%M:%SZ") if as_of else None
    rows = conn.execute("SELECT * FROM documents WHERE ready_at IS NOT NULL AND (? IS NULL OR published_at_utc<=?) ORDER BY published_at_utc DESC LIMIT 300",
                         (as_of, as_of)).fetchall()
    scored = []
    for d in rows:
        s = score_event(conn, d, client_id, persona, as_of)
        if s["relevance"] >= 0.25:
            scored.append({"doc_id": d["doc_id"], "title": d["title"], "snippet": d["snippet"], "url": d["url"],
                           "published_at": d["published_at"], "source_id": d["source_id"], "score": s["score"],
                           "relevance": s["relevance"], "factors": s["event_factors"], "provisional": True})
    scored.sort(key=lambda x: (x["score"], x["published_at"] or ""), reverse=True)
    return scored[:min(max(k, 1), 5)]


def ask_card(conn: sqlite3.Connection, card_id: str, question: str, scenario: dict[str, Any] | None = None) -> dict:
    card = get_card(conn, card_id)
    if not card: raise KeyError("card not found")
    trace_id = card["trace_id"]
    if scenario is not None:
        result = compute_scenario(scenario)
        answer = {"mode": "deterministic_scenario", "question": question, "result": result,
                  "note": "Расчёт использует только введённые числа и формулу; не является прогнозом."}
    else:
        doc_id = card["layers"]["fact"]["doc_id"]
        source = conn.execute("SELECT doc_id,title,url,published_at,snippet,text FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        query_terms = " ".join(w for w in question.split() if len(w) > 2)[:200]
        chunks = search_alfa_analytics(conn, query_terms, k=3) if query_terms else []
        answer = {"mode": "grounded_context", "question": question,
                  "available_evidence": [{"doc_id": source["doc_id"], "title": source["title"], "url": source["url"],
                                           "published_at": source["published_at"], "snippet": source["snippet"]}] if source else [],
                  "alfa_chunks": chunks,
                  "answer": "Возвращены подтверждённые источники для уточнения. Семантический ответ модели не выполнен в этом детерминированном режиме.",
                  "provisional": True}
    with conn:
        conn.execute("INSERT INTO card_questions(card_id,question,answer_json,trace_id,created_at) VALUES(?,?,?,?,?)",
                     (card_id, question, json.dumps(answer, ensure_ascii=False), trace_id, iso(now_utc())))
    _trace(conn, trace_id, card_id, "answer_question", "ask_card", {"question_length": len(question), "scenario": bool(scenario)}, card_id)
    return answer


def add_feedback(conn: sqlite3.Connection, card_id: str, helpful: bool, reason: str | None) -> dict:
    if not get_card(conn, card_id): raise KeyError("card not found")
    with conn:
        cur = conn.execute("INSERT INTO card_feedback(card_id,helpful,reason,created_at) VALUES(?,?,?,?)",
                           (card_id, int(helpful), (reason or "")[:500], iso(now_utc())))
    _trace(conn, conn.execute("SELECT trace_id FROM cards WHERE card_id=?", (card_id,)).fetchone()[0], card_id,
           "feedback", "card_feedback", {"helpful": bool(helpful), "reason_present": bool(reason)}, str(cur.lastrowid))
    return {"feedback_id": int(cur.lastrowid), "card_id": card_id, "stored": True}


def card_trace(conn: sqlite3.Connection, card_id: str) -> list[dict]:
    row = conn.execute("SELECT trace_id FROM cards WHERE card_id=?", (card_id,)).fetchone()
    if not row: raise KeyError("card not found")
    return [dict(r) for r in conn.execute("SELECT trace_id,card_id,step,tool,args_redacted,started_at,duration_ms,model,tokens_in,tokens_out,cost_rub,result_ref,status,retry_of FROM trace_events WHERE trace_id=? ORDER BY id", (row["trace_id"],))]
