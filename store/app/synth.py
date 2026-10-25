"""WP-B17: synthetic data. 8 client archetypes CA-01..CA-08 + filler counterparties in the Appendix-1 format, payments, links,
and synthetic news for the 8 golden-set events. Real client data and Alfa PDFs never go into the repository.
Facts used in news texts come only from the case docs (golden set / reference); every synthetic doc is labelled origin='synthetic'."""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .connectors.normalize import build_document
from .config import get_sources
from .ingest.news import store_documents, start_run, finish_run, RunSummary

UID_COLS = ["SYNTHETIC_U", "MAINOKVED_CODE", "MAINOKVED_NAME", "REVENUE_COMPANY", "AMOUNT_WORKERS_FNS_GRADATION",
            "AMOUNT_WORKERS_SPARK_GRADATION", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK", "CL_PRODUCT_LOA_MARK",
            "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK", "FACTORING_MARK", "FTS_FLAG",
            "FTS_COUNTRY_NAME", "GROUP_ID", "CITY", "REGION", "CL_TYPE", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL",
            "OKFS_NAME", "ORG_TYPE", "NEGATIVEMARK"]

# code, name, revenue (RUB; units of the real field are unknown - OQ-09), products dict, fts country, region/city, workers
ARCH = {
    "CA-01": ("41.20", "Construction of residential buildings", 22e9, dict(LOA=1, LEA=1, GAR=1, ZPP=1, DEP=0, RKO=1, FACTORING=0), None, ("Moscow region", "Khimki"), "1001-5000"),
    "CA-02": ("24.10", "Steel production", 40e9, dict(LOA=1, DEP=1, RKO=1, LEA=0, GAR=0, ZPP=1, FACTORING=0), "China", ("Chelyabinsk region", "Magnitogorsk"), "5001-10000"),
    "CA-03": ("07.29", "Mining of precious-metal ores", 35e9, dict(LOA=0, DEP=1, RKO=1, LEA=0, GAR=0, ZPP=1, FACTORING=0), "China", ("Krasnoyarsk region", "Krasnoyarsk"), "1001-5000"),
    "CA-04": ("47.11", "Retail trade", 120e9, dict(LOA=1, FACTORING=1, ZPP=1, DEP=0, RKO=1, LEA=0, GAR=0), None, ("Moscow", "Moscow"), "10001+"),
    "CA-05": ("01.11", "Growing of cereals", 18e9, dict(LOA=1, GAR=1, LEA=1, DEP=0, RKO=1, ZPP=1, FACTORING=0), None, ("Krasnodar region", "Krasnodar"), "1001-5000"),
    "CA-06": ("49.41", "Freight road transport", 9e9, dict(LEA=1, LOA=1, DEP=0, RKO=1, GAR=0, ZPP=1, FACTORING=0), None, ("Tatarstan", "Kazan"), "501-1000"),
    "CA-07": ("62.01", "Computer programming", 6e9, dict(DEP=1, ZPP=1, LOA=0, LEA=0, GAR=0, RKO=1, FACTORING=0), None, ("Saint Petersburg", "Saint Petersburg"), "501-1000"),
    "CA-08": ("29.10", "Manufacture of motor vehicles", None, None, None, (None, None), None),     # prospect with gaps (R05)
}
FILLER_CODES = ["01.11", "05.10", "07.10", "24.10", "19.20", "29.10", "35.11", "41.20", "47.11", "49.41", "55.10", "62.01", "64.19", "70.22"]


def _flag(v):
    return "" if v is None else str(v)


