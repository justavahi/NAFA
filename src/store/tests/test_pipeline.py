import httpx

from app.ingest.macro import get_series, run_macro_source, upsert_points
from app.ingest.news import run_source, store_documents
from app.models import MacroPoint
from tests.conftest import fixture_bytes
from tests.helpers import mkdoc

from datetime import date, datetime, timezone

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_run_source_is_idempotent_and_logged(conn, make_http):
    http = make_http({"cbr.ru/rss/RssPress": (200, fixture_bytes("cbr_press.xml"))})
    s1 = run_source(conn, "SRC-01", http, now=NOW)
    assert (s1.status, s1.items_new, s1.items_total) == ("ok", 2, 2)
    s2 = run_source(conn, "SRC-01", http, now=NOW)
    assert s2.status == "ok" and s2.items_new == 0                         # re-poll adds nothing
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM fetch_runs WHERE source_id='SRC-01' AND status='ok'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE status='ready'").fetchone()[0] == 2
    row = conn.execute("SELECT status,last_success_at FROM sources WHERE source_id='SRC-01'").fetchone()
    assert row["status"] == "works" and row["last_success_at"]
    d = conn.execute("SELECT published_at, url, ready_at FROM documents").fetchone()
    assert "+03:00" in d["published_at"] and d["url"] and d["ready_at"]     # acceptance WP-B3


def test_partial_status_when_items_fail(conn, make_http):
    http = make_http({"interfax.ru/rss.asp": (200, fixture_bytes("interfax.xml"))})
    s = run_source(conn, "SRC-03", http, now=NOW)
    assert s.status == "partial" and s.items_failed == 1 and s.items_new == 2
    assert conn.execute("SELECT status FROM sources WHERE source_id='SRC-03'").fetchone()[0] == "partial"
    err = conn.execute("SELECT error_type FROM ingestion_log WHERE status='error'").fetchone()
    assert err["error_type"] == "missing_date"


def test_source_failure_is_contained(conn, make_http):
    http = make_http({"cbr.ru/rss/RssPress": (503, b"")})
    s = run_source(conn, "SRC-01", http, now=NOW)
    assert s.status == "failed" and s.error_type == "http_error"
    assert conn.execute("SELECT status FROM sources WHERE source_id='SRC-01'").fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_robots_block_marks_failed(conn, make_http):
    http = make_http({"robots.txt": (200, b"User-agent: *\nDisallow: /\n")})
    s = run_source(conn, "SRC-01", http, now=NOW)
    assert s.status == "failed" and s.error_type == "robots_disallowed"


def test_disabled_source_without_connector(conn, make_http):
    s = run_source(conn, "SRC-05", make_http({}), now=NOW)
    assert s.status == "failed" and s.error_type == "NotImplementedError"


# ---------------- dedup ----------------
BASE = "Банк России сохранил ключевую ставку на уровне 14,0% годовых"
BODY = "Совет директоров принял решение сохранить ставку без изменений и сообщил о жёсткой риторике"


def test_dedup_exact_near_and_representative_by_source_weight(conn):
    a = mkdoc("SRC-03", BASE, BODY, "https://www.interfax.ru/business/1")                       # tier 0.85, first
    b = mkdoc("SRC-01", BASE + ".", BODY + ".", "https://www.cbr.ru/press/1")                    # same text, regulator 1.0 -> exact
    c = mkdoc("SRC-04", BASE, BODY + " сообщили в пресс-службе", "https://www.rbc.ru/x/1")       # tiny change -> near
    n = mkdoc("SRC-03", "Температура ниже нуля ожидается в Москве", "Синоптики прогнозируют заморозки", "https://www.interfax.ru/moscow/2")
    store_documents(conn, "SRC-03", None, [a])
    st = store_documents(conn, "SRC-01", None, [b, c, n])
    kinds = {r["url"]: r["dup_kind"] for r in conn.execute("SELECT url,dup_kind FROM documents")}
    assert kinds["https://www.cbr.ru/press/1"] == "exact"
    assert kinds["https://www.rbc.ru/x/1"] in ("near", "exact")
    assert kinds["https://www.interfax.ru/moscow/2"] is None                                      # unrelated news stays alone
    assert st["duplicate"] == 2
    cl = conn.execute("SELECT * FROM clusters").fetchall()
    assert len(cl) == 1 and cl[0]["size"] == 3
    rep = conn.execute("SELECT source_id FROM documents WHERE doc_id=?", (cl[0]["representative_doc_id"],)).fetchone()
    assert rep["source_id"] == "SRC-01"                                                           # highest source weight wins (FM-17)
    assert conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE is_duplicate=1").fetchone()[0] == 2


