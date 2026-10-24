# Parsing Sources

Runtime source registry: `config/sources.yaml`. Last successful fetch is populated dynamically by `GET /sources` from `ingestion_log`.

| ID | Source | Type | Default state | Notes |
|---|---|---|---|---|
| SRC-01 | Bank of Russia press releases | RSS | enabled | Verify at runtime; retain title/snippet/link |
| SRC-02 | CBR exchange rates | XML | enabled | USD/CNY/EUR mapped to M-03/M-04/M-05 |
| SRC-03 | Interfax | RSS | enabled | Verify URL, response and terms |
| SRC-04 | RBC | RSS | enabled | Verify URL and truncated-feed behavior |
| SRC-16 | CBR key rate | HTML | disabled | Enable only after selectors tested |
| SRC-CLIENTS / SRC-CLIENT-LINKS | Participant CSVs | CSV | disabled | Requires case files |
| SRC-PAYMENTS | Payment CSV | CSV | disabled | Requires case file |
| SRC-ALFA-DOC-* | Alfa files | PDF/images | disabled | Requires case corpus and VLM adapter |

A source is not marked verified merely because it appears in YAML. Check `GET /sources` and the latest ingestion records after a live run.
