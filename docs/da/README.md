# Data Analyst — пакет результатов

Команда NAFA. Всё, что считается числом, считает код, а не LLM.

## Документы (по таблице команды)

| Файл | Что внутри |
|---|---|
| [`01_metrics_descriptions.md`](01_metrics_descriptions.md) | Паспорта метрик P0/P1/P2 (F/G/E/Q), итоги профилирования WP-D1 на реальных данных, список вопросов организаторам (OQ-01..07) |
| [`01_stats_appendix.md`](01_stats_appendix.md) | Приложение A: распределения всех метрик по данным кейса (авто-экспорт) |
| [`02_formulas_and_calculations.md`](02_formulas_and_calculations.md) | Реализация FM-01..FM-24, конфиги, тестовые векторы 4.2 (44 OK / 0 FAIL), статус калибровки FM-18 |
| [`03_graphics.md`](03_graphics.md) | Реестр CH-01..CH-25, статусы, метаданные `CH-xx.json` |

Графики: `../assets/charts/CH-xx_<slug>.png` + `CH-xx.json`.

## Код

Пакет `da_scripts/` в корне репозитория:

- `da_scripts/analytics/` — контур целиком (профилирование, справочники, метрики F/G/E, формулы, графы, качество, калибровка, графики);
- `da_scripts/config/*.yaml` — все параметры (правило ТЗ: параметры не в коде);
- `da_scripts/ref_data/` — справочники = `docs/data/`;
- `da_scripts/tests/test_vectors.py` — тестовые векторы раздела 4.2;
- `da_scripts/requirements_da.txt`, `da_scripts/README_DA.md`.

## Быстрый старт

```
cd da_scripts
python -m pip install -r requirements_da.txt
python -m tests.test_vectors
python -m analytics.run_all --data-dir real_data --out-dir out
python -m analytics.export_doc_tables --features-dir out/features --out ../docs/da/01_stats_appendix.md
```

## Статус по DoD роли (раздел 7 ТЗ)

| Пункт DoD | Статус |
|---|---|
| Три выходных файла заполнены, графики в `docs/assets/charts/` | сделано (CH-08 — после рядов M от Backend) |
| P0-задачи WP-D1..D5, D6, D11, D16 | закрыты, прогон на реальных данных 2026-10-25 (85 514 клиентов, 1,38 млн платежей) |
| Тестовые векторы 4.2 воспроизводятся | да: 44 OK / 0 FAIL |
| Золотой набор (WP-D7): разметка двумя аналитиками, κ ≥ 0.6, hold-out | **в работе**: шаблон сгенерирован, идёт разметка |
| Контур оценки одной командой, Q-01..Q-11, Q-28, Q-29 | код готов (`python -m analytics.quality ...`); числа — после разметки и трейсов агента |
| Расхождения переданы BA, вопросы организаторам обновлены | да: OQ-01..OQ-07 в файле 01 |

## Передача другим ролям

- **Backend**: функции FM-xx, `config/*.yaml`, выгрузки `out/features/*.csv`, `gr02_edges.csv`.
- **Prompt Engineer**: паспорта метрик («как объяснять пользователю»), `CH-xx.json` (takeaway, alt_text), правила NULL («неизвестно» ≠ «нет продукта»).
- **BA**: список OQ, предложения по матрице SP×фактор (после WP-D12).
