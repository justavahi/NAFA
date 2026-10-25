-- NAFA storage schema. SQLite (WAL) holds app state; FTS5 gives BM25 search without embeddings (RSK-03).
-- Timestamps: ISO-8601 TEXT. *_utc columns are 'YYYY-MM-DDTHH:MM:SSZ' so string order == time order.

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  name TEXT, owner TEXT, type TEXT, url TEXT, priority TEXT,
  tier_weight REAL, enabled INTEGER NOT NULL DEFAULT 1,
  parser TEXT, schedule_minutes INTEGER,
  legal_json TEXT, config_json TEXT,
  status TEXT NOT NULL DEFAULT 'not_checked',      -- works | partial | failed | not_checked | disabled
  status_checked_at TEXT, last_success_at TEXT, last_error TEXT
);

-- ---------- news / analytics documents (spec 4.2 Document) ----------
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,                          -- sha1(canonical_url)
  source_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'news',
  origin TEXT NOT NULL DEFAULT 'feed',              -- feed | byo | synthetic
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  published_at TEXT NOT NULL,                       -- with UTC offset
  published_at_utc TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  language TEXT, category TEXT, snippet TEXT, text TEXT,
  tier_weight REAL NOT NULL,
  content_hash TEXT NOT NULL,
  cluster_id TEXT, dup_of TEXT, dup_kind TEXT,      -- dup_kind: exact | near | same_event | NULL
  stale INTEGER NOT NULL DEFAULT 0,
  ready_at TEXT                                     -- when ready for ranking (Q-24)
);
CREATE INDEX IF NOT EXISTS ix_documents_pub ON documents(published_at_utc);
CREATE INDEX IF NOT EXISTS ix_documents_src ON documents(source_id, published_at_utc);
CREATE INDEX IF NOT EXISTS ix_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS ix_documents_cluster ON documents(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id TEXT PRIMARY KEY,
  representative_doc_id TEXT NOT NULL,              -- chosen by source weight (FM-17)
  size INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(doc_id UNINDEXED, text);

-- ---------- operational logs (WP-B18: Q-23..Q-27) ----------
CREATE TABLE IF NOT EXISTS fetch_runs (
  run_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  http_status INTEGER, attempts INTEGER,
  items_total INTEGER NOT NULL DEFAULT 0, items_ok INTEGER NOT NULL DEFAULT 0,
  items_failed INTEGER NOT NULL DEFAULT 0, items_duplicate INTEGER NOT NULL DEFAULT 0,
  items_new INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,                             -- ok | partial | failed
  error_type TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_fetch_runs ON fetch_runs(source_id, started_at);

CREATE TABLE IF NOT EXISTS ingestion_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, source_id TEXT NOT NULL, doc_id TEXT,
  published_at TEXT, fetched_at TEXT NOT NULL, ready_at TEXT,
  status TEXT NOT NULL,                             -- ready | duplicate | error
  error_type TEXT,
  is_duplicate INTEGER NOT NULL DEFAULT 0,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_ingestion_src ON ingestion_log(source_id, fetched_at);

-- ---------- macro series (spec 4.5) ----------
CREATE TABLE IF NOT EXISTS macro_series (
  series_id TEXT NOT NULL,
  date TEXT NOT NULL,                               -- YYYY-MM-DD (effective date of the value)
  value REAL NOT NULL,
  unit TEXT, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,              -- grows only when the source changes a published value
  PRIMARY KEY (series_id, date, revision)
);

CREATE TABLE IF NOT EXISTS series_catalog (
  series_id TEXT PRIMARY KEY, name TEXT, unit TEXT, source_id TEXT, frequency TEXT, priority TEXT
);

-- ---------- Alfa-Bank corpus (spec 4.3 Chunk) ----------
CREATE TABLE IF NOT EXISTS alfa_docs (
  doc_id TEXT PRIMARY KEY,                          -- DOC-01..DOC-10
  title TEXT, doc_date TEXT, format TEXT, registry_status TEXT,
  stale INTEGER NOT NULL DEFAULT 0,
  file_hash TEXT, source_files TEXT, pages INTEGER,
  parse_status TEXT NOT NULL DEFAULT 'missing',     -- missing | parsed | partial | failed
  parsed_at TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,                        -- DOC-07-p1-b3
  doc_id TEXT NOT NULL, page INTEGER NOT NULL,
  block_type TEXT NOT NULL,                         -- text | table | chart_caption | chart_estimate
  text TEXT NOT NULL,
  numbers_json TEXT,
  as_of_date TEXT,
  extraction_model TEXT, extraction_confidence REAL,
  needs_manual_check INTEGER NOT NULL DEFAULT 1,
  verified_by TEXT,                                 -- manual | NULL
  image_path TEXT                                   -- page or chart crop for UI attribution
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id, page);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED, text);

-- ---------- bank position (spec 4.4 BankView, FM-16) ----------
CREATE TABLE IF NOT EXISTS bank_view (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  indicator TEXT NOT NULL, horizon TEXT NOT NULL,
  value REAL, unit TEXT, stance TEXT,
  as_of TEXT NOT NULL,
  doc_id TEXT, page INTEGER, quote_span TEXT,
  version_rank INTEGER NOT NULL DEFAULT 0,          -- 0 = active
  superseded_by INTEGER,
  extraction_method TEXT NOT NULL DEFAULT 'text',   -- text | chart_estimate
  verified_by TEXT NOT NULL DEFAULT 'auto',         -- manual | auto | seed
  note TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(indicator, horizon, as_of, doc_id, value)
);

-- ---------- client data (Appendix 1). NULL stays NULL, never 0. ----------
CREATE TABLE IF NOT EXISTS clients (
  synthetic_u TEXT PRIMARY KEY,
  mainokved_code TEXT, mainokved_name TEXT,
  revenue_company REAL,
  amount_workers_fns_gradation TEXT, amount_workers_spark_gradation TEXT,
  cl_product_rko_mark TEXT, cl_product_gar_mark TEXT, cl_product_loa_mark TEXT,
  cl_product_lea_mark TEXT, cl_product_dep_mark TEXT, cl_product_zpp_mark TEXT,
  factoring_mark TEXT,
  fts_flag TEXT, fts_country_name TEXT,
  group_id TEXT, city TEXT, region TEXT, cl_type TEXT,
  company_age_years_gradation TEXT, eco_mark_label TEXT, okfs_name TEXT, org_type TEXT,
  negativemark TEXT,
  extra_json TEXT,                                  -- unexpected columns are kept, not dropped
  loaded_at TEXT
);

CREATE TABLE IF NOT EXISTS client_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  synthetic_f TEXT, synthetic_u TEXT, city_name TEXT, rc_name TEXT
);
CREATE INDEX IF NOT EXISTS ix_links_u ON client_links(synthetic_u);
CREATE INDEX IF NOT EXISTS ix_links_f ON client_links(synthetic_f);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payer TEXT, beneficiary TEXT, pay_amt REAL, pay_cnt REAL
);
CREATE INDEX IF NOT EXISTS ix_pay_payer ON payments(payer);
CREATE INDEX IF NOT EXISTS ix_pay_benef ON payments(beneficiary);

