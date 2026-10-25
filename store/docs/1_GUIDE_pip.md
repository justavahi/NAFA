# NAFA Backend & Parsing — full guide (pip only)

Everything about installing, getting the database, running the services, generating the
deliverables and managing Docker — using only `python` + `pip`.
If you use `uv`, read `2_GUIDE_uv.md`. For the big picture and the order of steps read
`3_OVERVIEW_STEPS.md`.

> Verified in this build: the test suite (**77 passed**), every non-Docker command below, and the
> live sources (`cbr.ru`, `interfax.ru`, `rssexport.rbc.ru`) were actually run.
> **Not verified here:** the Docker commands (no Docker daemon in the build environment) and the
> approved LLM/VLM endpoint (no credentials). Windows/macOS command variants are given from
> standard behaviour, not executed here.

---

## 1. Requirements

| Need | Version | Check |
|---|---|---|
| Python | 3.11 or newer (verified on 3.13; the Docker image uses 3.12) | `python --version` |
| pip | any recent | `python -m pip --version` |
| Internet | only to install packages and to hit the live sources | |
| Docker (optional) | Docker Engine / Desktop with Compose v2 | `docker compose version` |

On Linux/macOS the interpreter is often `python3`.

---

## 2. Install

Open a terminal **in the project folder** (the one containing `app/`, `config/`,
`requirements.txt`).

```bash
python -m venv .venv                 # 1) isolated environment in .venv

source .venv/bin/activate            # 2) activate — Linux / macOS
# .venv\Scripts\Activate.ps1         #    Windows PowerShell
# .venv\Scripts\activate.bat         #    Windows cmd

python -m pip install --upgrade pip  # 3) install dependencies
python -m pip install -r requirements-dev.txt   # runtime + pytest
```

Runtime only (no pytest): `python -m pip install -r requirements.txt`.

Re-activate `.venv` in every new terminal. Your prompt shows `(.venv)` when it is active.

Self-check (uses only bundled fixtures, no internet):

```bash
python -m pytest -q                  # expect: 77 passed
```

---

## 3. Configuration (`.env`)

```bash
cp .env.example .env                 # Windows: copy .env.example .env
```

The program loads `.env` **automatically** from the project folder at start-up. A variable that
is already set in your shell always wins over `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `NAFA_DB_PATH` | `data/nafa.db` | SQLite file location. **Relative paths resolve against the project folder**, not your current directory. |
| `NAFA_DATA_DIR` | `data` | Folder for everything else: corpus page images, the extraction cache, synthetic CSVs. |
| `NAFA_CONTACT` | `team@example.invalid` | Your e-mail; sent inside the User-Agent to news sites (Interfax asks who is calling). **Set a real one before touching live sources.** |
| `NAFA_ADMIN_TOKEN` | *(none)* | Token for admin API calls (`POST /bank-view`). Without it those calls always return 401. |
| `NAFA_PSEUDONYM_SALT` | dev value | Secret used to pseudonymise client IDs in logs. Change it. |
| `LLM_BASE_URL`, `LLM_API_KEY` | empty | OpenAI-compatible endpoint from the organisers. Needed **only** by `corpus-parse` for a real VLM run. |

Everything except a real VLM run works with `.env` left at defaults.

---

## 4. Getting the database

There is **nothing to download**. The database is a single SQLite file the program creates
itself.

### 4.1 Where it is

Default: **`<project folder>/data/nafa.db`** (example:
`/home/you/nafa-backend/data/nafa.db`). Change it with `NAFA_DB_PATH`.

```bash
python -m app.cli db-info
```

prints the exact resolved path, whether it exists, its size and the row count of every table.

Next to it you will normally see:

```
data/nafa.db        the database
data/nafa.db-wal    write-ahead log (recent, not-yet-merged changes)
data/nafa.db-shm    shared-memory index
```

This is normal SQLite WAL mode. **Copy/back up all three together**, or use the safe online
backup in section 8.

### 4.2 How it is created

Every command creates the file and all tables automatically if they are missing. The explicit way:

```bash
python -m app.cli init-db
```

This applies `app/schema.sql` and loads the source registry (12 sources) and the macro-series
catalogue.

### 4.3 How it is filled

| Goal | Command | What it writes |
|---|---|---|
| Demo data, no internet | `python -m app.cli synth` | 8 synthetic archetype clients + fillers, links, payments and 8 synthetic news items. The CSVs are also written to `data/synthetic/`. |
| Bank position (reviewed) | `python -m app.cli bankview-seed data/ref/bank_view_manual.csv` | 22 records in `bank_view` with page+quote, `verified_by=manual`. Key rate end-2026: 14,0 % active, 13,5 % superseded. |
| Bank position (placeholder) | `python -m app.cli bankview-seed` | 5 records from `data/bank_view_seed.csv`, marked `verified_by=seed` — placeholders, verify against the PDFs. |
| Check live sources | `python -m app.cli check-sources` | robots.txt fact + test fetch + status per source. Needs internet. |
| Collect once | `python -m app.cli run-source SRC-01` (or `run-source --all`) | Documents / macro points. Needs internet. |
| Collect continuously | `python -m app.cli worker` | Polls every enabled source on its schedule (5–360 min). Keep it running. |
| Historical FX | `python -m app.cli backfill-fx --start 2026-09-25 --end 2026-10-24` | One request per day to `cbr.ru` → `M-03/M-04/M-05`. |
| Real client files | `python -m app.cli load-clients --uid df_Uid_data_hack.csv --links df_Fid_Uid_link_hack.csv --payments payments.csv` | Replaces `clients`, `client_links`, `payments`, writes a report to `load_reports`. Idempotent; `--force` to reload an identical file. |
| Alfa analytics corpus | `python -m app.cli corpus-parse ./data/input/alpha` | `alfa_docs`, `chunks`, `chunk_fts`, and page images in `data/corpus_pages/`. See section 6. |
| Verify key numbers | `python -m app.cli corpus-verify-key` | Marks checked chunks `verified_by=manual`; reports any mismatch. |
| Inflation by hand | `python -m app.cli macro-add --series M-11.yoy --date 2026-08-31 --value 6.32 --unit "% y/y" --source DOC-04` | One macro point (fallback when no Rosstat connector exists). |
| News you paste | `python -m app.cli byo --text "…"` or `--url https://www.cbr.ru/…` | One document through the same pipeline (`origin=byo`). |

