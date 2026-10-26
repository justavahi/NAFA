# Alfa analytics corpus processing

**Checked:** 2026-10-25. **Input directory:** `data/input/alpha/`.
The source materials supplied for the case live in `analitics/`; canonical copies with the
registry `DOC-xx` prefix are kept in `data/input/alpha/` so `corpus-parse` is reproducible.

## Corpus state

Nine of the ten registry documents are supplied and parsed; DOC-10 is not present in the
inputs and is reported as `missing` (no substitute is fabricated).

| Registry ID | File | Pages | Chunks | Parse status | Notes |
|---|---|---:|---:|---|---|
| DOC-01 | `DOC-01_FI_Strategy_3Q26_22072026.pdf` | 27 | 85 | parsed | Appendix 2 slide deck (3Q26 debt-market strategy) |
| DOC-02 | `DOC-02_ALFA-macro_stat1H26.pdf` | 5 | 18 | parsed | macro statistics 1H26 |
| DOC-03 | `DOC-03_ALFA-consumption.pdf` | 4 | 18 | parsed | consumption |
| DOC-04 | `DOC-04_Inflation_tracker_02.pdf` | 3 | 11 | parsed | inflation tracker |
| DOC-05 | `DOC-05_ALFA-7M26.pdf` | 4 | 15 | parsed | macro statistics 7M26 |
| DOC-06 | `DOC-06_Gold_digest_August26.pdf` | 8 | 23 | parsed | gold market review (A2) |
| DOC-07 | `DOC-07_ALFA-Macro_Insights-Policy_Rate_11Sep.pdf` | 4 | 17 | parsed | key-rate insight |
| DOC-08 | `DOC-08_Бонд_Бриф_14092026.pdf` | 5 | 16 | parsed | Bond Brief |
| DOC-09 | `DOC-09_ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf` | 4 | 11 | parsed; `stale=1` | 2021 report (older than the staleness threshold) |
| DOC-10 | — | — | 0 | missing | "Commodities performance"; the file was not supplied |

Total: 9 documents, 64 pages, 214 chunks. Every chunk carries `doc_id`, `page` and
`as_of_date` (the document date, not an individual number's date).

## Extraction route

The literal spec route is PDF → page images → approved VLM (Qwen3-VL) → `Chunk`, cached by
file hash, offline, once. The code keeps that route:

- `parse_corpus` renders each page with PyMuPDF (`corpus.dpi` / `corpus.max_side_px`).
- If an approved VLM endpoint is configured (`LLM_BASE_URL`/`LLM_API_KEY`), every page is sent
  to it through the LLM gateway (`operation=pdf_parse`, `role=vlm`) and the result is cached in
  `llm_cache` by request hash.
- When no VLM endpoint is configured yet, the pipeline consumes the per-page cached extraction
  stored under `data/corpus_extraction/<doc_id>/p<n>.json` (the same JSON the VLM prompt
  returns). This is the "offline, cached by file hash, once" part of the spec and keeps the
  ingest path identical. The cache is invalidated automatically when the source file changes.
  `extraction_model` on these chunks is `offline-multimodal-review (cached; re-run with the
  approved VLM when configured)` — it is never presented as a Qwen3-VL run.
- If neither a VLM nor a cache entry is available, the code falls back to local Tesseract OCR
  (unverified), and a missing Tesseract / language pack produces an actionable error instead of
  silent garbage.

To re-run with the approved VLM once credentials exist:

```bash
# with LLM_BASE_URL / LLM_API_KEY configured, this overwrites the cache with real VLM output
python -m app.cli corpus-parse ./data/input/alpha --force
python -m app.cli corpus-report
```

## Verification of key numbers (WP-B5, RSK-08)

The 30-key-number acceptance is implemented as a reproducible check: a curated list
`data/ref/key_numbers_to_verify.csv` (chunk, expected value, subject) is compared against the
stored chunk numbers; verified chunks are marked `verified_by='manual'`,
`needs_manual_check=0`.

```bash
python -m app.cli corpus-verify-key            # uses data/ref/key_numbers_to_verify.csv
```

Result on this build: **34 of 34 listed key numbers match; 0 discrepancies** (rate, inflation,
FX forecasts, GDP, gold, OFZ/corporate yields). Generated evidence:
`docs/backend/04_corpus_verification.md`.

## Bank position (WP-B7, FM-16)

`data/ref/bank_view_manual.csv` holds manually reviewed records with page and quote. For
«ключевая ставка, конец 2026» the active record is 14,0% from 11.09.2026 (DOC-07, p.2); the
previous 13,5% records (DOC-02 31.07, DOC-05 07.09) are marked `superseded`.

```bash
python -m app.cli bankview-seed data/ref/bank_view_manual.csv
python -m app.cli bankview-list --indicator key_rate --horizon end_2026
```

## Safety of extracted data

Chart estimates and OCR output are unverified text and never create facts automatically;
numbers read from charts without printed labels are stored as `chart_estimate`. Do not promote a
financial value to `bank_view` until the page has been checked and the record is explicitly
`verified_by=manual`.

## Run / retry

```bash
python -m app.cli corpus-parse ./data/input/alpha          # skips unchanged documents by file hash
python -m app.cli corpus-parse ./data/input/alpha --force  # reprocess everything
python -m app.cli corpus-report
```

Failed and partial documents are retried on the next parse run; a complete document with an
unchanged file hash is skipped.