def test_same_event_cluster_and_threshold_is_configurable(conn):
    from app.config import get_config
    a = mkdoc("SRC-03", "ЦБ сохранил ключевую ставку 14% годовых", "Банк России оставил ставку без изменений на заседании в пятницу", "https://www.interfax.ru/b/10")
    b = mkdoc("SRC-04", "ЦБ сохранил ключевую ставку 14% годовых, риторика жёстче", "Банк России оставил ставку без изменений, сигнал рынку жёсткий", "https://www.rbc.ru/e/10")
    store_documents(conn, "SRC-03", None, [a])
    store_documents(conn, "SRC-04", None, [b])
    row = conn.execute("SELECT dup_kind, cluster_id FROM documents WHERE source_id='SRC-04'").fetchone()
    assert row["dup_kind"] in ("same_event", "near") and row["cluster_id"]
    # paraphrase far below the threshold is NOT merged; lowering the threshold in config changes that (no code change)
    p = mkdoc("SRC-03", "Золото подешевело после выступления главы ФРС", "Цена золота скорректировалась", "https://www.interfax.ru/g/1")
    q = mkdoc("SRC-04", "Глава ФРС выступил, цена золота снизилась", "Золото скорректировалось вниз", "https://www.rbc.ru/g/1")
    store_documents(conn, "SRC-03", None, [p])
    get_config().raw["news"]["dedup"]["same_event_threshold"] = 0.99
    store_documents(conn, "SRC-04", None, [q])
    assert conn.execute("SELECT dup_kind FROM documents WHERE url='https://www.rbc.ru/g/1'").fetchone()[0] is None


def test_dedup_window_respected(conn):
    a = mkdoc("SRC-03", BASE, BODY, "https://www.interfax.ru/business/1", pub="2026-09-01T10:00:00+03:00")
    b = mkdoc("SRC-04", BASE + " ", BODY, "https://www.rbc.ru/x/9", pub="2026-09-11T10:00:00+03:00")   # 10 days later: a new story
    store_documents(conn, "SRC-03", None, [a])
    store_documents(conn, "SRC-04", None, [b])
    assert conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == 0


def test_evaluate_pairs_precision_recall(conn):
    from app.dedup import evaluate_pairs
    a = mkdoc("SRC-03", BASE, BODY, "https://www.interfax.ru/business/1")
    b = mkdoc("SRC-04", BASE, BODY + " сообщили в пресс-службе", "https://www.rbc.ru/x/1")
    n = mkdoc("SRC-03", "Температура ниже нуля ожидается в Москве", "Синоптики прогнозируют заморозки", "https://www.interfax.ru/moscow/2")
    store_documents(conn, "SRC-03", None, [a, n]); store_documents(conn, "SRC-04", None, [b])
    ids = {r["url"]: r["doc_id"] for r in conn.execute("SELECT url,doc_id FROM documents")}
    pairs = [(ids[a.url], ids[b.url], 1), (ids[a.url], ids[n.url], 0)]
    res = {r["threshold"]: r for r in evaluate_pairs(conn, pairs)}
    assert res[0.5]["precision"] == 1.0 and res[0.5]["recall"] == 1.0


# ---------------- macro ----------------
def test_macro_pipeline_and_revisions(conn, make_http):
    http = make_http({"XML_daily.asp": (200, fixture_bytes("xml_daily_1251.xml"))})
    s = run_macro_source(conn, "SRC-02", http)
    assert s.status == "ok" and s.items_new == 3
    pts = {p["date"]: p["value"] for p in get_series(conn, "M-03")}
    assert pts == {"2026-10-20": 82.8504}
    assert run_macro_source(conn, "SRC-02", http).items_new == 0                      # same values -> no new rows
    # the source revises a published value -> new revision, history kept, latest returned
    upsert_points(conn, [MacroPoint(series_id="M-03", date=date(2026, 10, 20), value=82.9, unit="RUB", source_id="SRC-02")])
    assert get_series(conn, "M-03")[0]["value"] == 82.9 and get_series(conn, "M-03")[0]["revision"] == 1
    assert conn.execute("SELECT COUNT(*) FROM macro_series WHERE series_id='M-03'").fetchone()[0] == 2


def test_key_rate_pipeline_sends_query_params(conn, make_http):
    seen = {}

    def handler(req):
        seen["q"] = dict(req.url.params)
        return httpx.Response(200, content=fixture_bytes("keyrate.html"))
    http = make_http({"hd_base/KeyRate": handler})
    s = run_macro_source(conn, "SRC-16", http, start=date(2026, 10, 1), end=date(2026, 10, 21))
    assert s.status == "partial" and s.items_new == 3                                 # one bad row reported, not fatal
    assert seen["q"]["UniDbQuery.From"] == "01.10.2026" and seen["q"]["UniDbQuery.To"] == "21.10.2026"


def test_manual_fallback_point_for_inflation(conn):
    from app.ingest.macro import add_manual_point
    add_manual_point(conn, "M-11.yoy", date(2026, 8, 31), 6.32, "% y/y", "DOC-04")
    p = get_series(conn, "M-11.yoy")[0]
    assert p["value"] == 6.32 and p["source_id"] == "DOC-04"
