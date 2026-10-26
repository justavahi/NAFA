# 02. Necessary formulas & calculations — формулы FM-01..FM-24 и расчёты

> Роль: Data Analyst (NAFA). Все формулы реализованы **чистыми функциями на Python** — без обращений к LLM. Параметры вынесены в конфигурацию. Тестовые векторы раздела 4.2 ТЗ воспроизводятся: **44 OK / 0 FAIL**.

## 1. Где что лежит (маппинг путей ТЗ → репозиторий)

| Объект по ТЗ | Путь в репозитории |
|---|---|
| Код формул | `da_scripts/analytics/formulas.py` (FM-01..FM-24), `da_scripts/analytics/metrics_e.py` (FM-03/FM-04 в векторной реализации) |
| Калибровка FM-18 | `da_scripts/analytics/calibrate.py` |
| Метрики качества FM-19 | `da_scripts/analytics/quality.py`, `golden_set.py` |
| Конфиг весов | `da_scripts/config/weights.yaml` (FM-08/09/10/11/13/14/16/17/20/22/23/18, F-13) |
| Конфиг экспозиций | `da_scripts/config/exposure_weights.yaml` (E-01..E-15, FM-22, справочник риск-тиров стран) |
| Тесты | `da_scripts/tests/test_vectors.py` |
| Справочники (матрица, ОКВЭД→SP, персоны, seed) | `da_scripts/ref_data/` = копия `docs/data/` |

**Запуск тестов одной командой** (из папки `da_scripts`):

```
python -m tests.test_vectors
```

**Запуск калибровки FM-18:**

```
python -m analytics.calibrate --components base_components.csv --golden golden_set.csv --out-dir out/calibration
```

## 2. Реестр формул

| ID | Назначение | Реализация | Параметры (конфиг) |
|---|---|---|---|
| FM-01 | Крупный бизнес и масштаб | `formulas.fm01_is_large` | порог 15e9 (аргумент) |
| FM-02 | Доля в отрасли | `formulas.fm02_share` | — |
| FM-03 | Noisy-OR объединение экспозиций | `formulas.fm03_noisy_or`; векторно `metrics_e.e01_own_rate_debt` | `exposure_weights.yaml → e01_debt` |
| FM-04 | Вектор экспозиций ε | `metrics_e.exposure_vector` (векторизовано на 85 тыс. клиентов) | `e14_alpha: own/prior/chain = 0.4/0.4/0.2` |
| FM-05 | HHI | `formulas.fm05_hhi`, `metrics_g.g03_hhi` | — |
| FM-06 | Релевантность события: Cov и Rel | `formulas.fm06_relevance` | порог Rel 0.25 (`weights.yaml → fm13`) |
| FM-07 | Персональный интерес Pers | `formulas.fm07_pers` | `ref_data/persona_weights.csv` |
| FM-08 | Свежесть Fresh = 0.5^(age/h) | `formulas.fm08_fresh` | `fm08_half_life_days` по типу документа |
| FM-09 | Срочность Urg | `formulas.fm09_urgency` | `fm09_tau_days: 7` |
| FM-10 | Достоверность Cred | `formulas.fm10_credibility` | `fm10_source_tiers` |
| FM-11 | Новизна Nov | `formulas.fm11_novelty` | `fm11_window_days: 14` |
| FM-12 | Масштаб/сюрприз Imp | `formulas.fm12_impact` | s0 в сигнатуре |
| FM-13 | Итоговый скор + фильтр + топ-k (MMR) | `formulas.fm13_score`, `fm13_filter_and_topk` | `fm13: weights, rel_threshold, top_k, mmr_lambda` |
| FM-14 | Правило знака эффекта ставки | `formulas.fm14_sign_effect` | `fm14.rate_threshold: 0.15` (двухветвевая логика) |
| FM-15 | Сценарные калькуляторы (процент, валюта, себестоимость) | `formulas.fm15_interest_expense`, `fm15_fx_result`, `fm15_cogs` | вводные от пользователя (`fm15_scenario` пуст) |
| FM-16 | Версии позиции банка, superseded, конфликт | `formulas.fm16_versions` | `fm16.conflict_delta: RATE 0.25 п.п.` |
| FM-17 | Дедупликация событий | `formulas.fm17_exact_key`, `fm17_jaccard`, `fm17_tf_cosine`, `fm17_is_duplicate` | `fm17_dedup` |
| FM-18 | Калибровка весов FM-13 | `calibrate.py` (симплекс, регуляризация, LAVO, бутстрэп) | `fm18_calibration: step 0.05, lambda_reg 0.01, bootstrap 1000` |
| FM-19 | Метрики качества Q | `quality.py` | цели каталога Q-01..Q-31 |
| FM-20 | Уверенность вывода | `formulas.fm20_confidence` | `fm20_confidence: high 0.75, mid 0.50` |
| FM-21 | Индекс уязвимости к ставке | `formulas.fm21_v_rate` | использует F-01 (pctl_rev) |
| FM-22 | Распространение шока по контрагентам | `formulas.fm22_shock_propagation`; рёбра `graphs.counterparty_shock_edges` | `fm22_shock: gamma 0.5, depth 2` |
| FM-23 | Распространение по группе компаний | `formulas.fm23_group_spread` | `fm23_group_delta: 0.5` |
| FM-24 | Линейный базовый скор | `formulas.fm24_linear_score` | `fm13baseline: 0.35/0.15/0.30/0.20` |

