# Графики DA (CH-xx)

Сюда кладутся PNG и JSON-метаданные из контура DA:

- `CH-xx_<slug>.png` — 1600×900, 200 dpi;
- `CH-xx.json` — метаданные (id, title, metric_ids, as_of, takeaway ≤ 25 слов, alt_text).

Реестр и статусы: [`../da/03_graphics.md`](../da/03_graphics.md).

Команды копирования из артефактов прогона (PowerShell, из корня репо):

```
copy da_scripts\out\charts\*.png docs\assets\charts\
copy da_scripts\out\charts\*.json docs\assets\charts\
```

CH-08 появится после передачи Backend рядов `rate_fact.csv` / `rate_forecast.csv`.
