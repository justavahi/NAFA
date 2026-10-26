# ♡ News-AI-Financial-Assistant ♡
Financial assistant for AlfaBank made by *The Great New Alliance* :3

# Documentation
All explanations of business logic, architecture, etc. are in `docs` :D
(`docs/business`, `docs/backend-parsing`, `docs/prompt engeneering`, `docs/verification`).

## Verifying the merged version

Right now these are working prototype components; **full acceptance of the case is not complete yet**.
The Alfa-Bank requirements matrix, the defects that were found and the check results:
[docs/verification/merged_audit.md](docs/verification/merged_audit.md).

Code layout: `src/store` — API/sources/SQLite; `src/da_scripts` — features and calculations;
`src/report_scripts` — prompts, schemas, generation and the critic. Python 3.12, package manager `uv`.

All reproducible local checks, run from the repository root:

```bash
uv run --project src/store python scripts/verify_merged.py
```

This exercises the tests of all three parts, a real HTTP server startup, 8 archetypes × 6 roles,
the corpus and search. Remote LLMs are not called and no human labels are generated.
Reports go to `docs/verification/`; temporary databases and logs go to the ignored `.local/audit/`.
`full_case_acceptance=false` is not hidden even when the infrastructure tests are green.

## Running the whole project (main branch) — full guide

This walks through starting **everything** (Backend + DA + PE + web UI) from the `main` branch.
Every command below is run from the repository root unless stated otherwise.

### 0. Prerequisites

| Need | Why | Check |
| --- | --- | --- |
| `uv` (0.11.x) | installs and runs every Python component | `uv --version` |
| Python 3.12 | the pinned interpreter for the merged app | `uv python install 3.12` (uv can fetch it) |
| Internet | only to install dependencies and (optionally) to reach model/source APIs | — |
| Model credentials (optional) | required only for real AI generation | `LLM_BASE_URL`, `LLM_API_KEY` from the organisers |
| Docker + Compose v2 (optional) | the single-container way to run everything | `docker compose version` |

Install `uv` if you do not have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh            # Linux / macOS
powershell -c "irm https://astral.sh/uv/install.ps1 | iex" # Windows PowerShell
```

The merged application pins Python 3.12 (see `.python-version`). Let uv install it once:

```bash
uv python install 3.12
```

### 1. Get the code on `main`

```bash
git clone <repository-url> NAFA
cd NAFA
git checkout main
git pull --ff-only origin main
git branch --show-current       # should print: main
```

The Alfa-Bank case PDF (`case.pdf`) lives in the `main` branch.

### 2. Install dependencies

The merged app is driven by the Backend project (`src/store`). This single command creates that
virtual environment and installs the runtime + dev (pytest) dependencies from the lock file:

```bash
uv sync --project src/store --group dev --frozen
```

No activation is needed — every later command uses `uv run --project src/store …`.

If you also want to work on the Prompt-Engineer or DA packages on their own:

```bash
uv sync --project src/report_scripts --group dev --frozen
uv sync --project src/report_scripts --extra backend --frozen
```

### 3. Prepare a runtime (synthetic demo)

This creates an isolated, self-contained demo instance (its own SQLite DB, pseudonym salt,
admin token and demo client tokens). **It never calls any model** — all data is synthetic:

```bash
uv run --project src/store --with matplotlib==3.11.2 --with tabulate==0.10.0 \
  python scripts/prepare_runtime.py --demo
```

What it produces (all inside the ignored `.local/demo/`):

- `nafa.db` — the demo database with 8 synthetic companies, DA aggregates and news;
- `runtime.env` — private credentials (`NAFA_ADMIN_TOKEN`, `NAFA_CLIENT_TOKENS`, paths);
- `data/` — generated CSVs and processed artifacts;
- `prepare.log` — the preparation log (kept private).

The command prints the generated `runtime.env` path and the exact `run_backend.py` line to start.
If `.local/demo/` already exists it stops instead of overwriting; either reuse it or pass a new
`--output` to build a separate snapshot.

### 4. Provide model credentials (optional)

The feed and company profiles work **without** an API key. Real AI generation, extraction and
chat need the organisers' OpenAI-compatible endpoint. Keep these secrets in a private file
(never commit them) and pass it as a second `--env-file`:

```bash
cat > .local/models.env <<'EOF'
LLM_BASE_URL=<organiser-endpoint-url>
LLM_API_KEY=<your-team-key>
EOF
chmod 600 .local/models.env
```

`.local/` is git-ignored. Without credentials, generation ends with a clear error message
instead of a fabricated card.

### 5. Start the backend

```bash
uv run --project src/store python scripts/run_backend.py \
  --env-file .local/demo/runtime.env
