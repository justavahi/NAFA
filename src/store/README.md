# NAFA — Backend & Parsing

Python 3.11+ backend for NAFA's data ingestion, normalization, provenance, search,
macro series, Alfa analytics corpus, SQLite storage and FastAPI access. The project is
mapped to [`02_TZ_backend_parsing.md`](https://github.com/justavahi/NAFA/blob/business-analysis/docs/02_TZ_backend_parsing.md).

> **Release honesty:** this is a tested development implementation, not a claim that every
> external acceptance criterion is already passed. Live P0 collection, macro backfill, the
> full supplied Alfa corpus, key-number verification and a 10-query search evaluation were
> executed (see `docs/backend/release_verification.md`). Still external: real participant CSV
> import, an approved VLM run (DOC-10 is absent from the inputs), approved model endpoints with
> real RUB prices, legal review of source terms, Docker clean-host build and the DA-owned
> formulas. See `docs/backend/acceptance_matrix.md`.

## Quick start (Python + pip)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

pytest -q
python -m app.cli init-db
python -m app.cli db-info
python -m app.cli corpus-report
python -m app.cli report --out docs/backend
python -m app.cli serve
```

API docs: `http://127.0.0.1:8000/docs`. Health check: `GET /health`.
Step-by-step guides in this folder: `docs/3_OVERVIEW_STEPS.md` (what to do, in order, and where the
DB lives), `docs/1_GUIDE_pip.md` (pip) and `docs/2_GUIDE_uv.md` (uv).

## Docker

```bash
cp .env.example .env
# Set NAFA_ADMIN_TOKEN and, if using the VLM, LLM_BASE_URL / LLM_API_KEY.
docker compose up --build
```

The container stores persistent state under `/data`. Run the first build on a machine
with network access to install OS/Python dependencies. Verify the clean-machine startup
criteria in `docs/backend/acceptance_matrix.md` before calling the deployment accepted.

## Included data

Nine of the ten registry analytics documents are under `data/input/alpha/`
(`DOC-01`..`DOC-09`; canonical copies of the materials in `analitics/`). They are image-only
PDFs; `corpus-parse` renders pages and extracts chunks. DOC-10 «Commodities performance» is not
in the inputs and is reported `missing` (no substitute is fabricated). Processing details,
including the offline cached-extraction route and the 34 verified key numbers, are in
`docs/backend/alfa_corpus_processing.md`. `data/corpus_extraction/<doc_id>/p<n>.json` holds the
per-page extraction cache that is re-used when no VLM endpoint is configured.

Live P0 sources (Bank of Russia RSS/XML/key-rate, Interfax, RBC) are fetched with an
identifying User-Agent under `robots.txt` rules; the DB is filled by `run-source --all` and
`backfill-fx`.

## Main commands

```bash
python -m app.cli init-db
python -m app.cli synth
python -m app.cli check-sources
python -m app.cli run-source --all
python -m app.cli backfill-fx --start 2026-09-25 --end 2026-10-24
python -m app.cli load-clients --uid clients.csv --links links.csv --payments payments.csv
python -m app.cli corpus-parse ./data/input/alpha
python -m app.cli corpus-report
python -m app.cli corpus-verify-key            # automatic reference-number match; does NOT mark human review
python -m app.cli bankview-seed data/ref/bank_view_manual.csv
python -m app.cli bankview-list --indicator key_rate --horizon end_2026
python -m app.cli search-alfa "ключевая ставка"
python -m app.cli search-alfa-eval             # 10-query golden set, top-3
python -m app.cli quality --days 7
python -m app.cli report --out docs/backend
```

`run-source --all`, `check-sources`, the VLM route and client import require the matching
network access, configuration or real source files. `synth` writes only synthetic test
data. Do not substitute it for the real participant data when producing an acceptance
report.

## API highlights

- `GET /health`
- `POST /events/ingest` — user-provided text or URL, subject to configured URL allow-list
- `GET /clients`, `GET /clients/{client_id}/profile`
- `GET /search/news`, `GET /search/alfa`
- `GET /series/{series_id}`
- `GET /bank-view`, `POST /bank-view` (admin token required for writes)
- `GET /quality/summary`, `GET /quality/ingestion`, `GET /budget`
- `GET /feed`, `POST /cards/generate`, `GET /cards/{card_id}`, `POST /cards/{card_id}/ask`,
  `POST /cards/{card_id}/feedback`, `GET /cards/{card_id}/trace`

The API schema is checked into `docs/backend/openapi.json` and can be regenerated from
`app.api:app`. Card generation is a deterministic/provisional baseline: it explicitly
labels where DA-owned scoring functions, a semantic critic, event calendar or validated
entity matcher are absent. It must not be presented as the final business ranking.

Merged PE integration: from the repository root use `python scripts/run_backend.py`.
It preserves existing card URLs and installs the PE generator/critic and dialogue.
It requires a trusted DA `CaseInput`; absent context returns `PE_CASE_NOT_READY`,
not an unverified fallback. See [backend handoff](../../docs/verification/backend_handoff.md).
Human PDF review is separate: only use `corpus-verify-key --confirm-reviewed` after
checking the original pages; mismatching chunks cannot be promoted by that command.

## Architecture overview

```text
config/sources.yaml -> source connectors -> normalized Document/MacroPoint
                                |-> dedup + event clusters
                                |-> SQLite + FTS5 + ingestion/quality trace
PDF/image corpus -> page images -> approved VLM if configured, else cached extraction, else unverified OCR
                                |-> chunk provenance + manual verification state
SQLite/FTS + typed read tools -> provisional card/API layer -> trace endpoints
```

- Configuration: `config/sources.yaml`, `config/settings.yaml`, `config/models.yaml`, `config/series.yaml`.
- Database schema: `app/schema.sql`; handover copy: `docs/backend/schema.sql`.
- Generated formal deliverables: `docs/backend/01_parsing_sources.md`,
  `docs/backend/02_sources_descriptions.md`, `docs/backend/03_metrics_collected.md`,
  `docs/backend/04_corpus_verification.md`.
- Release checklist and known blockers: `docs/backend/acceptance_matrix.md`.
- Verification record: `docs/backend/release_verification.md`.
- Tests: `tests/` (`pytest -q`, 77 tests in this release environment).
- License notices: `THIRD_PARTY_NOTICES` (especially review PyMuPDF AGPL/commercial terms).

## Production/acceptance blockers to resolve

1. Load the actual client/link/payment files and compare row counts, schemas and NULL reports
   (the pipeline, NULL handling and idempotency are implemented and tested on synthetic data).
2. Supply DOC-10 «Commodities performance» and re-run `corpus-parse`; run approved VLM
   extraction with `corpus-parse --force` once an endpoint is configured (the offline cached
   extraction is labelled as such).
3. Configure approved model names and actual RUB prices in `config/models.yaml`; confirm the
   budget guard with a real cache-hit and hard-limit scenario.
4. Have a human review the terms of use of each source (`legal.terms_checked_at` is still null;
   robots facts are recorded).
5. Build and start Docker Compose on a clean host and measure startup within 5 minutes.
6. Integrate DA-owned FM-06..FM-13 formulas and run the 8 archetype end-to-end acceptance
   tests. The current card scorer is intentionally a provisional fallback.
7. Finish the full security/prompt-injection and trace-coverage acceptance suite and UI
   work if no separate UI owner is responsible.

Re-measure Q-24 (ingestion latency) after scheduled polling has run for a few days; the
current window reflects a cold-start feed. Do not mark the whole role accepted until the
evidence in `docs/backend/acceptance_matrix.md` has been collected and reviewed.


## Merged application entrypoint (26 October 2026)

For the integrated PE/DA/UI application, use the root README and
`python scripts/run_backend.py --env-file .local/demo/runtime.env` from the repository root.
The historical `app.cli serve` / store-local Compose remain API-only baseline tools.
The root factory now provides automatic new-news extraction and DA context, FM-06..FM-13
feed ranking, verified generation/chat, session login, client isolation, a browser UI and
manual PDF review. The earlier integration/UI TODOs above describe the standalone store
release; current cross-component verification is in `../../docs/verification/merged_audit.md`.
Passing mocked integration tests does not establish live model quality or full acceptance.
