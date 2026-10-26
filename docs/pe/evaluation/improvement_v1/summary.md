# Пилот улучшения интерпретации

All new cases are synthetic. 60 variants in 10 story families, not 60 independent real observations.

Промпты и рубрика зафиксированы до вызовов. Старый балл 4.125 получен по другой рубрике и напрямую с новыми числами не сравнивается.

| Набор | Судья | Версия | Оценено | Медиана | Среднее |
|---|---|---|---|---|---|
| dev | DeepSeek-V4.1-Flash | baseline | 1/3 | 3.8 | 3.8 |
| dev | DeepSeek-V4.1-Flash | candidate | 1/3 | 4.0 | 4.0 |
| dev | Kimi-K2.6 | baseline | 0/3 | None | None |
| dev | Kimi-K2.6 | candidate | 0/3 | None | None |
| validation | DeepSeek-V4.1-Flash | baseline | 0/3 | None | None |
| validation | DeepSeek-V4.1-Flash | candidate | 0/3 | None | None |
| validation | Kimi-K2.6 | baseline | 0/3 | None | None |
| validation | Kimi-K2.6 | candidate | 0/3 | None | None |
| test | DeepSeek-V4.1-Flash | baseline | 0/3 | None | None |
| test | DeepSeek-V4.1-Flash | candidate | 0/3 | None | None |
| test | Kimi-K2.6 | baseline | 0/3 | None | None |
| test | Kimi-K2.6 | candidate | 0/3 | None | None |

Вызовов шлюза: 25; учтено токенов: 109304; стоимость ₽ неизвестна.

Закрытый тест открыт: False. Продвижения в production нет. Синтетический пилот не доказывает медиану 4.5 на реальных данных.

Покрытие, ошибки, оба порядка парного сравнения и результаты каждой версии сохранены в summary.json и файлах запуска. Оценки людей не имитируются.
