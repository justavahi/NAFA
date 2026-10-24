# -*- coding: utf-8 -*-
"""Генератор синтетических демо-данных (только для проверки скриптов!).

НЕ использовать для расчётов по кейсу: реальные данные — df_Uid_data_hack.csv
и платежи из Приложения 1. Здесь данные синтетические, чтобы контур DA можно
было прогнать end-to-end до прихода файлов (python -m analytics.run_all --demo).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OKVEDS = ["01", "10", "23", "41", "46", "47", "52", "62", "64", "71", "85", "96"]
REGIONS = ["Москва", "Санкт-Петербург", "Свердловская обл.",
           "Татарстан", "Новосибирская обл."]
STAGES = ["Рост", "Зрелость", "Запуск", "Кризис"]
OKFS = ["Частная", "Государственная", "Смешанная"]
COUNTRIES = ["Китай", "Германия", "Турция", "Казахстан", None]


def make_demo(data_dir: Path, n_clients: int = 60, n_payments: int = 400,
              seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    data_dir.mkdir(parents=True, exist_ok=True)

    uids = [f"U_{i}" for i in range(1, n_clients + 1)]
    okved = rng.choice(OKVEDS, n_clients)
    revenue = 10 ** rng.normal(9.6, 0.7, n_clients).clip(7, 12)  # 0.05..1000 млрд
    df = pd.DataFrame({
        "SYNTHETIC_U": uids,
        "REVENUE_COMPANY": revenue.round(0),
        "MAINOKVED_CODE": [f"{o}.34" for o in okved],
        "MAINOKVED_NAME": ["Демо-отрасль"] * n_clients,
        "REGION": rng.choice(REGIONS, n_clients),
        "CITY": ["Демо-город"] * n_clients,
        "ECO_MARK_LABEL": rng.choice(STAGES, n_clients),
        "COMPANY_AGE_YEARS_GRADATION": rng.choice(
            ["от 1 до 3", "от 3 до 10", "более 10"], n_clients),
        "AMOUNT_WORKERS_FNS_GRADATION": rng.choice(
            ["от 101 до 250", "от 251 до 500", "более 500"], n_clients),
        "AMOUNT_WORKERS_SPARK_GRADATION": rng.choice(
            ["от 101 до 250", "от 251 до 500", "более 500"], n_clients),
        "OKFS_NAME": rng.choice(OKFS, n_clients),
        "ORG_TYPE": ["ООО"] * n_clients,
        "CL_PRODUCT_RKO_MARK": rng.choice([0, 1], n_clients, p=[0.2, 0.8]),
        "CL_PRODUCT_GAR_MARK": rng.choice([0, 1], n_clients, p=[0.7, 0.3]),
        "CL_PRODUCT_LOA_MARK": rng.choice([0, 1], n_clients, p=[0.6, 0.4]),
        "CL_PRODUCT_LEA_MARK": rng.choice([0, 1], n_clients, p=[0.75, 0.25]),
        "CL_PRODUCT_DEP_MARK": rng.choice([0, 1], n_clients, p=[0.65, 0.35]),
        "CL_PRODUCT_ZPP_MARK": rng.choice([0, 1], n_clients, p=[0.6, 0.4]),
        "CL_PRODUCT_FACTORING_MARK": rng.choice([0, 1, None], n_clients,
                                                p=[0.85, 0.1, 0.05]),
        "FTS_FLAG": rng.choice([0, 1], n_clients, p=[0.7, 0.3]),
        "FTS_COUNTRY_NAME": rng.choice(COUNTRIES, n_clients),
        "CL_TYPE": rng.choice(["Действующий", "Потенциальный"], n_clients,
                              p=[0.8, 0.2]),
        "GROUP_ID": rng.choice([f"G_{i}" for i in range(1, 6)] + [None], n_clients),
        "NEGATIVEMARK": rng.choice([0, 1], n_clients, p=[0.95, 0.05]),
    })
    df["FTS_COUNTRY_NAME"] = df["FTS_COUNTRY_NAME"].astype(object)
    df.loc[df["FTS_FLAG"] == 0, "FTS_COUNTRY_NAME"] = None
    clients_path = data_dir / "df_Uid_data_hack.csv"
    df.to_csv(clients_path, index=False, encoding="utf-8-sig")

    # --- платежи: клиенты <-> клиенты + внешние контрагенты -------------------
    rows = []
    external = [f"EXT_{i}" for i in range(1, 41)]
    for _ in range(n_payments):
        payer = str(rng.choice(uids))
        ben = str(rng.choice(uids + external))
        rows.append({
            "PAYER": payer, "BENEFICIARY": ben,
            "PAY_AMT": float(10 ** rng.normal(7.5, 1.0)),
            "PAY_CNT": int(rng.integers(1, 30)),
        })
    pay = pd.DataFrame(rows)
    pay_path = data_dir / "df_Payments_data_hack.csv"
    pay.to_csv(pay_path, index=False, encoding="utf-8-sig")

    # --- связи с физлицами -------------------------------------------------------
    fids = [f"F_{i}" for i in range(1, 31)]
    link = pd.DataFrame({
        "SYNTHETIC_F": rng.choice(fids, 80),
        "SYNTHETIC_U": rng.choice(uids, 80),
        "RC_NAME": ["Иванов И.И."] * 80,
        "CITY_NAME": ["Демо-город"] * 80,
        "REGION": rng.choice(REGIONS, 80),
    })
    link_path = data_dir / "df_Fid_Uid_link_hack.csv"
    link.to_csv(link_path, index=False, encoding="utf-8-sig")

    # --- ряд ставки (M-01) и прогнозы Альфы (B-01) для CH-08 --------------------
    dates = pd.date_range("2025-06-01", "2026-09-14", freq="MS")
    vals = np.concatenate([np.full(6, 21.0), np.linspace(21, 17, 6),
                           np.full(4, 16.0)])
    fact = pd.DataFrame({"date": dates, "value": vals[:len(dates)]})
    fact_path = data_dir / "rate_fact.csv"
    fact.to_csv(fact_path, index=False)

    fc = pd.DataFrame({
        "as_of": pd.to_datetime(["2026-07-31", "2026-09-07", "2026-09-11"]),
        "horizon_date": pd.to_datetime(["2026-12-31"] * 3),
        "value": [13.5, 13.5, 14.0],
    })
    fc_path = data_dir / "rate_forecast.csv"
    fc.to_csv(fc_path, index=False)

    return {"clients": str(clients_path), "payments": str(pay_path),
            "link": str(link_path), "rate_fact": str(fact_path),
            "rate_forecast": str(fc_path)}
