# NAFA Backend & Parsing — full guide (uv)

Everything about installing, getting the database, running the services, generating the
deliverables and managing Docker — using [uv](https://docs.astral.sh/uv/).
If you prefer plain pip, read `1_GUIDE_pip.md`. For the big picture and the order of steps read
`3_OVERVIEW_STEPS.md`.

> Verified in this build: the test suite (**77 passed**), every non-Docker command below, and the
> live sources (`cbr.ru`, `interfax.ru`, `rssexport.rbc.ru`) were actually run.
> **Not verified here:** the Docker commands (no Docker daemon in the build environment) and the
> approved LLM/VLM endpoint (no credentials). Windows/macOS command variants are from standard
> behaviour, not executed here.

---

## 1. Requirements

| Need | Notes |
|---|---|
| uv | `uv --version` |
| Python 3.11+ | uv can install it for you (`uv python install 3.12`); verified here on 3.13, Docker uses 3.12 |
| Internet | only to install packages and to hit the live sources |
| Docker (optional) | Docker Engine / Desktop with Compose v2: `docker compose version` |

Install uv if you do not have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh            # Linux / macOS
powershell -c "irm https://astral.sh/uv/install.ps1 | iex" # Windows
pip install uv                                            # any OS, if pip is already present
```

---

## 2. Install

The project ships `pyproject.toml` and `uv.lock` (exact pinned versions), so one command does
everything. Open a terminal **in the project folder** (containing `app/`, `config/`,
`pyproject.toml`).

```bash
uv sync                      # creates .venv, installs runtime + dev (pytest) from uv.lock
uv sync --no-dev             # runtime only
uv sync --python 3.12        # force a specific Python (uv downloads it if missing)
```

Then either run commands through uv (no activation needed):

```bash
uv run python -m pytest -q   # expect: 77 passed
uv run python -m app.cli db-info
```

or activate the environment and use plain `python` (shorter to type):

```bash
source .venv/bin/activate    # Linux / macOS
# .venv\Scripts\Activate.ps1 # Windows PowerShell
python -m app.cli db-info
```

The rest of this guide writes `uv run python -m app.cli …`. If you activated `.venv`, drop `uv run`.

Alternative that mimics pip (uses `requirements-dev.txt` instead of the lock file):

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements-dev.txt
```

Housekeeping: `uv lock` (refresh the lock after editing `pyproject.toml`), `uv add <pkg>`,
`uv sync --locked` (fail if the lock is stale). Keep `requirements.txt` in step with
`pyproject.toml` — **Docker builds from `requirements.txt`**.

---

## 3. Configuration (`.env`)

```bash
cp .env.example .env         # Windows: copy .env.example .env
```

The program loads `.env` **automatically** from the project folder (you do **not** need
`uv run --env-file`). A variable already set in your shell always wins over `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `NAFA_DB_PATH` | `data/nafa.db` | SQLite file location. **Relative paths resolve against the project folder**, not your current directory. |
| `NAFA_DATA_DIR` | `data` | Folder for everything else: corpus page images, extraction cache, synthetic CSVs. |
| `NAFA_CONTACT` | `team@example.invalid` | Your e-mail; sent inside the User-Agent to news sites. **Set a real one before touching live sources.** |
| `NAFA_ADMIN_TOKEN` | *(none)* | Token for admin API calls (`POST /bank-view`). Without it those calls always return 401. |
| `NAFA_PSEUDONYM_SALT` | dev value | Secret for pseudonymising client IDs in logs. Change it. |
| `LLM_BASE_URL`, `LLM_API_KEY` | empty | OpenAI-compatible endpoint from the organisers. Needed **only** by `corpus-parse` for a real VLM run. |

---

## 4. Getting the database

There is **nothing to download**. The database is a single SQLite file the program creates
itself.

### 4.1 Where it is

Default: **`<project folder>/data/nafa.db`**. Change with `NAFA_DB_PATH`.

```bash
uv run python -m app.cli db-info     # exact path, exists?, size, row count per table
```

Next to it you will see (normal, SQLite WAL mode):

```
data/nafa.db        the database
data/nafa.db-wal    write-ahead log
data/nafa.db-shm    shared-memory index
```

Copy/back up all three together, or use the safe online backup in section 8.

### 4.2 How it is created

Every command creates the file and all tables if missing. Explicitly:

```bash
uv run python -m app.cli init-db
```

This applies `app/schema.sql` and loads the source registry (12 sources) and macro-series
catalogue.

### 4.3 How it is filled

| Goal | Command | What it writes |
|---|---|---|
| Demo data, no internet | `uv run python -m app.cli synth` | 8 synthetic archetype clients + fillers, links, payments, 8 synthetic news items; CSVs also in `data/synthetic/`. |
| Bank position (reviewed) | `uv run python -m app.cli bankview-seed data/ref/bank_view_manual.csv` | 22 records in `bank_view`, page+quote, `verified_by=manual`; key rate end-2026 14,0 % active, 13,5 % superseded. |
| Bank position (placeholder) | `uv run python -m app.cli bankview-seed` | 5 records from `data/bank_view_seed.csv`, `verified_by=seed` — verify against the PDFs. |
| Check live sources | `uv run python -m app.cli check-sources` | robots.txt fact + test fetch + status per source. Needs internet. |
| Collect once | `uv run python -m app.cli run-source SRC-01` (or `--all`) | Documents / macro points. Needs internet. |
| Collect continuously | `uv run python -m app.cli worker` | Polls enabled sources on schedule (5–360 min). Keep running. |
| Historical FX | `uv run python -m app.cli backfill-fx --start 2026-09-25 --end 2026-10-24` | One request per day to `cbr.ru` → `M-03/M-04/M-05`. |
| Real client files | `uv run python -m app.cli load-clients --uid df_Uid_data_hack.csv --links df_Fid_Uid_link_hack.csv --payments payments.csv` | Replaces `clients`, `client_links`, `payments`; report in `load_reports`. Idempotent; `--force` to reload. |
| Alfa analytics corpus | `uv run python -m app.cli corpus-parse ./data/input/alpha` | `alfa_docs`, `chunks`, `chunk_fts`, page images in `data/corpus_pages/`. See section 6. |
| Verify key numbers | `uv run python -m app.cli corpus-verify-key` | Marks checked chunks `verified_by=manual`; reports mismatches. |
| Inflation by hand | `uv run python -m app.cli macro-add --series M-11.yoy --date 2026-08-31 --value 6.32 --unit "% y/y" --source DOC-04` | One macro point. |
| News you paste | `uv run python -m app.cli byo --text "…"` or `--url https://www.cbr.ru/…` | One document through the same pipeline. |

A first offline session:

```bash
uv sync
uv run python -m app.cli init-db
uv run python -m app.cli synth
uv run python -m app.cli bankview-seed data/ref/bank_view_manual.csv
uv run python -m app.cli db-info
uv run python -m app.cli search-news "ключевая ставка" -k 3
```

A first online session:

```bash
uv run python -m app.cli check-sources
uv run python -m app.cli run-source --all
uv run python -m app.cli backfill-fx --start 2026-09-25 --end 2026-10-24
uv run python -m app.cli corpus-parse ./data/input/alpha
uv run python -m app.cli corpus-verify-key
uv run python -m app.cli quality
```

> `synth` writes synthetic news (`origin='synthetic'`). Real news sits beside it; filter with
> `WHERE origin <> 'synthetic'` or reset the DB (section 8).

---

## 5. Looking inside the database

**Easiest:**

```bash
uv run python -m app.cli db-info
uv run python -m app.cli quality                 # operational metrics Q-23..Q-27 (last 7 days)
uv run python -m app.cli search-news "ставка"
uv run python -m app.cli search-alfa "ключевая ставка"
uv run python -m app.cli bankview-list --indicator key_rate --horizon end_2026
uv run python -m app.cli corpus-report
```

**SQL with Python:**

```bash
uv run python -c "import sqlite3; c=sqlite3.connect('data/nafa.db'); print(c.execute('select source_id,status,last_success_at from sources').fetchall())"
```

**SQL with the `sqlite3` shell** (macOS/most Linux; Windows: sqlite.org):

```bash
sqlite3 data/nafa.db
sqlite> .tables
sqlite> select source_id, title, published_at from documents order by published_at_utc desc limit 5;
sqlite> select series_id, date, value from macro_series where series_id='M-03' order by date desc limit 5;
sqlite> select indicator, horizon, value, as_of, version_rank, superseded_by from bank_view order by indicator, horizon, as_of desc;
sqlite> .quit
```

**GUI:** DB Browser for SQLite or DBeaver can open `data/nafa.db` (read-only while the worker runs).

Useful queries:

```sql
select source_id, status, last_success_at, last_error from sources;
select started_at, source_id, status, items_total, items_new, items_failed, error_type from fetch_runs order by started_at desc limit 20;
select c.cluster_id, c.size, d.title from clusters c join documents d on d.doc_id = c.representative_doc_id;
select chunk_id, substr(text,1,80) from chunks where needs_manual_check = 1;
select table_name, rows_in_file, rows_loaded, null_share_json from load_reports;
select sum(cost_rub), sum(tokens_in+tokens_out) from llm_calls where day = date('now','+3 hours');
```

---

## 6. The Alfa corpus and the offline extraction cache

The 9 supplied analytics materials (`DOC-01…DOC-09`) are in `data/input/alpha/`. They are
**image-only PDFs** (no text layer), so text must be extracted.

`corpus-parse` resolves a page's text in this order:

1. **Approved VLM** — if `LLM_BASE_URL` / `LLM_API_KEY` are set, each page goes to the model via
   the gateway; the answer is cached in `llm_cache`.
2. **Offline cached extraction** — otherwise it reads `data/corpus_extraction/<doc_id>/p<n>.json`
   (one file per page, same JSON shape the VLM returns). This is the spec's *"cached by file hash,
   offline, once"* route. It is invalidated automatically when the source PDF changes.
3. **Local Tesseract OCR** — only if no cache entry exists. Needs the `tesseract` binary with
   `rus`+`eng` packs (installed in the Docker image; not required for routes 1–2).

```bash
uv run python -m app.cli corpus-parse ./data/input/alpha          # skips unchanged docs
uv run python -m app.cli corpus-parse ./data/input/alpha --force  # reprocess everything
uv run python -m app.cli corpus-report
uv run python -m app.cli corpus-verify-key                        # 34 key numbers
```

`corpus-verify-key` compares `data/ref/key_numbers_to_verify.csv` against the stored numbers,
marks matching chunks `verified_by=manual` and reports discrepancies (this build: **34/34 match,
0 discrepancies**). To re-run with the real VLM once credentials exist: fill `LLM_*`, then
`corpus-parse ./data/input/alpha --force`.

---

## 7. Running the services

Two long-running processes, two terminals:

```bash
uv run python -m app.cli serve               # API + docs at http://localhost:8000/docs  (Ctrl+C to stop)
uv run python -m app.cli worker              # scheduled collection                     (Ctrl+C to stop)
uv run python -m app.cli serve --port 8001   # if 8000 is busy
```

They can run together on the same DB file (WAL mode).

API endpoints (`GET` unless noted): `/health`, `/clients`, `/clients/{id}/profile`,
`POST /events/ingest`, `/feed`, `/cards/generate`, `/cards/{id}`, `POST /cards/{id}/ask`,
`POST /cards/{id}/feedback`, `/cards/{id}/trace`, `/sources`, `/series/{id}`, `/bank-view`,
`POST /bank-view` (header `X-Admin-Token`), `/search/news?q=`, `/search/alfa?q=`,
`/quality/summary`, `/quality/ingestion`, `/budget`.

```bash
curl "http://localhost:8000/search/news?q=ключевая+ставка"
curl "http://localhost:8000/series/M-03"
curl -X POST http://localhost:8000/events/ingest -H "Content-Type: application/json" \
     -d '{"text":"Банк России повысил прогноз по инфляции на 2026 год."}'
```

### Generate the deliverable files

```bash
uv run python -m app.cli report --out docs/backend
```

writes `docs/backend/01_parsing_sources.md`, `02_sources_descriptions.md`,
`03_metrics_collected.md`, `04_corpus_verification.md` from the live DB.

### Search evaluation

```bash
uv run python -m app.cli search-alfa-eval        # data/ref/alfa_search_golden.csv, top-3
uv run python -m app.cli dedup-eval pairs.csv    # if the DA supplies labelled pairs
```

---

## 8. Backup, reset, move, change DB

**Safe backup while the worker runs:**

```bash
uv run python -c "import sqlite3; s=sqlite3.connect('data/nafa.db'); d=sqlite3.connect('data/backup_2026-10-25.db'); s.backup(d); d.close()"
```

**Reset to empty** (deletes all collected data and loaded clients!):

```bash
# stop serve and worker first (Ctrl+C)
rm data/nafa.db data/nafa.db-wal data/nafa.db-shm        # Windows: del data\nafa.db*
uv run python -m app.cli init-db
```

For a full clean also delete `data/corpus_pages/` and `data/synthetic/`; keep
`data/corpus_extraction/` if you want the offline corpus route.

**Move the DB:** stop everything, copy the three `nafa.db*` files, point `NAFA_DB_PATH` at the new
place.

**Throw-away DB for one command:**

```bash
NAFA_DB_PATH=/tmp/test.db uv run python -m app.cli synth                 # Linux/macOS
$env:NAFA_DB_PATH="C:\temp\test.db"; uv run python -m app.cli synth      # PowerShell
```

---

## 9. Docker (uv is not needed inside or for Docker)

Docker builds its own image with `pip` from `requirements.txt`; your local uv environment is
irrelevant to it. Files: `Dockerfile`, `docker-compose.yml`, `.dockerignore`.

### 9.1 What compose starts

| Service | Command | Role |
|---|---|---|
| `api` | `python -m app.cli serve` | HTTP API on port 8000 |
| `worker` | `python -m app.cli worker` | scheduled collection |

Both mount the named volume **`nafa-data`** at `/data`. Inside containers the DB is
**`/data/nafa.db`** (compose sets `NAFA_DB_PATH`/`NAFA_DATA_DIR`, overriding `.env` for these two).
The volume survives restarts and rebuilds.

### 9.2 First start

```bash
cp .env.example .env              # required: compose reads it. Set NAFA_CONTACT, NAFA_ADMIN_TOKEN, LLM_*.
docker compose up -d --build
docker compose ps                 # both "running"
curl http://localhost:8000/health # {"status":"ok"}
```

`.env` is not baked into the image (`.dockerignore`); it is passed at run time.

### 9.3 Daily management

```bash
docker compose ps
docker compose logs -f worker             # follow collector log (Ctrl+C leaves it running)
docker compose logs -f api
docker compose logs --tail=100 worker
docker compose stop                       # stop, keep containers + data
docker compose start
docker compose restart worker
docker compose down                       # remove containers, KEEP the volume (DB safe)
docker compose down -v                    # remove containers AND volume  => THE DATABASE IS DELETED
```

### 9.4 Run CLI commands inside the container

```bash
docker compose exec api python -m app.cli db-info
docker compose exec api python -m app.cli init-db
docker compose exec api python -m app.cli synth
docker compose exec api python -m app.cli run-source --all
docker compose exec api python -m app.cli bankview-seed data/ref/bank_view_manual.csv
docker compose exec api python -m app.cli corpus-report
docker compose exec api python -m app.cli quality
docker compose exec api python -m app.cli search-news "ставка" -k 3
```

Containers stopped? `docker compose run --rm api python -m app.cli db-info`.

**Corpus in Docker.** The image contains the PDFs (`/app/data/input/alpha`) and Tesseract, but
`NAFA_DATA_DIR=/data`, so copy the offline cache into the volume to use it:

```bash
docker compose cp data/corpus_extraction api:/data/corpus_extraction
docker compose exec api python -m app.cli corpus-parse ./data/input/alpha
docker compose exec api python -m app.cli corpus-verify-key
```

Otherwise `corpus-parse` falls back to the image's Tesseract OCR (unverified text).

**Load real client files** (copy into the volume first):

```bash
docker compose cp ./df_Uid_data_hack.csv api:/data/df_Uid_data_hack.csv
docker compose cp ./df_Fid_Uid_link_hack.csv api:/data/df_Fid_Uid_link_hack.csv
docker compose cp ./payments.csv api:/data/payments.csv
docker compose exec api python -m app.cli load-clients --uid /data/df_Uid_data_hack.csv --links /data/df_Fid_Uid_link_hack.csv --payments /data/payments.csv
```

### 9.5 Getting the DB out of Docker

```bash
docker compose exec api python -c "import sqlite3; s=sqlite3.connect('/data/nafa.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker compose cp api:/data/backup.db ./backup.db
```

Open `backup.db` with any SQLite tool (section 5), or point the CLI at it:
`NAFA_DB_PATH=backup.db uv run python -m app.cli db-info`.

Where the volume lives: `docker volume ls` (name looks like `<project>_nafa-data`),
`docker volume inspect <name>`. On Docker Desktop (Windows/macOS) that is inside a VM — use
`docker compose cp`.

**DB visible on your disk as `./data/nafa.db`?** Replace the volume with a bind mount in
`docker-compose.yml` (both services):

```yaml
    volumes: ["./data:/data"]
```

and delete the `volumes: nafa-data:` block at the bottom. Caveat: SQLite WAL on Docker Desktop
(Windows/macOS) bind mounts can give "database is locked"; Linux is fine.

### 9.6 Changing code or configuration

* `config/*.yaml` and the code are **inside the image**: after editing run
  `docker compose up -d --build`.
* `.env`-only changes: `docker compose up -d`.
* Host port: change `ports: ["8000:8000"]` to e.g. `["8080:8000"]`.
* Changed dependencies in `pyproject.toml`? Mirror them in `requirements.txt` — the image builds
  from that file.

### 9.7 Docker troubleshooting

| Symptom | Cause / fix |
|---|---|
| `env file .env not found` | `cp .env.example .env`. |
| `port is already allocated` | Change the host port (9.6). |
| `database is locked` | Bind mount on Windows/macOS (9.5); use the named volume. |
| Worker log: `network` / `Name or service not known` | No internet/DNS in the container; sources show `failed` in `/sources`. |
| Data gone after restart | You ran `down -v`, or the DB was outside `/data`. `docker compose exec api python -m app.cli db-info` must show `/data/nafa.db`. |

---

## 10. Troubleshooting (local run)

| Symptom | Fix |
|---|---|
| `uv: command not found` | Install uv (section 1) and reopen the terminal. |
| `No module named 'app'` | Run from the project folder, with `-m`: `uv run python -m app.cli …`. |
| `uv sync` says Python not found | `uv python install 3.12`, then `uv sync --python 3.12`. |
| `ModuleNotFoundError: fastapi` after activating `.venv` | Run `uv sync` once. |
| `database is locked` | Another process holds a long write; avoid two `load-clients` at once. |
| `/events/ingest` with a URL returns 403 | Host not in `byo.allowed_domains` (`config/settings.yaml`) or robots.txt forbids it — by design. |
| `POST /bank-view` returns 401 | Send header `X-Admin-Token: <NAFA_ADMIN_TOKEN>`. |
| `check-sources` shows `network` errors | No internet/DNS/proxy; not a code problem. |
| `corpus-parse` reports `failed` with an OCR error | No VLM configured and no cache entry; install Tesseract + rus/eng packs, use the offline cache, or set `LLM_*`. |
| Search returns nothing | DB has no documents (`db-info` → `documents` = 0). Run `synth` or collect news. |

First diagnostic for almost anything: `uv run python -m app.cli db-info` — it shows which DB file
you are actually using.