import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from app.config import get_config, get_models
from app.ingest.alfa_corpus import blocks_to_chunks, corpus_report, load_registry, parse_corpus, parse_vlm_json, scan_dir, verify_chunk
from app.llm_gateway import BudgetExceeded, LLMError, LLMGateway
from app.search import search_alfa_analytics


def llm_transport(responder, counter):
    def handler(request: httpx.Request):
        counter["n"] += 1
        body = json.loads(request.content)
        content = responder(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
    return httpx.MockTransport(handler)


def gw(conn, responder=lambda b: "hello", counter=None):
    return LLMGateway(conn, transport=llm_transport(responder, counter if counter is not None else {"n": 0}), base_url="http://llm.test/v1", api_key="k", sleep=lambda s: None)


# ---------------- gateway ----------------
def test_gateway_logs_tokens_cost_and_caches(conn):
    get_models()["general"].update(p_in_rub_per_1k=0.2, p_out_rub_per_1k=0.4)
    cnt = {"n": 0}
    g = gw(conn, counter=cnt)
    msgs = [{"role": "user", "content": "hi"}]
    r1 = g.chat(operation="summarize", role="general", messages=msgs, trace_id="t1")
    assert (r1.tokens_in, r1.tokens_out, r1.cache_hit) == (1000, 500, False) and r1.cost_rub == pytest.approx(0.4)   # 1*0.2 + 0.5*0.4
    r2 = g.chat(operation="summarize", role="general", messages=msgs, trace_id="t2")
    assert r2.cache_hit and cnt["n"] == 1 and r2.cost_rub == 0
    rows = conn.execute("SELECT trace_id,model,tokens_in,cost_rub,cache_hit,status FROM llm_calls ORDER BY call_id").fetchall()
    assert len(rows) == 2 and rows[0]["trace_id"] == "t1" and rows[1]["cache_hit"] == 1 and all(r["model"] for r in rows)   # 100% calls logged


def test_gateway_budget_guard_blocks_but_cache_still_answers(conn):
    get_models()["general"].update(p_in_rub_per_1k=100.0, p_out_rub_per_1k=0.0)      # 100 RUB per call
    cfg = get_config(); cfg.raw["budget"]["daily_limit_rub"] = 250
    g = gw(conn)
    m = lambda s: [{"role": "user", "content": s}]
    assert not g.chat(operation="x", role="general", messages=m("a")).soft_limit_reached      # 100 / 250
    assert not g.chat(operation="x", role="general", messages=m("b")).soft_limit_reached      # 200 / 250 = 0.8 at the *next* call's check
    r3 = g.chat(operation="x", role="general", messages=m("c"))
    assert r3.soft_limit_reached                                                                # soft limit at 80%
    with pytest.raises(BudgetExceeded):                                                         # 300 >= 250: hard stop for NEW requests
        g.chat(operation="x", role="general", messages=m("d"))
    assert g.chat(operation="x", role="general", messages=m("a")).cache_hit                     # ...but cached answers still work
    assert conn.execute("SELECT COUNT(*) FROM llm_calls WHERE status='blocked'").fetchone()[0] == 1
    assert g.usage_today()["hard"] is True


def test_gateway_whitelist_and_config_errors(conn):
    g = gw(conn)
    with pytest.raises(ValueError):
        g.chat(operation="x", role="gpt-4o", messages=[])                                       # only models from models.yaml are routable
    g2 = LLMGateway(conn, transport=httpx.MockTransport(lambda r: httpx.Response(200)), base_url="")
    with pytest.raises(LLMError):
        g2.chat(operation="x", role="general", messages=[{"role": "user", "content": "q"}])


def test_gateway_retries_5xx_then_fails_logged(conn):
    g = LLMGateway(conn, transport=httpx.MockTransport(lambda r: httpx.Response(503)), base_url="http://llm.test/v1", sleep=lambda s: None)
    with pytest.raises(LLMError):
        g.chat(operation="x", role="general", messages=[{"role": "user", "content": "q"}])
    assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "error"


# ---------------- corpus ----------------
def make_pdf(path: Path, pages: int):
    doc = pymupdf.open()
    for i in range(pages):
        pg = doc.new_page(width=420, height=300)
        pg.insert_text((40, 60), f"Page {i + 1}", fontsize=20)
    doc.save(path); doc.close()


def make_png(path: Path, label: str = "slide"):
    doc = pymupdf.open(); pg = doc.new_page(width=300, height=200); pg.insert_text((30, 50), label, fontsize=18)
    pg.get_pixmap().save(path); doc.close()


PAGE_JSON = {
    "page_title": "Key rate", "as_of_date": "2026-09-11",
    "blocks": [
        {"block_type": "text", "text": "Ключевая ставка сохранена на уровне 14,0%, риторика стала жёстче.",
         "numbers": [{"value": "14,0", "unit": "% годовых", "subject": "ключевая ставка", "date": "2026-09-11"}], "bbox": None, "confidence": 0.95},
        {"block_type": "chart_caption", "text": "Прогноз ключевой ставки, % годовых", "numbers": [], "bbox": [0.1, 0.4, 0.9, 0.9], "confidence": 0.9},
        {"block_type": "chart_estimate", "text": "Ставка на конец 2027 около 12", "numbers": [{"value": 12, "subject": "ставка конец 2027"}], "bbox": [0.1, 0.4, 0.9, 0.9], "confidence": 0.6},
        {"block_type": "text", "text": "Обычный абзац без чисел.", "numbers": [], "confidence": 0.97},
    ]}


@pytest.fixture
def corpus_dir(tmp_path):
    d = tmp_path / "corpus"; d.mkdir()
    make_pdf(d / "DOC-07_key_rate.pdf", 2)
    make_png(d / "DOC-01_slide1.png", "slide one"); make_png(d / "DOC-01_slide2.png", "slide two")
    (d / "notes.txt").write_text("ignored")
    make_pdf(d / "DOC-99_unknown.pdf", 1)
    return d


def test_registry_loads_with_stale_and_missing(conn):
    assert load_registry(conn) == 10
    st = {r["doc_id"]: r for r in conn.execute("SELECT * FROM alfa_docs")}
    assert st["DOC-09"]["stale"] == 1 and st["DOC-07"]["stale"] == 0 and st["DOC-07"]["doc_date"] == "2026-09-11"
    assert st["DOC-10"]["doc_date"] is None and st["DOC-10"]["parse_status"] == "missing"


def test_parse_corpus_end_to_end(conn, corpus_dir):
    cnt = {"n": 0}
    g = gw(conn, lambda b: json.dumps(PAGE_JSON, ensure_ascii=False), cnt)
    rep = parse_corpus(conn, corpus_dir, g)
    assert sorted(rep["parsed"]) == ["DOC-01", "DOC-07"] and rep["unknown_files"] == ["DOC-99"] and "DOC-10" in rep["missing"]
    assert cnt["n"] == 4                                             # 2 pdf pages + 2 slide images, one VLM call each
    ids = [r["chunk_id"] for r in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id='DOC-07' ORDER BY chunk_id")]
    assert ids[0] == "DOC-07-p1-b1" and "DOC-07-p2-b4" in ids
    c = {r["chunk_id"]: r for r in conn.execute("SELECT * FROM chunks")}
    est = c["DOC-07-p1-b3"]
    assert est["block_type"] == "chart_estimate" and est["needs_manual_check"] == 1
    assert json.loads(est["numbers_json"])[0]["is_fact"] is False   # chart estimates never become facts
    fact = c["DOC-07-p1-b1"]
    assert json.loads(fact["numbers_json"])[0]["value"] == 14.0 and fact["needs_manual_check"] == 1 and fact["as_of_date"] == "2026-09-11"
    assert c["DOC-07-p1-b4"]["needs_manual_check"] == 0              # high-confidence text without numbers
    from app.config import data_dir
    assert (data_dir() / c["DOC-07-p1-b2"]["image_path"]).exists()   # chart crop saved for UI attribution
    assert (data_dir() / c["DOC-07-p1-b1"]["image_path"]).exists()   # whole page image
    assert conn.execute("SELECT parse_status,pages FROM alfa_docs WHERE doc_id='DOC-07'").fetchone()["pages"] == 2
    assert conn.execute("SELECT parse_status FROM alfa_docs WHERE doc_id='DOC-01'").fetchone()[0] == "parsed"
    assert conn.execute("SELECT COUNT(*) FROM llm_calls WHERE operation='pdf_parse'").fetchone()[0] == 4

    # second run: unchanged files are skipped, no new model calls (offline once, cached by hash)
    rep2 = parse_corpus(conn, corpus_dir, g)
    assert sorted(rep2["skipped"]) == ["DOC-01", "DOC-07"] and cnt["n"] == 4
    # forced re-parse is served by the gateway cache -> still no new paid calls
    parse_corpus(conn, corpus_dir, g, force=True)
    assert cnt["n"] == 4


def test_search_alfa_returns_doc_and_page_and_flags(conn, corpus_dir):
    parse_corpus(conn, corpus_dir, gw(conn, lambda b: json.dumps(PAGE_JSON, ensure_ascii=False)))
    hits = search_alfa_analytics(conn, "ключевая ставка риторика")
    assert hits[0]["doc_id"] in ("DOC-07", "DOC-01") and hits[0]["page"] >= 1 and hits[0]["image_path"]
    assert all(h["stale"] is False for h in hits)
    only = search_alfa_analytics(conn, "ставка", doc_ids=["DOC-07"])
    assert only and {h["doc_id"] for h in only} == {"DOC-07"}
    assert search_alfa_analytics(conn, "ставка", as_of="2026-01-01") == []      # nothing dated before the bank published it


def test_verify_chunk_manual_check_flow(conn, corpus_dir):
    parse_corpus(conn, corpus_dir, gw(conn, lambda b: json.dumps(PAGE_JSON, ensure_ascii=False)))
    assert verify_chunk(conn, "DOC-07-p1-b1", numbers=[{"value": 14.0, "unit": "% годовых", "subject": "ключевая ставка", "is_fact": True}])
    r = conn.execute("SELECT verified_by,needs_manual_check FROM chunks WHERE chunk_id='DOC-07-p1-b1'").fetchone()
    assert (r["verified_by"], r["needs_manual_check"]) == ("manual", 0)
    assert not verify_chunk(conn, "DOC-07-p9-b9")
    rep = {d["doc_id"]: d for d in corpus_report(conn)["docs"]}
    assert rep["DOC-07"]["verified"] == 1 and rep["DOC-07"]["needs_check"] >= 2 and "DOC-10" in corpus_report(conn)["not_parsed"]


def test_bad_vlm_output_gives_partial_status_not_silent_garbage(conn, tmp_path):
    d = tmp_path / "c"; d.mkdir(); make_pdf(d / "DOC-04_inflation.pdf", 2)
    answers = iter(["this is not json at all", json.dumps(PAGE_JSON)])
    rep = parse_corpus(conn, d, gw(conn, lambda b: next(answers)))
    assert rep["partial"] == ["DOC-04"]
    assert conn.execute("SELECT parse_status FROM alfa_docs WHERE doc_id='DOC-04'").fetchone()[0] == "partial"
    bad = conn.execute("SELECT * FROM chunks WHERE chunk_id='DOC-04-p1-b0'").fetchone()
    assert bad["needs_manual_check"] == 1 and bad["extraction_model"] is None
    assert search_alfa_analytics(conn, "extraction failed") == []             # placeholder is not searchable


def test_local_ocr_missing_tesseract_has_actionable_message(monkeypatch):
    import app.ingest.alfa_corpus as corpus

    monkeypatch.setattr(corpus.shutil, "which", lambda _name: None)
    with pytest.raises(LLMError, match="not found on PATH.*rus and eng"):
        corpus._local_ocr(b"not-used-because-preflight-fails")


def test_all_pages_failed_is_failed_not_partial(conn, tmp_path, monkeypatch):
    import app.ingest.alfa_corpus as corpus

    d = tmp_path / "c"; d.mkdir(); make_pdf(d / "DOC-02_macro.pdf", 2)
    monkeypatch.setattr(corpus, "_local_ocr", lambda _png: (_ for _ in ()).throw(LLMError("tesseract not installed")))
    rep = parse_corpus(conn, d, LLMGateway(conn, base_url=""))

    assert rep["failed"] == ["DOC-02"]
    assert rep["partial"] == [] and rep["parsed"] == []
    assert len(rep["errors"]) == 2
    assert conn.execute("SELECT parse_status FROM alfa_docs WHERE doc_id='DOC-02'").fetchone()[0] == "failed"
    assert all("tesseract not installed" in e["error"] for e in rep["errors"])


def test_ocr_fallback_can_finish_a_document_but_is_unverified(conn, tmp_path, monkeypatch):
    import app.ingest.alfa_corpus as corpus

    d = tmp_path / "c"; d.mkdir(); make_pdf(d / "DOC-02_macro.pdf", 1)
    monkeypatch.setattr(corpus, "_local_ocr", lambda _png: "OCR text, verify manually")
    rep = parse_corpus(conn, d, LLMGateway(conn, base_url=""))

    assert rep["parsed"] == ["DOC-02"] and rep["failed"] == []
    chunk = conn.execute("SELECT * FROM chunks WHERE doc_id='DOC-02'").fetchone()
    assert chunk["extraction_model"] == "tesseract-ocr (unverified fallback)"
    assert chunk["needs_manual_check"] == 1
    assert json.loads(chunk["numbers_json"]) == []


def test_budget_exhaustion_stops_corpus_parse_with_partial(conn, tmp_path):
    get_models()["vlm"].update(p_in_rub_per_1k=300.0)
    get_config().raw["budget"]["daily_limit_rub"] = 400
    d = tmp_path / "c"; d.mkdir(); make_pdf(d / "DOC-02_macro.pdf", 4)
    rep = parse_corpus(conn, d, gw(conn, lambda b: json.dumps(PAGE_JSON)))
    assert rep["budget_blocked"] and rep["partial"] == ["DOC-02"]
    assert conn.execute("SELECT COUNT(*) FROM llm_calls WHERE status='ok'").fetchone()[0] == 2    # 300 -> 600 >= 400, then blocked


def test_helpers_json_and_numbers():
    assert parse_vlm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_vlm_json('Here you go: {"blocks": []} thanks') == {"blocks": []}
    out = blocks_to_chunks("DOC-02", 1, {"blocks": [{"text": "x", "block_type": "weird", "numbers": [{"value": "abc"}], "bbox": [0.9, 0.1, 0.2, 0.2]}]}, "m", "2026-07-31", 0.8)
    ch, bbox = out[0]
    assert ch.block_type == "text" and ch.numbers == [] and bbox is None and ch.as_of_date == "2026-07-31"


def test_scan_dir_groups_by_doc_id(corpus_dir):
    g = scan_dir(corpus_dir)
    assert set(g) == {"DOC-01", "DOC-07", "DOC-99"} and len(g["DOC-01"]) == 2


def test_supplied_pdf_filenames_map_to_registry_ids(tmp_path):
    files = [
        "ALFA-macro_stat1H26.pdf",
        "Inflation_tracker_02.pdf",
        "ALFA-Macro_Insights-Policy_Rate_11Sep.pdf",
    ]
    for name in files:
        (tmp_path / name).write_bytes(b"placeholder")
    groups = scan_dir(tmp_path)
    assert set(groups) == {"DOC-02", "DOC-04", "DOC-07"}


def test_offline_russian_stemmer_matches_inflections(monkeypatch):
    import app.nlp as nlp
    monkeypatch.setattr(nlp, "_MORPH", None)
    nlp._lemma.cache_clear()
    try:
        assert nlp.lemma_text("ключевую ставку") == nlp.lemma_text("ключевая ставка")
    finally:
        nlp._lemma.cache_clear()


def test_new_corpus_filenames_map_to_registry_ids(tmp_path):
    files = ["FI_Strategy_3Q26_22072026.pdf", "ALFA-consumption.pdf", "ALFA-7M26.pdf",
             "Gold_digest_August26.pdf", "Бонд_Бриф_14092026.pdf",
             "ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf"]
    for name in files:
        (tmp_path / name).write_bytes(b"placeholder")
    assert set(scan_dir(tmp_path)) == {"DOC-01", "DOC-03", "DOC-05", "DOC-06", "DOC-08", "DOC-09"}


def test_offline_cached_extraction_used_without_a_vlm(conn, tmp_path):
    """No VLM endpoint: the pipeline consumes the per-page cached extraction (same Chunk contract)."""
    import app.ingest.alfa_corpus as corpus
    from app.config import data_dir

    d = tmp_path / "corpus"; d.mkdir()
    make_pdf(d / "DOC-02_macro.pdf", 1)
    cache = data_dir() / "corpus_extraction" / "DOC-02"
    cache.mkdir(parents=True)
    (cache / "p1.json").write_text(json.dumps({"page_title": "x", "as_of_date": "2026-07-31",
                                              "blocks": [{"block_type": "table", "text": "Ставка | 14,0",
                                                          "numbers": [{"value": "14,0", "unit": "% годовых",
                                                                       "subject": "ключевая ставка", "date": "2026-09-11"}],
                                                          "confidence": 0.9}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(corpus, "_local_ocr", lambda _png: (_ for _ in ()).throw(AssertionError("OCR must not run")))
    try:
        rep = parse_corpus(conn, d, LLMGateway(conn, base_url=""))
    finally:
        monkeypatch.undo()
    assert rep["parsed"] == ["DOC-02"] and rep["failed"] == []
    ch = conn.execute("SELECT * FROM chunks WHERE doc_id='DOC-02'").fetchone()
    assert ch["extraction_model"] == corpus.DEFAULT_CACHED_MODEL
    assert ch["as_of_date"] == "2026-07-31"                                  # chunk carries the document date, not the number's date
    assert json.loads(ch["numbers_json"])[0]["value"] == 14.0


def test_cached_extraction_ignored_on_file_hash_mismatch(conn, tmp_path):
    import app.ingest.alfa_corpus as corpus
    from app.config import data_dir

    d = tmp_path / "corpus"; d.mkdir()
    make_pdf(d / "DOC-02_macro.pdf", 1)
    cache = data_dir() / "corpus_extraction" / "DOC-02"
    cache.mkdir(parents=True)
    (cache / "p1.json").write_text(json.dumps({"_meta": {"file_hash": "not-the-hash"},
                                               "blocks": [{"block_type": "text", "text": "stale cache", "numbers": []}]}), encoding="utf-8")
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(corpus, "_local_ocr", lambda _png: "OCR text")
    try:
        rep = parse_corpus(conn, d, LLMGateway(conn, base_url=""))
    finally:
        monkeypatch.undo()
    ch = conn.execute("SELECT extraction_model,text FROM chunks WHERE doc_id='DOC-02'").fetchone()
    assert rep["parsed"] == ["DOC-02"] and ch["extraction_model"] == "tesseract-ocr (unverified fallback)"
    assert ch["text"] == "OCR text"                                          # stale cache rejected by file hash -> OCR fallback


def test_search_alfa_golden_eval_top_k(conn, corpus_dir, tmp_path):
    from app.search import evaluate_alfa_queries
    parse_corpus(conn, corpus_dir, gw(conn, lambda b: json.dumps(PAGE_JSON, ensure_ascii=False)))
    csv = tmp_path / "g.csv"
    csv.write_text('query,expected_doc_ids\n"ключевая ставка риторика",DOC-07\n', encoding="utf-8")
    res = evaluate_alfa_queries(conn, csv, k=3)
    assert res["total"] == 1 and res["passed"] == 1 and res["acceptance_met"]


def test_verify_key_numbers_marks_reviewed_and_flags_mismatch(conn, tmp_path):
    import app.ingest.alfa_corpus as corpus
    from app.config import data_dir

    d = tmp_path / "corpus"; d.mkdir()
    make_pdf(d / "DOC-02_macro.pdf", 1)
    cache = data_dir() / "corpus_extraction" / "DOC-02"
    cache.mkdir(parents=True)
    (cache / "p1.json").write_text(json.dumps({"as_of_date": "2026-07-31",
                                              "blocks": [{"block_type": "text", "text": "ставка 14,0%",
                                                          "numbers": [{"value": 14.0, "subject": "ставка"}], "confidence": 0.9}]}),
                                   encoding="utf-8")
    parse_corpus(conn, d, LLMGateway(conn, base_url=""))
    csv_path = tmp_path / "key.csv"
    csv_path.write_text("chunk_id,value\nDOC-02-p1-b1,14.0\nDOC-02-p1-b1,99.9\n", encoding="utf-8")
    res = corpus.verify_key_numbers(conn, csv_path)
    assert res["checked"] == 2 and res["matched"] == 1 and res["verified"] is False
    assert res["mismatches"] == [{"chunk_id": "DOC-02-p1-b1", "expected": 99.9, "note": None}]
    assert conn.execute("SELECT verified_by FROM chunks WHERE chunk_id='DOC-02-p1-b1'").fetchone()[0] == "manual"
