# Alfa analytics corpus inputs

Canonical copies of the case analytics materials (the originals live in `analitics/`).
`app/ingest/alfa_corpus.py` maps the filenames to the registry IDs:

| Registry ID | File |
|---|---|
| DOC-01 | `DOC-01_FI_Strategy_3Q26_22072026.pdf` |
| DOC-02 | `DOC-02_ALFA-macro_stat1H26.pdf` |
| DOC-03 | `DOC-03_ALFA-consumption.pdf` |
| DOC-04 | `DOC-04_Inflation_tracker_02.pdf` |
| DOC-05 | `DOC-05_ALFA-7M26.pdf` |
| DOC-06 | `DOC-06_Gold_digest_August26.pdf` |
| DOC-07 | `DOC-07_ALFA-Macro_Insights-Policy_Rate_11Sep.pdf` |
| DOC-08 | `DOC-08_Бонд_Бриф_14092026.pdf` |
| DOC-09 | `DOC-09_ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf` |

DOC-10 «Commodities performance» is not in the supplied inputs and is reported as `missing`.

The PDFs are image-only (no text layer). When `LLM_BASE_URL` is configured, `corpus-parse`
uses the approved VLM (preferred path). Without a VLM endpoint the package consumes the
per-page cached extraction under `data/corpus_extraction/<doc_id>/p<n>.json` (this is the
"offline, cached by file hash, once" route), and falls back to local Tesseract OCR only if no
cache entry exists. Chunks are unverified until `corpus-verify-key` / manual review marks them
`verified_by='manual'`; chart estimates never become facts.

```bash
python -m app.cli corpus-parse ./data/input/alpha
python -m app.cli corpus-report
python -m app.cli corpus-verify-key
```

See `docs/backend/alfa_corpus_processing.md` for the full processing and verification record.