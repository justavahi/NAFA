# NAFA ingestion library

Small reusable library for collecting RSS, HTML, JSON, XML, CSV and PDF data, normalizing it, and storing it in SQLite. Source URLs, selectors, field maps, CSV paths, XPath expressions, series mappings and weights are configured in `config/sources.yaml`, not hard-coded in Python.

## Install with uv

Install `uv` using the official method for your OS. On Linux, the standalone installer is:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart the shell or source the shell config, then from this project directory:

```bash
uv sync --group dev
cp .env.example .env
# Edit .env and set a real team contact in NAFA_USER_AGENT.
uv run --env-file .env nafa-ingest --config config/sources.yaml
```

Run just one source:

```bash
uv run --env-file .env nafa-ingest --config config/sources.yaml --source SRC-01
```

Run tests:

```bash
uv run pytest
```

`uv sync` creates `.venv/` and `uv.lock`. Commit `uv.lock` for reproducible installs. The SQLite file defaults to `data/nafa.sqlite3`.

## Library examples

```python
from nafa_ingest import load_config, ingest_all, ingest_source, search_documents
from nafa_ingest.fetch import fetch_url
from nafa_ingest.parsers import parse_rss, parse_csv, parse_html
from nafa_ingest.storage import init_db

# Run enabled sources from YAML.
results = ingest_all("config/sources.yaml")

# Reuse a connector and parser directly when building another part of the app.
settings, sources, config_dir = load_config("config/sources.yaml")
source = next(item for item in sources if item["id"] == "SRC-01")
body, final_url, content_type = fetch_url(source["url"], settings)
documents = parse_rss(body, source, settings, base_url=final_url)

# Search normalized news/documents later from another module.
news = search_documents(settings["database_path"], "ставка", limit=10)
```

## What is stored

- `documents`: normalized news and one record per PDF page, including original parser payload.
- `document_sources`: source provenance for exact URL duplicates.
- `macro_series`: normalized values keyed by series, date and source.
- `dataset_rows`: every CSV row as JSON, with missing values saved as `null`.
- `sources`: source configuration and last run timestamps.
- `ingestion_runs`: status/counts/errors per ingestion run.

CSV data is intentionally stored as JSON rows because the real input columns must come from the case files. This preserves source columns without inventing schemas; when column names are confirmed, typed client/payment tables can be added on top.

## Add a source

Add a new item to `sources` in YAML. For RSS, set `type: rss`, `url`, `id`, and optionally `fields`. For HTML, set `item_selector` and a `selectors` mapping; an attribute selector uses `selector@attribute`, e.g. `a.article@href`. For JSON, set `items_path` and `fields`. For generic XML, set `item_xpath` and `fields`. For CSV/PDF, configure a `path` or `path_glob`. For an XML macro series, configure `record_type: macro_series` plus its XPath mapping under `macro`.

Example HTML source:

```yaml
- id: SRC-EXAMPLE
  name: Example page
  type: html
  url: https://example.org/news
  enabled: false
  item_selector: "article.news-card"
  selectors:
    title: "h2 a"
    url: "h2 a@href"
    published_at: "time@datetime"
    snippet: ".summary"
```

Check each provider's usage terms, robots policy and rate limits before enabling a source. No proxy rotation or access-control bypass is implemented.
