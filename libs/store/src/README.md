# NAFA Backend & Parsing (data layer)

Config-driven Python library for collection, normalization and SQLite persistence. It covers the data-ingestion slice of the backend spec: RSS documents, macro-series, local CSV imports, PDF text chunks, source registry, ingestion logs, FTS search and a minimal API.

## Run with uv

```bash
uv sync --group dev
cp .env.example .env
# Edit .env: use a real project/team contact in NAFA_USER_AGENT
uv run --env-file .env nafa-ingest --config config/sources.yaml
uv run --env-file .env pytest
uv run --env-file .env uvicorn app.main:app --reload
```

`data/nafa.sqlite3` is created automatically. `/docs` contains API docs; `/health`, `/sources`, `/news`, `/series/{series_id}`, `/quality/ingestion`, and POST `/ingestion/run` are provided.

## Configuration and examples

Source URLs, paths, update cadence, weights, enabled state, and XML mappings live in `config/sources.yaml`. Add another RSS source by copying an RSS entry and changing its ID/name/URL. Local client/payment/PDF sources remain disabled until the actual case files are placed in the configured paths and their names are confirmed.

Reusable code examples:

```python
from nafa_ingest import ingest_all, load_config, ingest_source, search_documents

# Ingest all enabled sources:
# result = ingest_all("config/sources.yaml")

# Ingest one configured source:
# settings, sources, config_dir = load_config("config/sources.yaml")
# source = next(s for s in sources if s["id"] == "SRC-01")
# result = ingest_source(source, settings, settings["database_path"], config_dir)

# Search normalized news with SQLite FTS5:
# docs = search_documents("data/nafa.sqlite3", '"ключевая ставка"', limit=10)
```

## Tables

- `documents`, `documents_fts`: normalized news and search index.
- `macro_series`: dated macroeconomic observations.
- `client_rows`, `payment_rows`: all source CSV columns retained as JSON; blanks remain null-like values, not zero.
- `chunks`: extracted document text per page, with manual-review flag.
- `bank_view`: source-attributed bank position and version history.
- `ingestion_log`: source/run status, counts, duplicates and timings.
- `metric_values`: extensible table for catalogued metric values.
- `trace_events`, `llm_usage`, `cards`: persistence contracts for the remaining agent/API layer.

## Important limits (do not confuse scaffolding with verified completion)

1. The case Google Drive files are not present in this repository, so client/payment imports and the eight analytical PDFs cannot be completed/validated until those files are supplied. Add their exact paths in YAML and enable the sources.
2. PDF parsing extracts text only. The spec requires page images + Qwen3-VL extraction, chart crops and manual verification. The schema supports chunks, but a model adapter, its runtime/model availability and validation set are still needed.
3. The HTML key-rate connector is disabled until selectors are checked. The XML parser is generic and needs source-specific verification; the CBR XML rate mapping is in YAML.
4. Legal conditions/robots.txt need to be verified from the actual deployment network. Do not assume permission to store full text.
5. Exact URL deduplication is implemented; near-duplicate and event clustering need labeled pairs and evaluation to claim the FM-17 precision/recall target.
6. This package is not the whole WP-B9..B17 agent/LLM/UI implementation. It implements a focused data layer and minimal ingestion API, not the complete agent backend.
