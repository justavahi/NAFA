# Backend & Parsing acceptance matrix

**Build checked:** 2026-10-25. **Automated tests:** `pytest -q` — 77 passed.
**Live data collected this run:** SRC-01/02/03/04/16 (`run-source --all`) + `backfill-fx` for 30 days.

This matrix separates implementation in code from acceptance evidence that requires
live data, external credentials, or human review. A passing unit test is not reported
as proof of a production-source SLA.

| WP | Deliverable | Current result | Remaining acceptance evidence / blocker |
|---|---|---|---|
| WP-B1 | Reproducible environment, Docker, config, health | Code and Docker files present; `/health`; pins + `uv.lock` included | Docker Compose was not built in this environment (no Docker daemon here). Build and time startup on a clean host; not asserted by this offline run |
| WP-B2 | Client/payment ingestion | Configurable CSV loader, NULL handling, load report, idempotency, synthetic generator | The actual case CSV files were not supplied. Load real files and compare row counts/schema/missing-value report to the originals |
| WP-B3 | RSS/XML news normalization | **Live run:** SRC-01 (14 items), SRC-03 (25), SRC-04 (30), SRC-02 (3 rates), SRC-16 (21 rows); 100% items parsed; every record has tz-aware `published_at` and `url`; 3 samples per source in `01_parsing_sources.md` | Freshness <24h holds at run time; keep polling to preserve it. `check-sources` recorded `robots=allowed` for all P0; terms-of-use review stays manual |
| WP-B4 | Exact/near/same-event dedup | Code, unit tests, clusters table; regression fixed | Q-11 precision/recall cannot be claimed until DA provides labelled pairs and `dedup-eval` meets thresholds |
| WP-B5 | Alfa corpus extraction | **9/10 documents parsed** (DOC-01..DOC-09, 64 pages, 214 chunks); each chunk has `doc_id`, `page`, `as_of_date`; **34 key numbers verified, 0 discrepancies** (`04_corpus_verification.md`); DOC-09 stale | DOC-10 «Commodities performance» is not present in the supplied inputs (reported `missing`, no substitute fabricated). ACCEPTED-vs-VLM: extraction is offline-cached review; re-run `corpus-parse --force` with the approved VLM when credentials exist |
| WP-B6 | Alfa FTS search | FTS5 + Russian inflection fallback; **10-query golden set: 10/10 expected documents in top-3** (`search-alfa-eval`) | Target is ≥8/10 — met. Re-check on the DA-owned final golden set |
| WP-B7 | BankView and versioning | `bank_view` seeded from the reviewed file; for «ключевая ставка, конец 2026» **active = 14,0% (11.09.2026, DOC-07 p.2); 13,5% (DOC-02/05) marked `superseded`**; `get_bank_view` reflects it | Records verified `verified_by=manual` with page+quote |
| WP-B8 | Macro series | **M-01/M-03/M-04/M-05 daily** via SRC-16/SRC-02; 20.10.2026 USD 82,8504 / CNY 12,3869 / EUR 93,2316 exactly match the official XML; M-11 weekly/monthly/yoy fallback from DOC-04 with source+date | P1 series (M-06/M-07 MOEX etc.) not implemented: `iss.moex.com/robots.txt` disallows crawling, so SRC-10 stays disabled; inflation remains a documented DOC-04 fallback |
| WP-B9 | LLM gateway | Unified gateway, retry/cache/token accounting/budget guard code and tests | Configure the approved endpoint and real RUB model prices in `config/models.yaml` (currently `0.0` = not priced). Real RUB budget acceptance stays blocked |
| WP-B10 | Read-only agent tools | Typed/allow-listed tool functions and tests | Integration review with the team's final tool schema and permissions remains required |
| WP-B11 | Agent orchestration | Deterministic card baseline, persistence and trace hooks | Explicitly provisional: full plan/context/critic retry and DA-owned FM-06..FM-13 are not in this package |
| WP-B12 | API/OpenAPI | FastAPI routes and generated `openapi.json`; tests | Re-run the API suite against a clean DB with the consuming UI |
| WP-B13 | BYO news | Text/URL route and domain allow-list pipeline | End-to-end acceptance with a genuinely unseen article and the final allow-list |
| WP-B14 | UI | Not included in this backend-only package | Deliver UI-01..UI-09 if no separate UI owner is assigned |
| WP-B15 | Trace/audit | Trace schema, card trace endpoint, write helper | Verify 100% of card-generation paths incl. errors/retries; current trace is partial for some deterministic branches |
| WP-B16 | Security | Secret-by-environment, URL/domain allow-list, read-only tools, tests | Full 20-case prompt-injection suite, role policy review, log inspection |
| WP-B17 | Synthetic data | Synthetic generator and fixtures | Run the synthetic smoke test through all 8 archetypes |
| WP-B18 | Operational metrics | `/quality/ingestion`: Q-23 = 1.0 success for all P0 sources; Q-24 latency recorded (cold-start window); Q-25/Q-26/Q-27 populated | Q-24 p95 ≤ 15 min is only meaningful once scheduled polling runs (a cold-start feed includes items published hours earlier); re-measure after a polling cycle |
| WP-B19 | Handover documents | README, source passports, metrics report, corpus verification, OpenAPI, schema, third-party notices, acceptance matrix | Legal terms still require a human review per source |
| WP-B20 | Monitoring metrics | Counters and quality summary exist | Full P1 Q-12..Q-31 coverage and the dashboard are not implemented |
| WP-B21 | Classic B0 prefilter | Russian lemmatization/stemming fallback supports retrieval | Full stop-words + lemmatized text + extractive summary + vector benchmark over 1,000 documents is not implemented |

## Verification commands

```bash
pytest -q
python -m app.cli init-db
python -m app.cli db-info
python -m app.cli check-sources               # robots + test fetch + samples (live)
python -m app.cli run-source --all            # live P0 collection
python -m app.cli backfill-fx --start 2026-09-25 --end 2026-10-24
python -m app.cli corpus-parse ./data/input/alpha --force
python -m app.cli corpus-report
python -m app.cli corpus-verify-key           # 34 key numbers vs stored chunks
python -m app.cli bankview-seed data/ref/bank_view_manual.csv
python -m app.cli bankview-list --indicator key_rate --horizon end_2026
python -m app.cli search-alfa-eval            # 10-query golden set, top-3
python -m app.cli quality --days 7
python -m app.cli report --out docs/backend   # regenerates 01..04
python -m app.cli dedup-eval path/to/labelled_pairs.csv
```

Commands requiring network, source credentials, live model credentials, Docker, or the actual
case CSV files must be run in the team environment. Capture their JSON/logs as additional
evidence; do not replace missing evidence with synthetic values.