def generate_clients(out_dir: Path, seed: int = 7, n_fillers: int = 40) -> dict[str, Path]:
    rnd = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, ids = [], {}
    for i, (ca, (code, name, rev, prod, fts, (region, city), workers)) in enumerate(ARCH.items(), start=1):
        uid = f"SYN-U-{i:04d}"
        ids[ca] = uid
        prospect = ca == "CA-08"
        p = prod or {}
        rows.append({
            "SYNTHETIC_U": uid, "MAINOKVED_CODE": code, "MAINOKVED_NAME": name, "REVENUE_COMPANY": "" if rev is None else int(rev),
            "AMOUNT_WORKERS_FNS_GRADATION": workers or "", "AMOUNT_WORKERS_SPARK_GRADATION": workers or "",
            "CL_PRODUCT_RKO_MARK": _flag(None if prospect else p.get("RKO")), "CL_PRODUCT_GAR_MARK": _flag(None if prospect else p.get("GAR")),
            "CL_PRODUCT_LOA_MARK": _flag(None if prospect else p.get("LOA")), "CL_PRODUCT_LEA_MARK": _flag(None if prospect else p.get("LEA")),
            "CL_PRODUCT_DEP_MARK": _flag(None if prospect else p.get("DEP")), "CL_PRODUCT_ZPP_MARK": _flag(None if prospect else p.get("ZPP")),
            "FACTORING_MARK": _flag(None if prospect else p.get("FACTORING")),
            "FTS_FLAG": "" if prospect else (1 if fts else 0), "FTS_COUNTRY_NAME": fts or "", "GROUP_ID": f"SYN-G-{i:03d}" if not prospect else "",
            "CITY": city or "", "REGION": region or "", "CL_TYPE": "prospective" if prospect else "existing",
            "COMPANY_AGE_YEARS_GRADATION": "" if prospect else "10+", "ECO_MARK_LABEL": "" if prospect else "stable",
            "OKFS_NAME": "" if prospect else "private", "ORG_TYPE": "" if prospect else "JSC", "NEGATIVEMARK": "",
        })
    fillers = []
    for j in range(n_fillers):
        uid = f"SYN-U-{100 + j:04d}"
        fillers.append(uid)
        rows.append({**{c: "" for c in UID_COLS}, "SYNTHETIC_U": uid, "MAINOKVED_CODE": rnd.choice(FILLER_CODES), "MAINOKVED_NAME": "filler",
                     "REVENUE_COMPANY": rnd.randint(5, 80) * 10**9, "CL_TYPE": rnd.choice(["existing", "prospective"]),
                     "REGION": rnd.choice(["Moscow", "Saint Petersburg", "Kazan region"]), "FTS_FLAG": rnd.choice([0, 1])})
    uid_path = out_dir / "df_Uid_data_hack.csv"
    with open(uid_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=UID_COLS)
        w.writeheader(); w.writerows(rows)

    link_path = out_dir / "df_Fid_Uid_link_hack.csv"
    with open(link_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"])
        for k, (ca, uid) in enumerate(ids.items(), start=1):
            if ca != "CA-08":
                w.writerow([f"SYN-F-{k:04d}", ARCH[ca][5][1], ARCH[ca][5][0], uid])

    pay_path = out_dir / "payments.csv"
    no_payments = {ids["CA-01"]}                               # CA-01: payment data absent (numeric example in the DA spec)
    with open(pay_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"])
        for ca, uid in ids.items():
            if uid in no_payments:
                continue
            for _ in range(rnd.randint(4, 9)):
                other = rnd.choice(fillers)
                a, b = (uid, other) if rnd.random() < 0.5 else (other, uid)
                w.writerow([a, b, round(rnd.uniform(1e6, 5e8), 2), rnd.randint(1, 60)])
    return {"clients": uid_path, "links": link_path, "payments": pay_path}


MSK = timezone(timedelta(hours=3))

# EV-xx from the golden set. Texts paraphrase only facts that appear in the case docs.
SYNTHETIC_NEWS = [
    ("EV-01", "2026-09-11T13:30:00+03:00", "Банк России сохранил ключевую ставку на уровне 14,0% годовых",
     "Совет директоров Банка России принял решение сохранить ключевую ставку на уровне 14,0% годовых. Риторика регулятора стала жёстче."),
    ("EV-02", "2026-09-08T12:00:00+03:00", "Золото скорректировалось после выступления главы ФРС",
     "После роста на 9,7% в августе цена золота скорректировалась примерно к 4 400 долларам за унцию на фоне выступления главы ФРС."),
    ("EV-03", "2026-09-02T09:00:00+03:00", "Недельная инфляция в РФ с 25 по 31 августа составила -0,01%",
     "Годовая инфляция составила 6,32%. Бензин за неделю подорожал на 0,91%."),
    ("EV-04", "2026-09-14T10:00:00+03:00", "Доходности ОФЗ выросли после паузы ЦБ",
     "Минфин вернулся к размещению ОФЗ-ПД. Доходности государственных облигаций выросли после решения Банка России."),
    ("EV-05", "2026-09-04T11:00:00+03:00", "Эксперт РА подтвердил рейтинг Полюса на уровне ruAAA",
     "Прогноз по рейтингу стабильный."),
    ("EV-06", "2026-09-07T10:00:00+03:00", "Розничные продажи в июле выросли на 5,3% г/г, продажи легковых авто в августе снизились на 7%",
     "Показатели потребительского спроса за июль и август."),
    ("EV-07", "2026-10-20T12:00:00+03:00", "Официальный курс ЦБ на 20 октября: доллар 82,85 руб., юань 12,39 руб.",
     "Банк России установил официальные курсы валют на 20.10.2026."),
    ("EV-08", "2026-10-20T08:00:00+03:00", "Температура ниже нуля ожидается в ночь на среду в Москве",
     "Синоптики прогнозируют заморозки в столичном регионе."),
]


def load_synthetic_news(conn, now: datetime | None = None) -> dict:
    src = get_sources()["SRC-14"]
    fetched = now or datetime.now(MSK)
    run_id = start_run(conn, "SRC-14")
    docs = [build_document("SRC-14", src, {"title": t, "link": f"synthetic://{ev}", "pubDate": pub, "description": body, "category": ev},
                           fetched, origin="synthetic") for ev, pub, t, body in SYNTHETIC_NEWS]
    stats = store_documents(conn, "SRC-14", run_id, docs)
    finish_run(conn, RunSummary(run_id, "SRC-14", "ok", len(docs), len(docs), stats["new"], stats["duplicate"], 0))
    return stats
