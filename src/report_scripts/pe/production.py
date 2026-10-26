"""Automatic news -> validated extraction -> DA calculations -> PE input.

Profile imports are batch operations. News extraction is cached independently of
clients. Unknown economic signs remain explicitly unknown, not invented formulas.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

from .backend_bridge import PreparedCase
from .context import attach_news_fragments, fragment_news, role_card
from .evidence import Document
from .participant import products_text
from .pipeline import CaseInput
from .prompts import prepare_request
from .schemas import EventExtraction
from .tasks import run_task

ROOT = Path(__file__).resolve().parents[2]
DA = ROOT / "da_scripts"


def da_functions():
    sys.path.insert(0, str(DA)) if str(DA) not in sys.path else None
    from analytics import formulas, config
    if DA not in Path(formulas.__file__).resolve().parents:
        raise RuntimeError("wrong analytics package imported")
    return formulas, config.load_weights()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def init_profiles(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS pe_event_owners (doc_id TEXT NOT NULL,client_id TEXT NOT NULL,PRIMARY KEY(doc_id,client_id))")
    conn.execute("""CREATE TABLE IF NOT EXISTS pe_da_profiles (
        client_id TEXT PRIMARY KEY REFERENCES clients(synthetic_u), profile_json TEXT NOT NULL,
        client_hash TEXT NOT NULL, revision TEXT NOT NULL, synthetic INTEGER NOT NULL, ambiguous INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pe_event_analysis (
        doc_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, event_json TEXT NOT NULL, created_at TEXT NOT NULL, source_hash TEXT)""")
    if "source_hash" not in {r[1] for r in conn.execute("PRAGMA table_info(pe_event_analysis)")}:
        conn.execute("ALTER TABLE pe_event_analysis ADD COLUMN source_hash TEXT")


def client_hash(row):
    return digest({k: row[k] for k in row.keys() if k != "loaded_at"})


def import_profiles(conn, directory, *, synthetic=False, ambiguous_ids=()):
    """Import only curated aggregate fields; raw payment/person records stay out."""
    init_profiles(conn)
    directory = Path(directory)
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
              for name in ("client_features.csv", "exposures.csv", "payment_features.csv")}
    revision = digest(hashes)
    def rows(name):
        with (directory / name).open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)
    exposures = {r["SYNTHETIC_U"]: r for r in rows("exposures.csv")}
    payments = {r["SYNTHETIC_U"]: r for r in rows("payment_features.csv")}
    ambiguous_ids = set(ambiguous_ids)
    F, cfg = da_functions()
    count = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for r in rows("client_features.csv"):
            uid = r["SYNTHETIC_U"]
            raw = conn.execute("SELECT * FROM clients WHERE synthetic_u=?", (uid,)).fetchone()
            if raw is None:
                raise ValueError("DA client is absent from Backend; load matching source CSVs first")
            if uid not in exposures or uid not in payments:
                raise ValueError("DA feature tables have different keys")
            e, g = exposures[uid], payments[uid]
            eps = {key[4:]: number(value) for key, value in e.items() if key.startswith("eps_")}
            if any(v is not None and not 0 <= v <= 1 for v in eps.values()):
                raise ValueError("invalid exposure range")
            profile = dict(sector=r.get("F03_sector_profile") or None,
                sector_name=raw["mainokved_name"], client_type=r.get("F10_cl_type") or None,
                products={key: number(r.get("F08_"+key)) for key in ("RKO", "GAR", "LOA", "LEA", "DEP", "ZPP", "FACTORING")},
                completeness=number(r.get("F13_completeness")), rate_net=number(e.get("E15_rate_net")),
                exposures=eps, hhi_in=number(g.get("G03_HHI_in")), hhi_out=number(g.get("G03_HHI_out")))
            conn.execute("""INSERT INTO pe_da_profiles VALUES(?,?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET
                profile_json=excluded.profile_json,client_hash=excluded.client_hash,revision=excluded.revision,
                synthetic=excluded.synthetic,ambiguous=excluded.ambiguous""",
                (uid, json.dumps(profile, ensure_ascii=False, allow_nan=False), client_hash(raw), revision, int(synthetic), int(uid in ambiguous_ids)))
            count += 1
        conn.commit()
    except BaseException:
        conn.rollback(); raise
    return dict(imported=count, revision=revision, synthetic=synthetic, ambiguous=len(ambiguous_ids))


def get_profile(conn, client_id):
    init_profiles(conn)
    row = conn.execute("SELECT * FROM pe_da_profiles WHERE client_id=?", (client_id,)).fetchone()
    raw = conn.execute("SELECT * FROM clients WHERE synthetic_u=?", (client_id,)).fetchone()
    if raw is None:
        raise KeyError("client not found")
    if row is None or row["client_hash"] != client_hash(raw):
        raise ValueError("PE_PROFILE_NOT_READY: compute/import current DA aggregates")
    if row["ambiguous"]:
        raise ValueError("PE_PROFILE_AMBIGUOUS: conflicting source rows require review")
    return json.loads(row["profile_json"]), dict(row)


def cutoff(value=None):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(timezone.utc)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class ProductionProvider:
    accepts_trace_id = True

    def __init__(self, *, gateway_factory, pseudonym, store_root):
        self.gateway_factory, self.pseudonym, self.store_root = gateway_factory, pseudonym, Path(store_root)

    def classify(self, conn, doc, *, trace_id):
        text = doc["text"] or doc["snippet"] or ""
        document = Document(id=doc["doc_id"], kind="news", text=text,
            as_of=cutoff(doc["published_at_utc"]).date(), is_mock=doc["origin"] == "synthetic")
        with (self.store_root / "data/ref/event_types.csv").open(encoding="utf-8-sig") as stream:
            types = [{k: row[k] for k in ("event_type", "name", "primary_factors", "secondary_factors")} for row in csv.DictReader(stream)]
        payload = dict(document=document.model_dump(mode="json"), event_types=types)
        request = prepare_request("PRM-01", payload, validate_input=True)
        source_hash = digest([doc["text"], doc["snippet"], doc["title"], doc["published_at_utc"]])
        key = digest([request["prompt_hash"], request["input_hash"], request["model"]])
        row = conn.execute("SELECT * FROM pe_event_analysis WHERE doc_id=? AND input_hash=?", (doc["doc_id"], key)).fetchone()
        if row:
            return EventExtraction.model_validate_json(row["event_json"]), document, [dict(prompt_id="PRM-01", status="cached_event", cache_hit=True,
                tokens_in=0, tokens_out=0, prompt_hash=request["prompt_hash"], input_hash=request["input_hash"], elapsed_ms=0)]
        gateway = self.gateway_factory(conn)
        try:
            result = run_task("PRM-01", payload, gateway)
        finally:
            gateway.close()
        event = EventExtraction.model_validate(result["output"])
        if event.event_type not in {r["event_type"] for r in types}:
            raise ValueError("event type outside configured dictionary")
        # Named people/entities are not used for company matching or sent onward.
        event.entities = []
        conn.execute("INSERT INTO pe_event_analysis VALUES(?,?,?,?,?) ON CONFLICT(doc_id) DO UPDATE SET input_hash=excluded.input_hash,event_json=excluded.event_json,created_at=excluded.created_at,source_hash=excluded.source_hash",
            (doc["doc_id"], key, event.model_dump_json(), datetime.now(timezone.utc).isoformat(), source_hash))
        return event, document, result["calls"]

    def rank(self, conn, doc, profile, persona, *, as_of=None, event=None):
        F, cfg = da_functions()
        if event:
            iota, severity = event.iota, event.severity
        else:
            row = conn.execute("SELECT event_json,source_hash FROM pe_event_analysis WHERE doc_id=?", (doc["doc_id"],)).fetchone()
            if row and row["source_hash"] == digest([doc["text"], doc["snippet"], doc["title"], doc["published_at_utc"]]):
                event = EventExtraction.model_validate_json(row[0]); iota, severity = event.iota, event.severity
            else:
                from app.agent.orchestrator import _event_factors
                iota, severity = _event_factors((doc["title"] or "")+" "+(doc["snippet"] or "")), 3
        if not any(iota.values()):
            return dict(score=0., relevance=0., event_factors={}, classification="pending")
        eps = {k: v for k,v in profile["exposures"].items() if v is not None}
        cov, rel = F.fm06_relevance(eps, iota, match=0.)
        pers = F.fm07_pers(role_card(persona).weights, iota)
        age = (cutoff(as_of)-cutoff(doc["published_at_utc"])).total_seconds()/86400
        fresh = F.fm08_fresh(age, cfg["fm08_half_life_days"]["news"])
        cred = F.fm10_credibility(float(doc["tier_weight"] or 0.4), 1)
        seen = conn.execute("SELECT 1 FROM cards WHERE event_doc_id=? AND client_id=? LIMIT 1", (doc["doc_id"], profile.get("_client_id"))).fetchone()
        nov = F.fm11_novelty(1. if seen else 0.)
        imp = F.fm12_impact(None, None, None, severity_1_5=severity)
        score = F.fm13_score(fresh, rel, imp, 0., nov, cred, pers, cfg["fm13"]["weights"])
        return dict(score=float(score), relevance=float(rel), covariance=float(cov), freshness=float(fresh), impact=float(imp),
            urgency=0., novelty=float(nov), credibility=float(cred), persona_interest=float(pers), event_factors=iota,
            classification="extracted" if event else "lexical_prior", provisional=event is None,
            limitations=["Entity match unavailable: Match=0", "No decision calendar: Urg=0", "Confidence is not a calibrated probability"])

    def bank_context(self, conn, event, as_of):
        from app.search import search_alfa_analytics
        from app.config import get_config
        terms = {"RATE":"ключевая ставка", "CREDIT":"кредит", "INFL":"инфляция", "FX":"валютный курс", "ENERGY":"топливо",
                 "COMMOD":"сырье", "DEMAND":"потребление", "LABOR":"зарплата", "BUDGET":"бюджет", "REG":"регулирование", "GEOPOL":"геополитика", "CPTY":"контрагенты"}
        query = " ".join(terms[k] for k,v in event.iota.items() if v > 0)
        hits = search_alfa_analytics(conn, query, k=20, as_of=cutoff(as_of).isoformat()) if query else []
        docs, entries, outdated = [], [], False
        seen = set()
        for hit in hits:
            cid = hit.get("chunk_id")
            if not cid or cid in seen:
                continue
            row = conn.execute("SELECT c.*,a.doc_date FROM chunks c JOIN alfa_docs a ON a.doc_id=c.doc_id WHERE chunk_id=?", (cid,)).fetchone()
            if not row or row["needs_manual_check"] or row["verified_by"] != "manual" or row["block_type"] == "chart_estimate":
                continue
            day = row["as_of_date"] or row["doc_date"]
            if not day or date.fromisoformat(day) > cutoff(as_of).date():
                continue
            text = row["text"]
            did = f"{row['doc_id']}_p{row['page']}_{len(docs)}"
            docs.append(dict(id=did, kind="bank", text=text, as_of=day, page=row["page"], is_mock=False))
            entries.append(dict(id=f"BANK_{len(entries)}", kind="BANK_VIEW", source=dict(document_id=did, start=0, end=len(text), quote=text)))
            outdated |= (cutoff(as_of).date()-date.fromisoformat(day)).days > get_config().get("corpus.stale_after_days",365)
            seen.add(cid)
            if len(entries) == 3:
                break
        return docs, entries, "outdated" if entries and outdated else "available" if entries else "missing"

    def __call__(self, conn, *, client_id, event_doc_id, persona, as_of=None, trace_id=None):
        role_card(persona)  # Reject unknown roles before any paid extraction.
        init_profiles(conn)
        profile, metadata = get_profile(conn, client_id)
        doc = conn.execute("SELECT * FROM documents WHERE doc_id=? AND ready_at IS NOT NULL", (event_doc_id,)).fetchone()
        if doc is None or cutoff(doc["published_at_utc"]) > cutoff(as_of):
            raise KeyError("event unavailable at requested as_of")
        if doc["origin"] == "byo" and not conn.execute("SELECT 1 FROM pe_event_owners WHERE doc_id=? AND client_id IN (?, '*')",(event_doc_id,client_id)).fetchone():
            raise KeyError("event not available for this client")
        event, document, calls = self.classify(conn, doc, trace_id=trace_id or "prepare_"+uuid.uuid4().hex)
        if not any(event.iota.values()) or not event.facts:
            raise ValueError("EVENT_NO_SUPPORTED_FACTS: no grounded event to interpret")
        profile["_client_id"] = client_id
        rank = self.rank(conn, doc, profile, persona, as_of=as_of, event=event)
        F, cfg = da_functions()
        alias = self.pseudonym(client_id)
        def feature(eid, fid, value):
            return dict(id=eid, kind="COMPANY_FEATURE", client_id=alias, feature_id=fid, value=value,
                source_record_id=f"DA:{metadata['revision']}:{alias}:{fid}")
        entries = [dict(id="NEWS", kind="NEWS_FACT", source=dict(document_id=document.id, start=0, end=len(document.text), quote=document.text)),
            feature("C_PRODUCTS", "F-08", products_text(profile["products"])),
            feature("C_SECTOR", "F-03", (profile["sector"] or "unknown")+"; "+(profile["sector_name"] or "нет отраслевого описания")),
            feature("C_TYPE", "F-10", profile["client_type"]), feature("C_COMPLETE", "F-13", profile["completeness"]),
            feature("C_NET", "E-15", profile["rate_net"]),
            feature("C_LIMITS", "F-13", "Суммы, ставки и сроки договоров неизвестны. Нулевые продуктовые флаги не доказывают отсутствие долга в других банках. Единицы выручки и дата агрегатов не подтверждены; выручка не передана.")]
        for factor, value in profile["exposures"].items():
            if value is not None:
                entries.append(feature("EPS_"+factor, "E-14", value))
        confidence, _ = F.fm20_confidence(rank["credibility"], profile["completeness"] or 0., .5, cfg["fm20_confidence"])
        effect_ids = {}
        for factor, intensity in event.iota.items():
            if intensity <= 0:
                continue
            eid = "SIGN_"+factor
            effect_ids[factor] = eid
            if factor == "RATE":
                sign = F.fm14_sign_effect(profile["rate_net"] if profile["rate_net"] is not None else float("nan"),
                    d_rate=event.direction[factor], threshold=cfg["fm14"]["rate_threshold"])["effect"]
                effect = "two_branches" if not math.isfinite(sign) else "negative" if sign < 0 else "positive" if sign > 0 else "neutral"
                entries.append(dict(id=eid, kind="COMPUTED_VALUE", formula_id="FM-14", value=effect,
                    input_ids=["NEWS", "C_NET", "C_PRODUCTS"], calculation_ref="DA/FM-14; extracted factor direction, conditional on the event horizon"))
            else:
                entries.append(dict(id=eid, kind="UNKNOWN_EFFECT", factor=factor, value="two_branches", input_ids=["NEWS", "C_LIMITS"],
                    reason="Для этого фактора нет утвержденного правила знака на доступных данных клиента; нужны условия и уточняющий вопрос."))
        for eid, fid, value in [("REL", "FM-06", rank["relevance"]), ("CONF", "FM-20", float(confidence)), ("SCORE", "FM-13", rank["score"])]:
            entries.append(dict(id=eid, kind="COMPUTED_VALUE", formula_id=fid, value=value,
                input_ids=["NEWS", "C_COMPLETE", "C_LIMITS"]+[e["id"] for e in entries if e["id"].startswith("EPS_")],
                calculation_ref=f"DA/{fid}; revision {metadata['revision']}; Match=0, Urg=0; provisional critic prior=.5; not calibrated probability"))
        bank_docs, bank_entries, status = self.bank_context(conn, event, as_of)
        case = CaseInput.model_validate(dict(ledger=dict(trace_id=trace_id or "prepare_"+uuid.uuid4().hex, client_id=alias,
            event_id=event_doc_id, documents=[document.model_dump(mode="json")]+bank_docs, entries=entries+bank_entries),
            event=event.model_dump(mode="json"), persona=persona, bank_view_status=status,
            relevance_id="REL", confidence_id="CONF", effect_source_ids=effect_ids))
        case.ledger = attach_news_fragments(case.ledger)
        spans = []
        for fact in event.facts:
            start = document.text.find(fact.source_span)
            spans.append((start, start+len(fact.source_span)))
        selected = [f.id for f in fragment_news(document) if any(f.source.start < end and f.source.end > start for start,end in spans)]
        return PreparedCase(case=case, selected_news_ids=selected, profile="connected_v1", rank_score=rank["score"],
            preparation_calls=calls, profile_revision=metadata["revision"], synthetic=bool(metadata["synthetic"]))

    def feed(self, conn, client_id, persona=None, as_of=None, k=5):
        from app.agent.orchestrator import _persona_code
        from app.util import now_utc
        profile, metadata = get_profile(conn, client_id)
        profile["_client_id"] = client_id
        F, cfg = da_functions()
        rows = conn.execute("""SELECT * FROM documents d WHERE ready_at IS NOT NULL AND published_at_utc<=?
            AND (origin!='byo' OR EXISTS(SELECT 1 FROM pe_event_owners o WHERE o.doc_id=d.doc_id AND o.client_id IN (?, '*')))
            ORDER BY published_at_utc DESC LIMIT 300""",
            (cutoff(as_of).strftime("%Y-%m-%dT%H:%M:%SZ"),client_id)).fetchall()
        scored = []
        for doc in rows:
            rank = self.rank(conn, doc, profile, _persona_code(persona), as_of=as_of)
            if rank["relevance"] < cfg["fm13"]["rel_threshold"]:
                continue
            scored.append(dict(doc_id=doc["doc_id"], event_id=doc["doc_id"], title=doc["title"], snippet=doc["snippet"],
                url=doc["url"], published_at=doc["published_at"], source_id=doc["source_id"], score=rank["score"], relevance=rank["relevance"],
                factors=rank["event_factors"], provisional=rank.get("provisional", True), classification=rank["classification"],
                profile_revision=metadata["revision"], fresh=rank.get("freshness", 0.), rel=rank["relevance"],
                imp=rank.get("impact", .5), urg=0., nov=rank.get("novelty",1.), cred=rank.get("credibility",.4), pers=rank.get("persona_interest",.5)))
        return F.fm13_filter_and_topk(scored, cfg["fm13"]["weights"], cfg["fm13"]["rel_threshold"], top_k=min(k,5),
            mmr_lambda=cfg["fm13"]["mmr_lambda"], sim_fn=lambda a,b: F.fm17_tf_cosine(a["title"], b["title"]))
