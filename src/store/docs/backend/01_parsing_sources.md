# Parsing Sources

_Generated from config/sources.yaml and the database. Do not edit by hand._

| ID | Source | Type | Priority | Parser | Poll (min) | Status | Last success | Samples |
|---|---|---|---|---|---|---|---|---|
| SRC-01 | Bank of Russia press releases | RSS | P0 | rss | 10 | works | 2026-10-25T00:22:41+00:00 | 3 |
| SRC-02 | Bank of Russia daily exchange rates | XML | P0 | cbr_daily | 180 | works | 2026-10-25T00:22:42+00:00 | 3 |
| SRC-03 | Interfax general feed | RSS | P0 | rss | 5 | works | 2026-10-25T00:22:49+00:00 | 3 |
| SRC-04 | RBC news feed | RSS | P0 | rss | 5 | works | 2026-10-25T00:22:51+00:00 | 3 |
| SRC-05 | TASS RSS | RSS | P1 | rss |  | disabled | never | 0 |
| SRC-06 | Kommersant RSS | RSS | P1 | rss |  | disabled | never | 0 |
| SRC-07 | Vedomosti RSS | RSS | P2 | rss |  | disabled | never | 0 |
| SRC-08 | Ministry of Finance | HTML | P1 | none |  | disabled | never | 0 |
| SRC-09 | Rosstat | HTML | P1 | none |  | disabled | never | 0 |
| SRC-10 | MOEX ISS API | JSON | P1 | none |  | disabled | never | 0 |
| SRC-14 | User-pasted news (text or URL) and synthetic news | internal | P0 | internal |  | not_checked | never | 0 |
| SRC-16 | Bank of Russia key rate | HTML | P0 | cbr_keyrate | 360 | works | 2026-10-25T00:22:53+00:00 | 3 |

## Samples (3 latest records per source)

### SRC-01 - Bank of Russia press releases
- 2026-10-23T13:30:00+03:00 | Банк России принял решение сохранить ключевую ставку на уровне 14,00% годовых (23.10.2026) | https://www.cbr.ru/press/PR/?file=23102026_133000key.htm
- 2026-10-22T04:51:00+03:00 | Отозвана лицензия на осуществление банковских операций у ООО НКО «Тайдон» (22.10.2026) | https://www.cbr.ru/press/PR/?file=639282394585722382BANK_SECTOR.htm
- 2026-10-21T16:00:00+03:00 | Установлены факты неправомерного использования инсайдерской информации при совершении сдел | https://www.cbr.ru/press/PR/?file=639281939787093652PNP.htm

### SRC-02 - Bank of Russia daily exchange rates
- M-03 | 2026-10-24 | 80.5381 RUB
- M-04 | 2026-10-24 | 12.0557 RUB
- M-05 | 2026-10-24 | 90.2429 RUB

### SRC-03 - Interfax general feed
- 2026-10-25T02:56:00+03:00 | Один человек погиб, десять ранены в центре Белгорода при массированной атаке БПЛА | https://www.interfax.ru/russia/1124819
- 2026-10-25T02:48:00+03:00 | Землетрясение магнитудой 5,2 произошло у берегов Камчатки | https://www.interfax.ru/russia/1124818
- 2026-10-25T02:41:00+03:00 | Белгород подвергся массированной атаке БПЛА, есть пострадавшие | https://www.interfax.ru/russia/1124817

### SRC-04 - RBC news feed
- 2026-10-25T03:01:38+03:00 | Мэр Белгорода сообщил о значительных разрушениях после атаки БПЛА | https://www.rbc.ru/rbcfreenews/6add435cd62b94fdb2f092ef
- 2026-10-25T02:51:37+03:00 | Белгород последствия | https://www.rbc.ru/photoslider/6add440fb4ea335ad8b7867d
- 2026-10-25T02:49:57+03:00 | При массированной атаке БПЛА на Белгород погиб человек | https://www.rbc.ru/politics/25/10/2026/6add43f9e062e941d97d44c5

### SRC-05 - TASS RSS
- _no records yet_

### SRC-06 - Kommersant RSS
- _no records yet_

### SRC-07 - Vedomosti RSS
- _no records yet_

### SRC-08 - Ministry of Finance
- _no records yet_

### SRC-09 - Rosstat
- _no records yet_

### SRC-10 - MOEX ISS API
- _no records yet_

### SRC-14 - User-pasted news (text or URL) and synthetic news
- _no records yet_

### SRC-16 - Bank of Russia key rate
- M-01 | 2026-10-23 | 14.0 % p.a.
- M-01 | 2026-10-22 | 14.0 % p.a.
- M-01 | 2026-10-21 | 14.0 % p.a.

