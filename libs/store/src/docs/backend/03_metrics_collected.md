# Metrics collected

| Metric ID | Source | Storage | Frequency | Status |
|---|---|---|---|---|
| M-03 USD/RUB | SRC-02 CBR XML | `macro_series` | daily on manual run; scheduler not wired | parser configured; live verification pending |
| M-04 CNY/RUB | SRC-02 CBR XML | `macro_series` | daily on manual run; scheduler not wired | parser configured; live verification pending |
| M-05 EUR/RUB | SRC-02 CBR XML | `macro_series` | daily on manual run; scheduler not wired | parser configured; live verification pending |
| M-01 key rate | SRC-16 CBR HTML / report fallback | `macro_series` / `chunks` | daily planned | disabled pending selector verification |
| Q-23 parsing success | ingestion log | `GET /quality/ingestion` | rolling 7 days | basic calculation |
| Q-24 ingestion latency | ingestion log durations | `GET /quality/ingestion` | rolling 7 days | processing duration proxy; publication-to-ready latency requires valid published timestamps |
| Q-26 duplicate/error counts | ingestion log | `GET /quality/ingestion` | rolling 7 days | basic calculation |
| Client/payment F/G metrics | case CSVs | raw JSON tables first | on import | not calculated until schemas and metric definitions are loaded |

Do not mark a metric as collected until its source is successfully fetched and its stored values are checked against the authoritative source.
