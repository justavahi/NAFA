# -*- coding: utf-8 -*-
"""
analytics — пакет скриптов роли Data Analyst проекта NAFA.

Задачи (по ТЗ 03_TZ_data_analyst.md):
    WP-D1  profiling.py    — профилирование данных (раздел 3.1 ТЗ)
    WP-D2  dictionaries.py — справочники: ОКВЭД -> SP, словари значений, страна -> валюта
    WP-D3  metrics_f.py    — метрики F-01..F-13 (признаки клиента)
    WP-D4  metrics_g.py    — метрики G-01..G-10 (платежи и контрагенты)
    WP-D5  metrics_e.py    — экспозиции E-01..E-15 (вектор чувствительности)
    WP-D6  formulas.py     — формулы FM-01..FM-24 (чистые функции, без LLM)
    WP-D7  golden_set.py   — золотой набор: согласие разметчиков (Cohen kappa), hold-out
    WP-D8  calibrate.py    — калибровка весов FM-13 (симплекс, NDCG@5, LOAO)
    WP-D9  quality.py      — контур качества: Q-01..Q-11, Q-17, Q-28..Q-31
    WP-D11 charts.py       — графики CH-01..CH-25 (P0-семёрка полностью)
    WP-D16 graphs.py       — графы GR-01..GR-03, FM-21..FM-24 (цепочки, группы)

Запуск демо без реальных данных:  python -m analytics.run_all --demo
Прогон на реальных данных:        python -m analytics.run_all --data-dir путь/к/csv
Проверка тестовых векторов 4.2:    python -m tests.test_vectors
"""

__version__ = "0.1.0"