CREATE TABLE IF NOT EXISTS load_reports (
  load_id TEXT PRIMARY KEY,
  table_name TEXT NOT NULL, file_name TEXT, file_hash TEXT,
  rows_in_file INTEGER, rows_loaded INTEGER,
  null_share_json TEXT, quality_json TEXT, warnings_json TEXT,
  status TEXT NOT NULL, loaded_at TEXT NOT NULL
);

-- Flags normalised for convenience; unknown spellings and NULL -> NULL (not "no product").
CREATE VIEW IF NOT EXISTS v_clients_flags AS
SELECT synthetic_u,
  CASE WHEN upper(trim(cl_product_rko_mark)) IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(cl_product_rko_mark)) IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS rko,
  CASE WHEN upper(trim(cl_product_gar_mark)) IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(cl_product_gar_mark)) IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS gar,
  CASE WHEN upper(trim(cl_product_loa_mark)) IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(cl_product_loa_mark)) IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS loa,
  CASE WHEN upper(trim(cl_product_lea_mark)) IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(cl_product_lea_mark)) IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS lea,
  CASE WHEN upper(trim(cl_product_dep_mark)) IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(cl_product_dep_mark)) IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS dep,
  CASE WHEN upper(trim(cl_product_zpp_mark)) IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(cl_product_zpp_mark)) IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS zpp,
  CASE WHEN upper(trim(factoring_mark))      IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(factoring_mark))      IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS factoring,
  CASE WHEN upper(trim(fts_flag))            IN ('1','Y','YES','TRUE','ДА') THEN 1 WHEN upper(trim(fts_flag))            IN ('0','N','NO','FALSE','НЕТ') THEN 0 END AS fts
FROM clients;

-- ---------- LLM gateway (WP-B9) and trace (spec 4.8) ----------
CREATE TABLE IF NOT EXISTS llm_calls (
  call_id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT, ts TEXT NOT NULL, day TEXT NOT NULL,
  operation TEXT NOT NULL,                          -- pdf_parse | classify | summarize | interpret | critic | dialog ...
  role TEXT, model TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  cost_rub REAL NOT NULL DEFAULT 0, cache_hit INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, error TEXT, request_hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_day ON llm_calls(day);

CREATE TABLE IF NOT EXISTS llm_cache (
  request_hash TEXT PRIMARY KEY, response TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT NOT NULL, card_id TEXT, step TEXT, tool TEXT,
  args_redacted TEXT, started_at TEXT, duration_ms INTEGER,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_rub REAL,
  result_ref TEXT, status TEXT, retry_of INTEGER
);
CREATE INDEX IF NOT EXISTS ix_trace ON trace_events(trace_id);

-- ---------- persisted cards and user interactions (WP-B11/B12/B15) ----------
CREATE TABLE IF NOT EXISTS cards (
  card_id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  persona TEXT NOT NULL,
  event_doc_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  score REAL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(event_doc_id) REFERENCES documents(doc_id)
);
CREATE INDEX IF NOT EXISTS ix_cards_client ON cards(client_id, created_at DESC);
CREATE TABLE IF NOT EXISTS card_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES cards(card_id),
  helpful INTEGER NOT NULL CHECK(helpful IN (0,1)),
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES cards(card_id),
  question TEXT NOT NULL,
  answer_json TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