A first offline session:

```bash
python -m app.cli init-db
python -m app.cli synth
python -m app.cli bankview-seed data/ref/bank_view_manual.csv
python -m app.cli db-info
python -m app.cli search-news "ключевая ставка" -k 3
```

A first online session (fills the same DB from real sources):

```bash
python -m app.cli check-sources
python -m app.cli run-source --all
python -m app.cli backfill-fx --start 2026-09-25 --end 2026-10-24
python -m app.cli corpus-parse ./data/input/alpha
python -m app.cli corpus-verify-key
python -m app.cli quality
```

> `synth` writes synthetic news (`origin='synthetic'`). Real news will sit beside it; filter with
> `WHERE origin <> 'synthetic'` or reset the DB (section 8).

---

## 5. Looking inside the database

**Easiest (no extra tools):**

```bash
python -m app.cli db-info
python -m app.cli quality                    # operational metrics Q-23..Q-27 (last 7 days)
python -m app.cli search-news "ставка" -k 5
python -m app.cli search-alfa "ключевая ставка" -k 5
python -m app.cli bankview-list --indicator key_rate --horizon end_2026
python -m app.cli corpus-report              # state of the Alfa corpus
```

**SQL with Python (works everywhere):**

```bash
python -c "import sqlite3; c=sqlite3.connect('data/nafa.db'); print(c.execute('select source_id,status,last_success_at from sources').fetchall())"
```

**SQL with the `sqlite3` shell** (preinstalled on macOS/most Linux; Windows: download from sqlite.org):

```bash
sqlite3 data/nafa.db
sqlite> .tables
sqlite> select source_id, title, published_at from documents order by published_at_utc desc limit 5;
sqlite> select series_id, date, value from macro_series where series_id='M-03' order by date desc limit 5;
sqlite> select indicator, horizon, value, as_of, version_rank, superseded_by from bank_view order by indicator, horizon, as_of desc;
sqlite> .quit
```

**GUI:** *DB Browser for SQLite* or the DBeaver SQLite driver can open `data/nafa.db` directly
(open read-only if the worker is running).

Useful queries:

```sql
-- health of each source
select source_id, status, last_success_at, last_error from sources;
-- last 20 collection attempts
select started_at, source_id, status, items_total, items_new, items_failed, error_type from fetch_runs order by started_at desc limit 20;
-- duplicate clusters
select c.cluster_id, c.size, d.title from clusters c join documents d on d.doc_id = c.representative_doc_id;
-- chunks still awaiting manual number check
select chunk_id, substr(text,1,80) from chunks where needs_manual_check = 1;
-- client load report (row counts, NULL share)
select table_name, rows_in_file, rows_loaded, null_share_json from load_reports;
-- today's LLM spend
select sum(cost_rub), sum(tokens_in+tokens_out) from llm_calls where day = date('now','+3 hours');
```

