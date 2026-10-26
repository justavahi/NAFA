# Descriptions of sources

_Template: spec section 4.1. `legal` shows FACTS recorded by `check-sources`; the terms-of-use review is manual._

## SRC-01 - Bank of Russia press releases

| Field | Value |
|---|---|
| id | SRC-01 |
| name / owner | Bank of Russia press releases / Bank of Russia |
| url / path | https://www.cbr.ru/rss/RssPress |
| type | RSS |
| auth | none (public endpoint) |
| update_frequency | our poll every 10 min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **allowed** (checked 2026-10-25T00:22:41+00:00); terms of use: **NOT VERIFIED - manual review required; terms URL: https://www.cbr.ru/**; storage allowed in our config: **snippet** |
| fields | see connector `rss` -> Document / MacroSeries |
| quirks | RSS 2.0, 12 items, pubDate with +0300 |
| tier_weight | 1.0 |
| parser | app/connectors (rss) |
| status | works (checked 2026-10-25T00:22:41+00:00) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | 2026-10-23T13:30:00+03:00 | Банк России принял решение сохранить ключевую ставку на уровне 14,00% годовых (23.10.2026) | https://www.cbr.ru/press/PR/?file=23102026_133000key.htm; 2026-10-22T04:51:00+03:00 | Отозвана лицензия на осуществление банковских операций у ООО НКО «Тайдон» (22.10.2026) | https://www.cbr.ru/press/PR/?file=639282394585722382BANK_SECTOR.htm; 2026-10-21T16:00:00+03:00 | Установлены факты неправомерного использования инсайдерской информации при совершении сдел | https://www.cbr.ru/press/PR/?file=639281939787093652PNP.htm |

## SRC-02 - Bank of Russia daily exchange rates

| Field | Value |
|---|---|
| id | SRC-02 |
| name / owner | Bank of Russia daily exchange rates / Bank of Russia |
| url / path | https://www.cbr.ru/scripts/XML_daily.asp |
| type | XML |
| auth | none (public endpoint) |
| update_frequency | our poll every 180 min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **allowed** (checked 2026-10-25T00:22:41+00:00); terms of use: **NOT VERIFIED - manual review required; terms URL: https://www.cbr.ru/**; storage allowed in our config: **full_text** |
| fields | see connector `cbr_daily` -> Document / MacroSeries |
| quirks | windows-1251, decimal comma, optional ?date_req=DD/MM/YYYY |
| tier_weight | 1.0 |
| parser | app/connectors (cbr_daily) |
| status | works (checked 2026-10-25T00:22:42+00:00) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | M-03 | 2026-10-24 | 80.5381 RUB; M-04 | 2026-10-24 | 12.0557 RUB; M-05 | 2026-10-24 | 90.2429 RUB |

## SRC-03 - Interfax general feed

| Field | Value |
|---|---|
| id | SRC-03 |
| name / owner | Interfax general feed / Interfax |
| url / path | https://www.interfax.ru/rss.asp |
| type | RSS |
| auth | none (public endpoint) |
| update_frequency | our poll every 5 min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **allowed** (checked 2026-10-25T00:22:48+00:00); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `rss` -> Document / MacroSeries |
| quirks | General feed with lots of noise (filter downstream); atom namespace via https; asks to identify the caller (OQ-11): identifying User-Agent is sent |
| tier_weight | 0.85 |
| parser | app/connectors (rss) |
| status | works (checked 2026-10-25T00:22:49+00:00) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | 2026-10-25T02:56:00+03:00 | Один человек погиб, десять ранены в центре Белгорода при массированной атаке БПЛА | https://www.interfax.ru/russia/1124819; 2026-10-25T02:48:00+03:00 | Землетрясение магнитудой 5,2 произошло у берегов Камчатки | https://www.interfax.ru/russia/1124818; 2026-10-25T02:41:00+03:00 | Белгород подвергся массированной атаке БПЛА, есть пострадавшие | https://www.interfax.ru/russia/1124817 |

## SRC-04 - RBC news feed

| Field | Value |
|---|---|
| id | SRC-04 |
| name / owner | RBC news feed / RBC |
| url / path | https://rssexport.rbc.ru/rbcnews/news/30/full.rss |
| type | RSS |
| auth | none (public endpoint) |
| update_frequency | our poll every 5 min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **allowed** (checked 2026-10-25T00:22:50+00:00); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `rss` -> Document / MacroSeries |
| quirks | RSS 2.0 with full-text extension; response may be truncated (unclosed tags) -> recovering XML parser |
| tier_weight | 0.85 |
| parser | app/connectors (rss) |
| status | works (checked 2026-10-25T00:22:51+00:00) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | 2026-10-25T03:01:38+03:00 | Мэр Белгорода сообщил о значительных разрушениях после атаки БПЛА | https://www.rbc.ru/rbcfreenews/6add435cd62b94fdb2f092ef; 2026-10-25T02:51:37+03:00 | Белгород последствия | https://www.rbc.ru/photoslider/6add440fb4ea335ad8b7867d; 2026-10-25T02:49:57+03:00 | При массированной атаке БПЛА на Белгород погиб человек | https://www.rbc.ru/politics/25/10/2026/6add43f9e062e941d97d44c5 |

