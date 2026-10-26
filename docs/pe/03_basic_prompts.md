# Промпты и протокол проверки

Тексты в `prompts/`, полный машиночитаемый реестр — `prompts/manifest.json`. Входные и выходные JSON-схемы экспортированы в `schemas/`. Общие правила — `common_v2.txt`: три слоя, проверяемые источники, готовые числа, недоверенный внешний текст и честность о пробелах.

Few-shot пока не используется: примеры золотого набора в system-текст не встроены. Поля constraints/few_shot/eval_cases/status хранятся в реестре. Статус прогона не равен экспертной приёмке.

| ID | Текст и версия | Вход | Выход | Модель | Лимит выхода |
|---|---|---|---|---|---|
| PRM-01 | [PRM-01_v1.txt](../../prompts/PRM-01_v1.txt); v1 | [ExtractionInput](../../schemas/ExtractionInput.json) | [EventExtraction](../../schemas/EventExtraction.json) | Gemma 4 26B A4B | 4096 |
| PRM-02 | [PRM-02_v1.txt](../../prompts/PRM-02_v1.txt); v1 | [ImpactInput](../../schemas/ImpactInput.json) | [ImpactVector](../../schemas/ImpactVector.json) | Gemma 4 26B A4B | 4096 |
| PRM-03 | [PRM-03_v4.txt](../../prompts/PRM-03_v4.txt); v4 | [PageInput](../../schemas/PageInput.json) | [Chunk](../../schemas/Chunk.json) | Qwen3-VL-30B-A3B-Instruct | 4096 |
| PRM-04 | [PRM-04_v1.txt](../../prompts/PRM-04_v1.txt); v1 | [BankInput](../../schemas/BankInput.json) | [BankViewExtraction](../../schemas/BankViewExtraction.json) | Gemma 4 26B A4B | 4096 |
| PRM-05 | [PRM-05_v2.txt](../../prompts/PRM-05_v2.txt); v2 | [CoreInput](../../schemas/CoreInput.json) | [EventCore](../../schemas/EventCore.json) | GLM-5.3-Flash | 4096 |
| PRM-06 | [PRM-06_v4.txt](../../prompts/PRM-06_v4.txt); v4 | [PersonalInput](../../schemas/PersonalInput.json) | [PersonalOverlay](../../schemas/PersonalOverlay.json) | GLM-5.3-Flash | 4096 |
| PRM-07 | [PRM-07_v4.txt](../../prompts/PRM-07_v4.txt); v4 | [CriticInput](../../schemas/CriticInput.json) | [CriticVerdict](../../schemas/CriticVerdict.json) | Qwen3.6 35B A3B | 6144 |
| PRM-08 | [PRM-08_v2.txt](../../prompts/PRM-08_v2.txt); v2 | [ChatInput](../../schemas/ChatInput.json) | [ChatAnswer](../../schemas/ChatAnswer.json) | Qwen3.6 35B A3B | 4096 |
| PRM-09 | [PRM-09_v1.txt](../../prompts/PRM-09_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | GLM-5.3-Flash | 4096 |
| PRM-10 | [PRM-10_v1.txt](../../prompts/PRM-10_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | GLM-5.3-Flash | 4096 |
| PRM-11 | [PRM-11_v1.txt](../../prompts/PRM-11_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | Qwen3.6 35B A3B | 4096 |
| PRM-12 | [PRM-12_v2.txt](../../prompts/PRM-12_v2.txt); v2 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | Qwen3.6 35B A3B | 4096 |
| PRM-13 | [PRM-13_v1.txt](../../prompts/PRM-13_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | Qwen3.6 35B A3B | 4096 |
| PRM-14 | [PRM-14_v1.txt](../../prompts/PRM-14_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | Qwen3.6 35B A3B | 4096 |
| PRM-15 | [PRM-15_v1.txt](../../prompts/PRM-15_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [OfferText](../../schemas/OfferText.json) | Qwen3.6 35B A3B | 4096 |
| PRM-16 | [PRM-16_v1.txt](../../prompts/PRM-16_v1.txt); v1 | [JudgeInput](../../schemas/JudgeInput.json) | [JudgeScore](../../schemas/JudgeScore.json) | Kimi-K2.6 | 4096 |
| PRM-17 | [PRM-17_v1.txt](../../prompts/PRM-17_v1.txt); v1 | [ExplanationInput](../../schemas/ExplanationInput.json) | [GroundedExplanation](../../schemas/GroundedExplanation.json) | GLM-5.3-Flash | 4096 |

| Промпт | Случаи seed | Статус автоматической проверки |
|---|---|---|
| PRM-01 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-02 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-03 | GS-01, GS-04, GS-06 | automated_3_of_3_manual_source_review_required |
| PRM-04 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-05 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-06 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-07 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-08 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-09 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-10 | GS-03, GS-09, GS-12 | automated_3_of_3_expert_acceptance_separate |
| PRM-11 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-12 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-13 | GS-01, GS-15, GS-16 | automated_3_of_3_expert_acceptance_separate |
| PRM-14 | GS-01, GS-09, GS-12 | automated_3_of_3_expert_acceptance_separate |
| PRM-15 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-16 | GS-01, GS-04, GS-06 | automated_3_of_3_expert_acceptance_separate |
| PRM-17 | GS-01, GS-08, GS-13 | automated_3_of_3_expert_acceptance_separate |

## Воспроизведение

```bash
uv run --env-file .env --extra backend python -m pe.acceptance matrix --out out/acceptance
uv run --env-file .env --extra backend python -m pe.acceptance cards --out out/acceptance
```

Matrix проверяет каждый PRM на трёх синтетических случаях seed команды. Для VLM передаются реальные изображения страниц корпуса. Cards сравнивает два генератора на 12 случаях с независимым критиком и ограниченным исправлением. Набор атак, внесённых ошибок, рубрика и их фактические результаты отражены в [отчёте](evaluation/acceptance_summary.md).

Для A/B фиксируются seed, версия/хэш промпта, набор источников, температура и лимиты; контекст и критерии одинаковы. Изменения после анализа ошибки оформляются новой версией. Hold-out не используется для выбора промпта. Метрики на seed и синтетических атаках отделяются от экспертной проверки реальных карточек.