---

## 6. The Alfa corpus and the offline extraction cache

The 9 supplied analytics materials (`DOC-01…DOC-09`) are in `data/input/alpha/`. They are
**image-only PDFs** (no text layer), so text must be extracted.

`corpus-parse` resolves a page's text in this order:

1. **Approved VLM** — if `LLM_BASE_URL` / `LLM_API_KEY` are set, each page is sent to the model
   through the gateway and the answer is cached in `llm_cache`.
2. **Offline cached extraction** — otherwise it reads `data/corpus_extraction/<doc_id>/p<n>.json`
   (one file per page, the same JSON shape the VLM returns). This is the spec's *"cached by file
   hash, offline, once"* route. It is invalidated automatically when the source PDF changes.
3. **Local Tesseract OCR** — only if there is no cache entry. Requires the `tesseract` binary with
   the `rus`+`eng` packs (installed in the Docker image; not required for routes 1–2).

```bash
python -m app.cli corpus-parse ./data/input/alpha          # skips unchanged docs (file hash)
python -m app.cli corpus-parse ./data/input/alpha --force   # reprocess everything
python -m app.cli corpus-report
python -m app.cli corpus-verify-key                         # 34 key numbers vs stored chunks
```

`corpus-report` prints per-document status/chunk/verified counts. `corpus-verify-key` compares a
curated list (`data/ref/key_numbers_to_verify.csv`) against the stored numbers, marks matching
chunks `verified_by=manual` and reports any discrepancy (this build: **34/34 match, 0 discrepancies**).

To re-run with the real VLM once credentials exist: fill `LLM_*`, then
`corpus-parse ./data/input/alpha --force`.

---

## 7. Running the services

Two long-running processes; use two terminals (both with `.venv` active):

```bash
python -m app.cli serve               # API + docs at http://localhost:8000/docs  (Ctrl+C to stop)
python -m app.cli worker              # scheduled collection                     (Ctrl+C to stop)
python -m app.cli serve --port 8001   # if port 8000 is busy
```

They can run at the same time on the same DB file (WAL mode).

API endpoints (all `GET` unless noted): `/health`, `/clients`, `/clients/{id}/profile`,
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
python -m app.cli report --out docs/backend
```

writes, from the live DB: `docs/backend/01_parsing_sources.md`, `02_sources_descriptions.md`,
`03_metrics_collected.md`, `04_corpus_verification.md`.

### Search evaluation

```bash
python -m app.cli search-alfa-eval            # data/ref/alfa_search_golden.csv, top-3
python -m app.cli dedup-eval pairs.csv        # if the DA supplies labelled pairs
```

---

## 8. Backup, reset, move, change DB

**Safe backup while the worker runs** (SQLite online backup, includes the WAL):

```bash
python -c "import sqlite3; s=sqlite3.connect('data/nafa.db'); d=sqlite3.connect('data/backup_2026-10-25.db'); s.backup(d); d.close()"
```

**Reset to empty** (deletes all collected data and loaded clients!):

```bash
# stop serve and worker first (Ctrl+C)
rm data/nafa.db data/nafa.db-wal data/nafa.db-shm        # Windows: del data\nafa.db*
python -m app.cli init-db
```

For a full clean also delete `data/corpus_pages/` and `data/synthetic/`. The extraction cache
(`data/corpus_extraction/`) is committed on purpose — keep it if you want the offline corpus route.

**Move the DB:** stop everything, copy the three `nafa.db*` files, set `NAFA_DB_PATH` to the new
location.

**Use a different DB for one command** without touching `.env`:

```bash
NAFA_DB_PATH=/tmp/test.db python -m app.cli synth                 # Linux/macOS
$env:NAFA_DB_PATH="C:\temp\test.db"; python -m app.cli synth      # PowerShell
```

---

## 9. Docker (Docker needs neither Python nor pip on your machine)

Files: `Dockerfile` (Python 3.12-slim, installs `requirements.txt` **and** `tesseract-ocr` with
rus/eng packs), `docker-compose.yml`, `.dockerignore`.

### 9.1 What compose starts

| Service | Command | Role |
|---|---|---|
| `api` | `python -m app.cli serve` | HTTP API on port 8000 |
| `worker` | `python -m app.cli worker` | scheduled collection |

Both mount the named volume **`nafa-data`** at `/data`. Inside the containers the DB is
**`/data/nafa.db`** (compose sets `NAFA_DB_PATH`/`NAFA_DATA_DIR` itself, overriding `.env` for these
two). The volume survives restarts and rebuilds.

### 9.2 First start

```bash
cp .env.example .env              # required: compose reads it (env_file). Set NAFA_CONTACT, NAFA_ADMIN_TOKEN, LLM_*.
docker compose up -d --build
docker compose ps                 # both should be "running"
curl http://localhost:8000/health # {"status":"ok"}
```

`.env` is **not** baked into the image (`.dockerignore`); it is passed at run time.

### 9.3 Daily management

```bash
docker compose ps                         # status
docker compose logs -f worker             # follow collector log (Ctrl+C leaves it running)
docker compose logs -f api
docker compose logs --tail=100 worker
docker compose stop                       # stop containers, KEEP data
docker compose start
docker compose restart worker             # e.g. after changing config/ ... see 9.6
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