## SRC-05 - TASS RSS

| Field | Value |
|---|---|
| id | SRC-05 |
| name / owner | TASS RSS / TASS |
| url / path | none |
| type | RSS |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `rss` -> Document / MacroSeries |
| quirks | none recorded |
| tier_weight | 0.85 |
| parser | app/connectors (rss) |
| status | disabled (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-06 - Kommersant RSS

| Field | Value |
|---|---|
| id | SRC-06 |
| name / owner | Kommersant RSS / Kommersant |
| url / path | none |
| type | RSS |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `rss` -> Document / MacroSeries |
| quirks | none recorded |
| tier_weight | 0.85 |
| parser | app/connectors (rss) |
| status | disabled (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-07 - Vedomosti RSS

| Field | Value |
|---|---|
| id | SRC-07 |
| name / owner | Vedomosti RSS / Vedomosti |
| url / path | none |
| type | RSS |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `rss` -> Document / MacroSeries |
| quirks | none recorded |
| tier_weight | 0.85 |
| parser | app/connectors (rss) |
| status | disabled (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-08 - Ministry of Finance

| Field | Value |
|---|---|
| id | SRC-08 |
| name / owner | Ministry of Finance / MinFin RF |
| url / path | none |
| type | HTML |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `none` -> Document / MacroSeries |
| quirks | none recorded |
| tier_weight | 1.0 |
| parser | app/connectors (none) |
| status | disabled (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-09 - Rosstat

| Field | Value |
|---|---|
| id | SRC-09 |
| name / owner | Rosstat / Rosstat |
| url / path | none |
| type | HTML |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **snippet** |
| fields | see connector `none` -> Document / MacroSeries |
| quirks | none recorded |
| tier_weight | 1.0 |
| parser | app/connectors (none) |
| status | disabled (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-10 - MOEX ISS API

| Field | Value |
|---|---|
| id | SRC-10 |
| name / owner | MOEX ISS API / MOEX |
| url / path | https://iss.moex.com |
| type | JSON |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **metadata** |
| fields | see connector `none` -> Document / MacroSeries |
| quirks | robots.txt restricts API access - verify terms before enabling |
| tier_weight | 1.0 |
| parser | app/connectors (none) |
| status | disabled (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-14 - User-pasted news (text or URL) and synthetic news

| Field | Value |
|---|---|
| id | SRC-14 |
| name / owner | User-pasted news (text or URL) and synthetic news / internal |
| url / path | none |
| type | internal |
| auth | none (public endpoint) |
| update_frequency | our poll every n/a min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **not checked yet** (checked never); terms of use: **NOT VERIFIED - manual review required**; storage allowed in our config: **full_text** |
| fields | see connector `internal` -> Document / MacroSeries |
| quirks | none recorded |
| tier_weight | 0.4 |
| parser | app/connectors (internal) |
| status | not_checked (checked never) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | none yet |

## SRC-16 - Bank of Russia key rate

| Field | Value |
|---|---|
| id | SRC-16 |
| name / owner | Bank of Russia key rate / Bank of Russia |
| url / path | https://www.cbr.ru/hd_base/KeyRate/ |
| type | HTML |
| auth | none (public endpoint) |
| update_frequency | our poll every 360 min |
| rate_limit | >= 1.0 s between requests to one host (http.min_interval_s) |
| legal | robots.txt: **allowed** (checked 2026-10-25T00:22:51+00:00); terms of use: **NOT VERIFIED - manual review required; terms URL: https://www.cbr.ru/**; storage allowed in our config: **full_text** |
| fields | see connector `cbr_keyrate` -> Document / MacroSeries |
| quirks | HTML table date -> rate (decimal comma). Query params UniDbQuery.From/To are UNVERIFIED in the case docs: check-sources tests them. DailyInfoWebServ is an alternative. |
| tier_weight | 1.0 |
| parser | app/connectors (cbr_keyrate) |
| status | works (checked 2026-10-25T00:22:53+00:00) |
| failure_modes / fallback | retries <=5 with growing pauses; failed run logged in fetch_runs; cached documents stay available; synthetic news set (SRC-14) for demos |
| samples | M-01 | 2026-10-23 | 14.0 % p.a.; M-01 | 2026-10-22 | 14.0 % p.a.; M-01 | 2026-10-21 | 14.0 % p.a. |