## 3. Ключевые формулы (как реализовано)

**FM-06 (релевантность).** Событие задаётся вектором интереса ι по факторам. Покрытие: `Cov = Σ_f min(ι_f, eps_f) / Σ_f ι_f`. Релевантность: `Rel = Cov · (1 + β·match)`, при Match=0 `Rel = Cov`, масштабируется к [0,1] нормировкой ι. События с `Rel < 0.25` не проходят в ленту.

**FM-13 (скор карточки).** `Score = Fresh · (w_Rel·Rel + w_Imp·Imp + w_Urg·Urg + w_Nov·Nov + w_Cred·Cred) · (0.5 + 0.5·Pers)`. Стартовые веса (0.40, 0.25, 0.10, 0.10, 0.15) — допущение BA, калибруются FM-18. Затем фильтр по порогу Rel, дедупликация тем и жадный MMR (λ=0.7) против «пяти одинаковых», топ-5.

**FM-14/15 (эффект ставки).** `rate_net > 0` → ставка выгодна (вклад), `< 0` → вредна (долг); `|rate_net| ≤ 0.15` — обе ветви в карточке. Калькуляторы: проценты `Δ = D·Δr` (10 млн ₽ при D=1000 млн, Δr=+1 п.п.), валюта `Δ = V·(S1−S0)·k` (−43,5 млн ₽ для вектора 4.2), себестоимость с pass-through.

**FM-21..FM-23 (графовые).** `V_rate = E-01 · (0.5 + 0.5·pctl_rev)` (0.72/0.36/0.54 для вектора). Шок по цепочке `P_k = 1 − Π(1 − γ·w_i·x_i)` с глубиной 2 и γ=0.5 (0.38 → 0.50 для вектора). По группе `x_group = x_c + δ·(max_rest − x_c)` при `max_rest > x_c` (0.4 для вектора).

**FM-24 (базовая линия).** `ScaleFactor = 0.5·pctl_rev + 0.5·pctl_workers`; `Score_lin = 0.35·IndustryMatch + 0.15·ScaleFactor + 0.30·ProductRelevance + 0.20·SupplyChainRisk` (0.762 для вектора). Служит нижней планкой для FM-18: FM-13 обязан выигрывать по NDCG@5.

## 4. Тестовые векторы раздела 4.2 ТЗ — все воспроизводятся

Полный набор — 44 проверки (векторы 4.2 + sanity 4.3 + регресс-кейсы: ведущий ноль ОКВЭД `01.34`, renormalization ε при пропусках, NULL против нуля у G-метрик). Запуск: `python -m tests.test_vectors`.

