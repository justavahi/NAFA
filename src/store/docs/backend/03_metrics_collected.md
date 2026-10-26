# Metrics collected

_Generated. Covers the Backend-owned/co-owned metrics of metrics_catalog.csv._

| ID | Metric | Priority | Source / table | Frequency | Last value | Last date | Status |
|---|---|---|---|---|---|---|---|
| N-01 | Тип события | P0 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| N-03 | Сущности | P0 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| N-06 | Источник и его вес | P0 | documents.tier_weight / sources.tier_weight | on ingest |  |  | collected |
| N-07 | Свежесть | P0 | documents.published_at_utc (input; Fresh formula FM-09 is DA's function) | at ranking |  |  | input stored |
| N-08 | Новизна и кластер дубликатов | P0 | documents.cluster_id, clusters (exact/near/same_event) | on ingest |  |  | collected (cluster part; Nov needs client history) |
| N-10 | Горизонт и срочность | P1 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| N-13 | Покрытие позицией банка | P0 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| M-01 | Ключевая ставка ЦБ | P0 | macro_series via SRC-16 (cbr.ru key-rate table) | 6 h | 14.0 | 2026-10-23 | collected |
| M-02 | Ставка RUONIA | P1 | - | - |  |  | not implemented |
| M-03 | Курс USD/RUB (официальный ЦБ) | P0 | macro_series via SRC-02 XML_daily | 3 h | 80.5381 | 2026-10-24 | collected |
| M-04 | Курс CNY/RUB (ЦБ) | P0 | macro_series via SRC-02 XML_daily | 3 h | 12.0557 | 2026-10-24 | collected |
| M-05 | Курс EUR/RUB (ЦБ) | P1 | macro_series via SRC-02 XML_daily | 3 h | 90.2429 | 2026-10-24 | collected |
| M-06 | Доходности ОФЗ и индекс RGBI | P1 | - | - |  |  | not implemented |
| M-07 | Индекс МосБиржи IMOEX | P1 | - | - |  |  | not implemented |
| M-08 | Доходность корпоративных облигаций по рейтингу | P2 | - | - |  |  | not implemented |
| M-09 | Цена нефти Brent и Urals | P1 | - | - |  |  | not implemented |
| M-10 | Цена золота | P1 | - | - |  |  | not implemented |
| M-11 | Инфляция: недельная, месячная, годовая, базовая | P0 | macro_series M-11.weekly / M-11.mom / M-11.yoy: fallback from DOC-04 (no stable Rosstat API) | weekly / monthly | -0.01 | 2026-08-31 | collected (fallback source DOC-04) |
| M-12 | Инфляционные ожидания | P1 | - | - |  |  | not implemented |
| M-13 | Оборот розничной торговли | P1 | - | - |  |  | not implemented |
| M-14 | Рынок труда | P1 | - | - |  |  | not implemented |
| M-15 | Промпроизводство и ВВП (оперативно) | P2 | - | - |  |  | not implemented |
| M-16 | Кредитование юрлиц | P1 | - | - |  |  | not implemented |
| M-17 | Бюджет и заимствования | P1 | - | - |  |  | not implemented |
| M-18 | Цены на топливо | P1 | - | - |  |  | not implemented |
| M-19 | Внешний фон: ФРС, DXY, госдолг США | P2 | - | - |  |  | not implemented |
| M-20 | Цены на сталь и металлы | P2 | - | - |  |  | not implemented |
| M-21 | Интерес к темам (частота запросов) | P2 | - | - |  |  | not implemented |
| Q-12 | Задержка генерации карточки | P1 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-13 | Стоимость и бюджет токенов | P0 | llm_calls (tokens, RUB per call; /budget) | continuous |  |  | computed |
| Q-14 | Статистика агентного цикла | P1 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-15 | Безопасность | P0 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-16 | Полнота трассировки | P0 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-18 | Полезность по оценке пользователя | P1 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-19 | Доля уточняющих вопросов | P2 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-20 | Переходы к первоисточнику | P2 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-21 | Отклик на предложение банка | P2 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-22 | Вовлечённость на главном экране | P2 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-23 | Доля успешных парсингов | P0 | fetch_runs -> /quality/ingestion | continuous | 1.0000 | last 7 days | computed (7d) overall; per source: SRC-01=1.0; SRC-02=1.0; SRC-03=1.0; SRC-04=1.0; SRC-16=1.0 (target >=0.95) |
| Q-24 | Задержка доставки контента (Ingestion Latency) | P0 | ingestion_log (ready_at - published_at) -> /quality/ingestion | continuous | p95 23279 s | last 7 days | computed (7d) SRC-03: p50=15827s/p95=23279s(n=25); SRC-04: p50=11732s/p95=19110s(n=30) (cold-start feed; re-measure after polling) |
| Q-25 | Расход токенов по операциям и остаток лимита | P0 | llm_calls by operation -> /quality/ingestion | continuous | 0 in / 0 out | 2026-10-25 | computed (7d): 0.00 RUB spent; today remaining 500.00 RUB |
| Q-26 | Частота дубликатов и ошибок | P1 | ingestion_log -> /quality/ingestion | daily | dup 0.000 / err 0.000 | last 7 days | computed (7d) average over sources |
| Q-27 | Динамика обработки документов | P1 | ingestion_log, alfa_docs -> /quality/ingestion | daily | 69 | 2026-10-25 | computed (7d): documents received by day (classified stage is DA/PE) |
| Q-28 | Показатель трассируемости | P0 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
| Q-30 | Engagement Rate | P1 | - | - |  |  | agent layer (orchestrator/UI), not the data layer |
