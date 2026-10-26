# ♡ News-AI-Financial-Assistant ♡
Financial assistant for AlfaBank made by *The Great New Alliance* :3

# Documentation
all explanations of business logic, architecure, etc are in docs :D

## Проверка объединённой версии

Сейчас это рабочие компоненты прототипа, **полная приёмка по кейсу ещё не завершена**.
Матрица требований Альфа-Банка, найденные ошибки и результаты проверок:
[docs/verification/merged_audit.md](docs/verification/merged_audit.md).

Код: `src/store` — API/источники/SQLite; `src/da_scripts` — признаки и расчёты;
`src/report_scripts` — промпты, схемы, генерация и критик. Python 3.12, менеджер `uv`.

Все воспроизводимые локальные проверки из корня:

```bash
uv run --project src/store python scripts/verify_merged.py
```

Проверяются тесты трёх частей, настоящий запуск HTTP-сервера, 8 архетипов × 6 ролей,
корпус и поиск. Удалённые LLM не вызываются, человеческие метки не генерируются.
Отчёты — `docs/verification/`, временные БД и логи — в игнорируемом `.local/audit/`.
`full_case_acceptance=false` не скрывается даже при зелёных инфраструктурных тестах.

## Запустить приложение

Подготовьте отдельную базу с восемью синтетическими компаниями. Подготовка не вызывает модели:

```bash
uv run --project src/store --with matplotlib==3.11.2 --with tabulate==0.10.0 \
  python scripts/prepare_runtime.py --demo
uv run --project src/store python scripts/run_backend.py --env-file .local/demo/runtime.env
```

Откройте `http://127.0.0.1:8000`. На странице входа доступны демо-компании.
Выберите роль и событие из ленты либо вставьте свою новость. В карточке доступны
факты, позиция банка, вывод AI, вопрос, сценарные расчёты, обратная связь и trace.
Ответы в демо помечены как демонстрационные и не считаются опубликованными
рабочими карточками.

Рабочая область помещается в один экран: лента слева, событие справа;
источники, расчёты и основания — во вкладках. На телефоне между лентой и
карточкой можно переключаться. Кнопка «Разобрать влияние» запускает AI отдельно:
просмотр событий не расходует вызовы моделей.
[Сверка нового интерфейса с ТЗ](docs/verification/ui_redesign.md).

Для генерации нужны `LLM_BASE_URL` и `LLM_API_KEY` из инструкции организаторов.
Сохраните их в приватном файле и передайте вторым `--env-file`:

```bash
uv run --project src/store python scripts/run_backend.py \
  --env-file .local/demo/runtime.env --env-file /absolute/path/to/models.env
```

Лента и профиль работают без ключа; генерация без API завершается сообщением об ошибке.
Для реальных данных используйте `--data-dir /absolute/path/to/participant-data`
вместо `--demo`. Будет создан `.local/participant/runtime.env` с отдельной БД,
постоянной солью псевдонимов и admin token. Для входа администратора используйте
`NAFA_ADMIN_TOKEN` из этого файла. Для клиентов задайте `NAFA_CLIENT_TOKENS` — JSON
вида `{ "секретный-код": "ID компании" }`. Клиент видит только свою компанию,
карточки и добавленные новости. Секреты и данные не публикуйте в Git.
Повторная подготовка сохраняет существующий runtime: для нового снимка задайте другой `--output`.

## Аналитика банка

Загрузите поставляемый кэш извлечения без сетевых вызовов:

```bash
uv run --project src/store python scripts/prepare_corpus.py --env-file .local/demo/runtime.env
```

Войдите как администратор и откройте очередь проверки внизу страницы.
Сверьте текст и числа с оригиналом PDF, исправьте ошибки и подтвердите фрагмент.
Только вручную проверенные фрагменты используются как позиция банка.
Без них приложение сообщает об отсутствии подтверждённой позиции.
Кэш не заменяет приёмку разрешённой VLM; DOC-10 отсутствует в исходных файлах.

## Как соединены части

```bash
uv sync --project src/store --group dev --frozen
# Настройте src/store/.env: см. src/store/.env.example
python scripts/run_backend.py
```

API: `http://127.0.0.1:8000/docs`. Нужны URL/ключ разрешённого API моделей,
`NAFA_ADMIN_TOKEN`, стабильный `NAFA_PSEUDONYM_SALT`. Модели/источники/пороги
настраиваются в `src/store/config/`, схемы PE — в `src/report_scripts/schemas/`.
Новая новость автоматически проходит PRM-01 (Gemma), существующие расчёты DA,
PRM-05/06 (GLM) и независимый PRM-07 (Qwen). Извлечение события кэшируется
между компаниями; сырые платежи и ID в модель не передаются.
Feed использует FM-06…FM-13 и MMR; до извлечения факторов сортировка предварительная.
Неизвестный знак эффекта остаётся условным, без выдуманного расчёта.

Для доверенных готовых входов сохранён `POST /internal/pe/cases`;
[договор API](docs/verification/backend_handoff.md). Ручная регистрация не нужна
для стандартного пути с импортированными актуальными DA-агрегатами.
`python scripts/run_backend.py --mode baseline` запускает прежнюю предварительную
генерацию на правилах, явно с `publishable=false`; её нельзя выдавать за итоговую AI-карточку.

## Контейнер и браузерная проверка

Корневые `Dockerfile` и `compose.yaml` включают Backend, DA, PE и интерфейс.
Сначала подготовьте runtime. Добавьте модельные переменные в его приватный
`runtime.env`, затем выполните `docker compose up --build`.
По умолчанию монтируется `.local/demo`; для другого runtime задайте
`NAFA_RUNTIME_DIR` и `NAFA_RUNTIME_ENV`. PDF монтируются отдельно только для чтения.
Docker daemon на машине проверки недоступен: конфигурация Compose валидна,
сборка и запуск контейнера ещё не подтверждены.

```bash
uv run --project src/store --group dev --with playwright python scripts/check_browser.py
```

Браузерный тест требует подготовленный demo runtime и Chrome либо Chromium
(`playwright install chromium`). Проверяет вход, шесть ролей, ленту, свою новость,
карточку, вопрос, расчёт, feedback, trace, мобильный экран и выход. Используются
настоящие API и DA/PE, но контролируемые ответы моделей. Отчёт —
`docs/verification/browser_check.json`; это не оценка качества настоящих LLM.

До сдачи остаются человеческие оценки, живой прогон текущего профиля,
сверка PDF, тарифов/задержек и контейнера. Инфраструктурные тесты не доказывают
оценку 4,5 и полное соответствие кейсу.

# Roles ┌(;￣◇￣)┘
|      Nickname      |        Role       |
|--------------------|-------------------|
| poluektovnikolai07 |  Business Analyst |
|     justavahi      | Backend & Parsing |
|     PhantasmT      |    Data Analyst   |
|    roreternity     |   Prompt Engeener |

Informally *justavahi* also is the *Architecure Designer* so please ask him freely what to do if something feels senseless ૮₍˶•⤙•˶₎ა

# How to work ( ˘▽˘)っ 
- Every contributer has to work in his own branch 
- please DON'T merge any branch with any branch else before everyone finishes their work
- If a contributer needs to push anything in the *main* branch, he has to discuss it with *The Great New Alliance* in advance. Alfa Bank's pdf file is in the *main* branch  
- *Prepared tasks* in branches for each contributer are just *recomendations*. If a contributer isn't agree, he may do *whatever* he considers right ;(