```

With credentials too:

```bash
uv run --project src/store python scripts/run_backend.py \
  --env-file .local/demo/runtime.env --env-file .local/models.env
```

Details and useful flags:

- default host/port: `127.0.0.1:8000` (change with `--host` / `--port`);
- `--mode baseline` starts the older rule-based provisional generator (`publishable=false`) —
  it must not be presented as a final AI card;
- leave the process running; press `Ctrl+C` to stop.

Once it is up: `http://127.0.0.1:8000` (the web app) and `http://127.0.0.1:8000/docs`
(the OpenAPI schema).

### 6. Open the app and log in

Open `http://127.0.0.1:8000` in a browser. The login page lists the **demo companies** with
their demo tokens (only shown when `NAFA_DEMO_MODE=true`). Pick a company, then choose one of the
six roles (personas `PER-01`…`PER-06`) and an event from the feed, or paste your own news.

A company can only ever see its own data — one demo login equals one synthetic company.

### 7. Working in the UI

The workspace fits a single screen: the feed on the left, the selected event on the right;
sources, calculations and evidence live in tabs. On a phone you can switch between the feed and
the card. The card shows facts, the bank's position, the AI conclusion, a follow-up question,
scenario calculations, feedback and the trace.

The **“Analyse impact”** button runs the AI separately on the selected event: browsing the feed
**does not spend model calls**. Answers in the demo are labelled as demonstration output and are
not counted as published working cards.
[UI redesign vs. the requirements](docs/verification/ui_redesign.md).

### 8. Load the bank analytics corpus (admin, optional)

Import the shipped offline extraction cache — no network calls:

```bash
uv run --project src/store python scripts/prepare_corpus.py --env-file .local/demo/runtime.env
```

Then log in as administrator (use `NAFA_ADMIN_TOKEN` from `.local/demo/runtime.env`) and open the
review queue at the bottom of the page. Compare the text and numbers against the original PDF,
fix mistakes and confirm each fragment. Only manually reviewed fragments are used as the bank's
position; without them the app reports that no confirmed position exists.

The cache does **not** replace acceptance of the approved VLM; `DOC-10` is absent from the
supplied files.

### 9. Alternative: run everything in Docker

The root `Dockerfile` and `compose.yaml` include Backend, DA, PE and the UI.

```bash
# Optional: point Compose at your model credentials (defaults to ./.env)
export NAFA_MODEL_ENV=.local/models.env
# Optional: expose the app on another host port (defaults to 8000)
export NAFA_PORT=8000

docker compose up --build
```

Compose starts two services:

- `init` — runs `prepare_runtime.py --demo` once into the `runtime` volume;
- `nafa` — the app, published on `${NAFA_PORT:-8000}` and healthchecked at `/health`.

The supplied PDFs (`src/store/data/input/alpha`) are mounted read-only. Switch runtimes by
changing the mounted `runtime` volume contents / `NAFA_MODEL_ENV`.

> Note: a Docker daemon was not available in the verification environment, so the Compose
> configuration is validated but the container build/run is not yet confirmed.

### 10. Alternative: real participant data instead of the demo

Prepare a runtime from the real participant CSVs instead of `--demo`:

```bash
uv run --project src/store --with matplotlib==3.11.2 --with tabulate==0.10.0 \
  python scripts/prepare_runtime.py --data-dir /absolute/path/to/participant-data
```

This creates `.local/participant/runtime.env` with a separate DB, a persistent pseudonym salt and
an admin token. To log in:

- **admin** — use `NAFA_ADMIN_TOKEN` from that file;
- **clients** — set `NAFA_CLIENT_TOKENS`, a JSON map like `{ "secret-code": "company-id" }`.

A client sees only its own company, cards and submitted news. Never commit secrets or data to Git.
Re-running preparation preserves the existing runtime; pass a different `--output` for a new snapshot.

### 11. Run the verification suite

```bash
# all reproducible checks (tests, real TCP startup, profiles, corpus, DA pipeline)
uv run --project src/store python scripts/verify_merged.py

# optional real-browser check of the whole user journey
uv run --project src/store --group dev --with playwright python scripts/check_browser.py
```

The browser check needs a prepared demo runtime and Chrome or Chromium
(`playwright install chromium`). It covers login, six roles, the feed, your own news, the card,
the question, a calculation, feedback, the trace, the mobile screen and logout — using the real
APIs and DA/PE but **controlled model responses**. The report is
`docs/verification/browser_check.json`; it is not an assessment of real LLM quality.