Containers stopped? Use `docker compose run --rm api python -m app.cli db-info`.

**Corpus in Docker.** The image contains the PDFs (`/app/data/input/alpha`) and Tesseract, but
`NAFA_DATA_DIR=/data`, so the offline cache must be copied into the volume if you want it:

```bash
docker compose cp data/corpus_extraction api:/data/corpus_extraction
docker compose exec api python -m app.cli corpus-parse ./data/input/alpha
docker compose exec api python -m app.cli corpus-verify-key
```

Without that copy, `corpus-parse` falls back to the image's Tesseract OCR (unverified text).

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

Then open `backup.db` with any SQLite tool (section 5), or point the CLI at it:
`NAFA_DB_PATH=backup.db python -m app.cli db-info`.

Where the volume physically is: `docker volume ls` (name looks like `<project>_nafa-data`) and
`docker volume inspect <name>`. On Docker Desktop (Windows/macOS) that path is inside a VM — use
`docker compose cp`, not the host path.

**Want the DB visible on your disk as `./data/nafa.db`?** Replace the named volume with a bind
mount in `docker-compose.yml` (both services):

```yaml
    volumes: ["./data:/data"]
```

and delete the `volumes: nafa-data:` block at the bottom. Caveat: SQLite WAL on bind mounts of
Docker Desktop (Windows/macOS) can give "database is locked"; Linux is fine. If it happens, go
back to the named volume.

### 9.6 Changing code or configuration

* `config/*.yaml` and the code are **inside the image**: after editing run
  `docker compose up -d --build`.
* `.env`-only changes: `docker compose up -d`.
* Host port: edit `ports: ["8000:8000"]` to e.g. `["8080:8000"]`.

### 9.7 Docker troubleshooting

| Symptom | Cause / fix |
|---|---|
| `env file .env not found` | Run `cp .env.example .env` first. |
| `port is already allocated` | Something uses 8000. Change the host port (9.6). |
| `database is locked` | Bind mount on Windows/macOS (9.5), or another process holds the file. Use the named volume. |
| Worker log shows `network` / `Name or service not known` | The container has no internet/DNS. Sources then show `failed` in `/sources`. |
| Data gone after restart | You ran `down -v`, or the DB was outside `/data`. `docker compose exec api python -m app.cli db-info` must show `/data/nafa.db`. |

---

## 10. Troubleshooting (local run)

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | You are not in the project folder. `cd` there and use `python -m app.cli …` (note the `-m`). |
| `ModuleNotFoundError: fastapi` (or another package) | Environment not active: run the activate command from section 2. |
| `python: command not found` | Use `python3`, or install Python 3.11+. |
| `sqlite3.OperationalError: database is locked` | Another process holds a long write; wait, and avoid two `load-clients` at once. |
| `/events/ingest` with a URL returns 403 | Host is not in `byo.allowed_domains` (`config/settings.yaml`) or robots.txt forbids it — by design. |
| `POST /bank-view` returns 401 | Send header `X-Admin-Token: <NAFA_ADMIN_TOKEN from .env>`. |
| `check-sources` shows `network` errors | No internet / DNS / proxy. Not a code problem. |
| `corpus-parse` reports `failed` with an OCR error | No VLM configured and no cache entry; install Tesseract + rus/eng packs, or use the offline cache, or set `LLM_*`. |
| Search returns nothing | The DB has no documents yet (`db-info` → `documents` = 0). Run `synth` or collect news. |

First diagnostic for almost anything: `python -m app.cli db-info` — it tells you which DB file you
are actually looking at.