| Вход (4.2 ТЗ) | Ожидание | Статус |
|---|---|---|
| CA-01: LOA=1, LEA=1, FACTORING=0 | E-01 = 0.72 | OK |
| CA-01: DEP=0 | E-02 = 0; rate_net = −0.72 | OK |
| CA-01, SP-09, FTS=0, GAR=1, зрелая, без платежей | ε: RATE 0.86; FX 0.167; INFL 1.0; ENERGY 0.333; COMMOD 0.667; DEMAND 1.0; LABOR 1.0; CREDIT 0.75; BUDGET 0.85; REG 1.0; GEOPOL 0.333; CPTY 1.0 | OK (12/12, допуск 0.001) |
| ET-01, ι: RATE 1.0; FX 0.5; INFL 0.5; CREDIT 0.5 | Cov = 0.727; Rel = 0.509 | OK |
| Роль PER-03 | Pers = 0.94 | OK |
| as_of 14.09.2026, событие 11.09.2026, h=14 | Fresh = 0.862 | OK |
| Imp 0.75, Urg 0, Nov 1, Cred 1 | Score = 0.536 | OK |
| CA-07 (ИТ, DEP=1, SP-13), то же событие | Cov = 0.353; Rel = 0.247 < 0.25 — вне топ-5 | OK |
| FM-15(а): D=1000 млн, Δr=+1 п.п. | +10 млн ₽/год | OK |
| FM-15(б): V=10 млн $, S0=82.85, S1=87.2, k=−1 | −43.5 млн ₽ | OK |
| FM-16: ставка на конец 2026: 13.5% (31.07, 07.09), 14.0% (11.09) | активна 14.0%; 13.5% superseded; конфликт Δ=0.5 ≥ 0.25 | OK |
| FM-14: rate_net = −0.72, d_RATE = +1 | эффект отрицательный; при rate_net=0 — две ветви | OK |
| FM-21: E-01=0.72; pctl_rev = 1; 0; NULL | V_rate = 0.72; 0.36; 0.54 | OK |
| FM-22: доли 0.6/0.4, x=0.5/0.2, γ=0.5 | P_1 = 0.38; при P_1(x=0.5)=0.4 → P_2 = 0.50 | OK |
| FM-23: x_c=0.3, max_rest=0.8, δ=0.5 | x_group = 0.4 | OK |
| FM-24: w=(0.35;0.15;0.30;0.20), вектор из ТЗ | ScaleFactor = 0.8; Score_lin = 0.762 | OK |

## 5. Sanity-проверки экспозиций (4.3 ТЗ)

| Проверка | Ожидание | Статус |
|---|---|---|
| Клиент без LOA/LEA/FACTORING | E-01 = 0 | OK |
| DEP без долга | rate_net > 0 | OK |
| LOA и DEP одновременно | rate_net близко к 0 | OK |
| FTS_FLAG = 0 | own FX = 0, остаётся prior | OK |
| Все ε ∈ [0, 1] | да (проверяется и на реальном прогоне — Приложение A) | OK |
| Сумма долей mix = 1 | да (допуск 1e-6) | OK |
| Монотонность по LEA | E-01 не уменьшается | OK |

## 6. Калибровка FM-18 (WP-D8) — статус

Метод реализован (`analytics/calibrate.py`): перебор весов FM-13 по симплексу с шагом 0.05, максимизация среднего NDCG@5 со штрафом `λ_reg·‖w − w0‖²` (λ=0.01), проверка leave-one-archetype-out, бутстрэп-доверительные интервалы (1000 повторов), сравнение с FM-24 и абляции по членам.

**Статус: ожидает разметки золотого набора (WP-D7).** Стартовые веса в `config/weights.yaml` не менялись — правило ТЗ «не менять параметры после просмотра hold-out» соблюдено: до разметки калибровка не запускалась. После разметки 40–60 пар двумя аналитиками (κ ≥ 0.6):

1. `python -m analytics.golden_set --check <файл разметки>` → κ;
2. `python -m analytics.golden_set --split <файл>` → hold-out;
3. `python -m analytics.calibrate --components base_components.csv --golden golden_set.csv`;
4. Выбранные веса вписываются в `config/weights.yaml → fm13`, обоснование дополняется в этот файл.

## 7. Конфигурационные файлы (единственный источник параметров)

- `config/weights.yaml` — FM-13 (веса, порог Rel 0.25, top-k 5, λ MMR 0.7), FM-14 (порог 0.15), FM-16 (Δ 0.25 п.п.), полураспады FM-08 (3/14/30/60 дней), FM-09 (7 дней), тиры источников FM-10, окно новизны FM-11 (14 дн.), пороги дедупа FM-17, уровни уверенности FM-20, γ/глубина FM-22, δ FM-23, параметры FM-18, веса полноты F-13.
- `config/exposure_weights.yaml` — веса noisy-OR E-01 (0.6/0.3/0.3), E-02 (0.6), E-03 (0.8), E-08..E-10, риск-тиры стран E-12, параметры E-13 (0.6/0.4 + порог малых контрагентов), α-веса E-14 (0.4/0.4/0.2).

Правило ТЗ «всё, что считается числом, считает код» соблюдено: LLM в расчётах не участвует; все параметры конфигурируемы; ни одна формула не зашита в промпты.
