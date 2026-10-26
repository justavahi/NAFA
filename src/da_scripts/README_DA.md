# Скрипты роли Data Analyst — NAFA (кейс Альфа-Банк x Changellenge)

Пакет закрывает ТЗ `docs/03_TZ_data_analyst.md`: все числа считает код,
а не LLM. Главная контрольная точка — тестовые векторы раздела 4.2 ТЗ:
**сейчас 44 проверки из 44 проходят** (допуск 0.001).

## Установка (Windows, CMD/PowerShell — однострочные команды)

```
python -m pip install -r requirements_da.txt
python -m tests.test_vectors
```

Если тесты печатают `44 OK / 0 FAIL` — окружение готово.

## Демо без реальных данных (проверить, что всё работает)

```
python -m analytics.run_all --demo
```

Создаст `demo_data/` (синтетика), посчитает весь контур и сложит результаты
в `out/`. Артефакты демо НЕ сдавать — они для проверки скриптов.

## Прогон на реальных данных Приложения 1

```
python -m analytics.run_all --data-dir путь\к\папке\с\csv --out-dir out
```

В папке должны лежать `df_Uid_data_hack.csv` (клиенты), платежи (имя файла
уточнить у организаторов), `df_Fid_Uid_link_hack.csv` (связи с физлицами).
Справочники пакета (`matrix`, `ОКВЭД→SP`, персоны, архетипы) лежат в
`ref_data/` — это копия `docs/data` из репозитория; можно указать свою папку
через `--ref-dir` или переменную `NAFA_REF_DIR`.

## Что где лежит (соответствие задачам ТЗ)

| Задача ТЗ | Модуль | Что делает | Результат |
|---|---|---|---|
| WP-D1 (P0) | `analytics/profiling.py` | 14 блоков проверок из раздела 3.1: дубли, единицы выручки, пропуски, словари, ФНС/СПАРК, ВЭД, группы, выбросы, платежи | `out/profiling/report.md` + вопросы BA (OQ) |
| WP-D2 (P0) | `analytics/dictionaries.py` | покрытие ОКВЭД→SP, словари значений, правила growth_flag/is_state_related, страна→валюта | `out/dictionaries/report.md` |
| WP-D3 (P0) | `analytics/metrics_f.py` | F-01..F-13 (профиль клиента) | `out/features/client_features.csv` |
| WP-D4 (P0) | `analytics/metrics_g.py` | G-01..G-10 (платежи, HHI, миксы) | `out/features/payment_features.csv` |
| WP-D5 (P0) | `analytics/metrics_e.py` | E-01..E-15, вектор eps 12 факторов | `out/features/exposures.csv` |
| WP-D6 (P0) | `analytics/formulas.py` | FM-01..FM-24 чистыми функциями | + тесты `tests/test_vectors.py` |
| WP-D7 (P0) | `analytics/golden_set.py` | шаблон разметки, Cohen kappa, hold-out | см. функции (CLI ниже) |
| WP-D8 (P1) | `analytics/calibrate.py` | симплекс 0.05, NDCG@5, LOAO, бутстрэп | `out/calibration/report.md` |
| WP-D9 (P0) | `analytics/quality.py` | Q-01..Q-11, Q-28, Q-29 (трейсы агента!), Q-30/31 — входы для CH-16..CH-19/CH-24 | `out/quality/report.md` |
| WP-D11 (P0) | `analytics/charts.py` | P0-семёрка CH-06/07/08/14/15/23/25 + P1-заготовки CH-01/02/03 | `out/CH-xx_*.png` + `CH-xx.json` |
| WP-D16 (P0) | `analytics/graphs.py` | GR-01..GR-03, FM-21..FM-24 (цепочки, группы) | лог + `out/features/gr02_edges.csv` |

Конфиги весов (по правилу ТЗ «пороги в конфигурации, не в коде»):
`config/weights.yaml`, `config/exposure_weights.yaml`.

## Золотой набор (WP-D7) — ручные шаги

```
python -m analytics.golden_set --template data/golden_set_seed.csv --out data/annotation_template.csv
```
1. Заполните `annotator1`/`annotator2` двумя аналитиками по шкале
   Noise=0 / Low=1 / Medium=2 / High=3, доведите набор до 40-60 пар.
2. `python -m analytics.golden_set --check data/golden_set.csv` — kappa;
   цель >= 0.6, иначе пересогласовать правила разметки.
3. `python -m analytics.golden_set --split data/golden_set.csv --out data` —
   отделит hold-out (эти события НЕ используются при калибровке WP-D8).

## Контур качества (WP-D9) — что подавать на вход

- `--golden data/golden_set.csv` — разметка relevance 0..3
- `--pred predictions.csv` — ленты: `client_id,event_id,score,rank` (топ-5)
- `--traces папка\с\json` — трейсы вашего RAG-агента: asserts claims с
  `source_ids` и `sources[].id/is_mock` — так считаются Q-28 (трассируемость)
  и Q-29 (индекс галлюцинаций). Работа, сделанная раньше, напрямую feeding
  эти метрики.
- `--pairs dedup_pairs.csv` — `pair_id,is_dup,flagged` для Q-11
- `--rubric rubric_scores.csv` — `card_id,q08_score` для Q-08/Q-10

## Калибровка (WP-D8)

Нужен `base_components.csv`: `client_id,event_id,archetype,fresh,rel,imp,urg,nov,cred,pers`.
Потом: `python -m analytics.calibrate --components base_components.csv --golden data/golden_set.csv --out-dir out`.
Итоговые веса вписать в `config/weights.yaml` (после этого hold-out не смотреть!).

## Согласие с ТЗ и известные допущения

- Все тестовые векторы 4.2 и sanity 4.3 воспроизводятся (44 OK / 0 FAIL).
- Расчёт eps воспроизводит пример 8.2 справочника целиком, включая вектор
  CA-01 (RATE 0.86 ... CPTY 1.0) и CA-07 (Rel 0.247 < 0.25).
- Допущения, которые нужно проверить на реальных данных и/или спросить BA:
  1. единицы REVENUE_COMPANY (порог 15 млрд может «не сработать» — WP-D1);
  2. собственная гео-экспозиция E-12 считается только при FTS_FLAG=1
     (при FTS=0 — компонент пуст, это даёт тестовый вектор GEOPOL=0.333);
  3. поправка E-13 на малое число контрагентов (+5% за недостающего до 5)
     — стартовое допущение, не из ТЗ;
  4. словарь страна→валюта — стартовый, расширять по данным ВЭД.

## Данные для графиков P1/P2 от Backend (ряды M)

CH-09..CH-13, CH-16..CH-20, CH-24 ждут ряды M-01..M-18 и логи (Q-12..Q-27):
положите CSV с датой/значением и используйте строители из `analytics/charts.py`
как шаблон. CH-08 в демо уже показывает нужный формат (rate_fact.csv + rate_forecast.csv).