### Quick troubleshooting

| Symptom | Fix |
| --- | --- |
| `uv: command not found` | Install `uv` (step 0) and reopen the terminal. |
| `Runtime already exists …` | Reuse `.local/demo/runtime.env`, or pass a new `--output`. |
| Generation returns an error | No `LLM_BASE_URL`/`LLM_API_KEY`; add `.local/models.env` (step 4). |
| `port is already allocated` / `8000` busy | Start with `--port 8001`, or change `NAFA_PORT` for Docker. |
| The page shows no demo companies | The runtime was built without `--demo`, or `NAFA_DEMO_MODE` is not `true`. |
| `pipeline`/`pandas` import errors | Re-run `uv sync --project src/store --group dev --frozen` (step 2). |

## Bank analytics

The admin-only corpus flow is described in step 8 above. In short: import the cached extraction,
log in as administrator, review the queue by hand and confirm fragments. Only human-verified
fragments become the bank's position; otherwise the app reports that no confirmed position exists.
The cache does not replace acceptance of the approved VLM, and `DOC-10` is missing from the
supplied files.

## How the parts are connected

```bash
uv sync --project src/store --group dev --frozen
# Configure src/store/.env: see src/store/.env.example
python scripts/run_backend.py
```

API: `http://127.0.0.1:8000/docs`. You need the URL/key of the approved model API, `NAFA_ADMIN_TOKEN`
and a stable `NAFA_PSEUDONYM_SALT`. Models/sources/thresholds are configured under `src/store/config/`;
the PE schemas are under `src/report_scripts/schemas/`.

A new news item automatically goes through PRM-01 (Gemma), the existing DA calculations,
PRM-05/06 (GLM) and an independent PRM-07 (Qwen). Event extraction is cached between companies;
raw payments and IDs are never passed to the model. The feed uses FM-06…FM-13 and MMR; before the
factors are extracted the ordering is provisional. An unknown effect sign stays conditional, with
no invented calculation.

For trusted prepared inputs, `POST /internal/pe/cases` is preserved;
[API contract](docs/verification/backend_handoff.md). No manual registration is needed for the
standard path with imported, up-to-date DA aggregates.
`python scripts/run_backend.py --mode baseline` runs the older rule-based provisional generation,
explicitly with `publishable=false`; it must not be presented as the final AI card.

## Container and browser check

The root `Dockerfile` and `compose.yaml` include Backend, DA, PE and the UI. First prepare a
runtime (step 3), add the model variables to its private `runtime.env`, then run
`docker compose up --build`. By default `.local/demo` is mounted; for another runtime set
`NAFA_RUNTIME_DIR` and `NAFA_RUNTIME_ENV`. PDFs are mounted separately, read-only.
A Docker daemon was not available on the verification machine: the Compose configuration is valid,
but the container build and run are not yet confirmed.

```bash
uv run --project src/store --group dev --with playwright python scripts/check_browser.py
```

The browser test requires a prepared demo runtime and Chrome or Chromium
(`playwright install chromium`). It checks login, the six roles, the feed, your own news, the card,
the question, a calculation, feedback, the trace, the mobile screen and logout. It uses the real
APIs and DA/PE, but controlled model responses. The report is
`docs/verification/browser_check.json`; it is not an assessment of real LLM quality.

Before submission, the remaining items are human evaluations, a live run of the current profile,
PDF/tariff/latency verification and the container. Infrastructure tests do not prove a score of
4.5 or full compliance with the case.

# Roles ┌(;￣◇￣)┘
|      Nickname      |        Role       |
|--------------------|-------------------|
| poluektovnikolai07 |  Business Analyst |
|     justavahi      | Backend & Parsing |
|     PhantasmT      |    Data Analyst   |
|    roreternity     |   Prompt Engeener |

Informally *justavahi* also is the *Architecure Designer* so please ask him freely what to do if something feels senseless ૮₍˶•⤙•˶₎ა

# How to work ( ˘▽˘)っ 
- Every contributer has to work in his own branch 
- please DON'T merge any branch with any branch else before everyone finishes their work
- If a contributer needs to push anything in the *main* branch, he has to discuss it with *The Great New Alliance* in advance. Alfa Bank's pdf file is in the *main* branch  
- *Prepared tasks* in branches for each contributer are just *recomendations*. If a contributer isn't agree, he may do *whatever* he considers right ;(
