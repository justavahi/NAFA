# Release verification record

- Date: 2026-10-25
- Test command: `pytest -q`
- Result: `77 passed`
- Generated handover artifacts: `01_parsing_sources.md`, `02_sources_descriptions.md`,
  `03_metrics_collected.md`, `04_corpus_verification.md`, `schema.sql`, `openapi.json`,
  `acceptance_matrix.md`, `alfa_corpus_processing.md`
- Live source checks: `check-sources` and `run-source --all` executed against the real P0
  endpoints. SRC-01 (14 items), SRC-02 (USD/CNY/EUR), SRC-03 (25 items), SRC-04 (30 items),
  SRC-16 (key-rate table, 21 rows). `robots=allowed` recorded for every P0 source.
- Macro history: `backfill-fx --start 2026-09-25 --end 2026-10-24` (30 days, 0 failed). The
  20.10.2026 official rates (USD 82,8504 / CNY 12,3869 / EUR 93,2316) match the case reference.
- Alfa corpus: 9 of the 10 registry documents supplied and parsed (DOC-01..DOC-09; 64 pages,
  214 chunks). DOC-10 is absent from the inputs and reported `missing` without substitution.
- Corpus key numbers: `corpus-verify-key` — 34 of 34 checked numbers match the stored chunks,
  0 discrepancies. Verified chunks are `verified_by=manual`.
- Bank position: `bankview-seed data/ref/bank_view_manual.csv` — for «ключевая ставка, конец
  2026» the active record is 14,0% (11.09.2026, DOC-07), the 13,5% records are `superseded`.
- Search: `search-alfa-eval` — 10 of 10 golden queries return the expected document in the top-3.
- Actual case client/payment CSV import: not run because the input files were not supplied;
  the synthetic generator covers the pipeline.
- VLM extraction: not run because no approved endpoint/key was configured. The corpus was
  built through the offline cached-extraction route and is labelled as such; re-running with the
  approved VLM is `corpus-parse --force` once credentials exist.
- Docker Compose startup and the LLM budget with real RUB prices were not exercised in this
  environment (no Docker daemon, no model credentials).

This is a reproducible development release with the implemented and live-verified behaviors
listed above. It is not a claim that every external P0 acceptance condition of the team's
Backend & Parsing specification has passed. See `acceptance_matrix.md` for the explicit
remaining evidence and blockers.