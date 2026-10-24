# -*- coding: utf-8 -*-
"""Загрузка входных данных и машиночитаемых справочников пакета docs/data."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .columns import (CLIENT_COLUMNS, LINK_COLUMNS, PAYMENT_COLUMNS,
                      find_file, rename_canonical)

CLIENT_FILE_CANDIDATES = ["df_Uid_data_hack.csv", "df_uid_data_hack.csv",
                          "df_Uid_data.csv", "clients.csv"]
PAYMENT_FILE_CANDIDATES = ["df_Uid_payment_hack.csv", "df_Payments_data_hack.csv",
                           "payments.csv", "df_payments_hack.csv",
                           "df_Pay_hack.csv", "transactions.csv"]
LINK_FILE_CANDIDATES = ["df_Fid_Uid_link_hack.csv", "df_Fid_Uid_link.csv",
                        "link_fid_uid.csv"]

REFERENCE_FILES = {
    "metrics_catalog": "metrics_catalog.csv",
    "matrix": "sector_sensitivity_matrix.csv",
    "okved_map": "okved_to_sector_profile.csv",
    "personas": "persona_weights.csv",
    "event_types": "event_types.csv",
    "corpus": "alfa_corpus_registry.csv",
    "archetypes": "synthetic_archetypes.csv",
    "golden_seed": "golden_set_seed.csv",
    "factors": "factors.csv",
}


def _decode(raw: bytes) -> tuple[str, str]:
    """utf-8(-sig) -> cp1251 -> utf-8 c заменой. Возвращает (текст, кодировка)."""
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _sniff_sep(sample: str) -> str:
    """Разделитель = самый частый кандидат вне кавычек (реальные CSV кейса
    могут быть с ';', '\\t', '|' — WP-D1)."""
    counts: dict[str, int] = {}
    in_quotes = False
    prev = ""
    for ch in sample:
        if ch == '"':
            if prev != '"':
                in_quotes = not in_quotes
        elif not in_quotes and ch in ",;\t|":
            counts[ch] = counts.get(ch, 0) + 1
        prev = ch
    return max(counts, key=counts.get) if counts else ","


def _read(path: Path) -> pd.DataFrame:
    """Чтение CSV организаторов: разделитель и кодировка определяются
    автоматически, выбранные параметры печатаются в лог (помощь при WP-D1)."""
    text, enc = _decode(path.read_bytes())
    kwargs: dict = {"encoding": enc}
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    m = re.match(r"(?i)^sep[=:]\s*([,;\t|])\s*$", first)
    if m:  # Excel иногда пишет первой строкой 'sep=;'
        kwargs["sep"] = m.group(1)
        kwargs["skiprows"] = 1
    else:
        kwargs["sep"] = _sniff_sep(text[:65536])
    sep_repr = "\\t" if kwargs["sep"] == "\t" else kwargs["sep"]
    print(f"[DA] {path.name}: разделитель '{sep_repr}', кодировка {enc}")
    return pd.read_csv(path, **kwargs)


def load_clients(data_dir: str | Path) -> tuple[pd.DataFrame, Path | None]:
    """df_Uid_data_hack.csv, приведённый к каноническим именам колонок."""
    path = find_file(data_dir, CLIENT_FILE_CANDIDATES)
    if path is None:
        raise FileNotFoundError(
            f"В {data_dir} не найден файл клиентов "
            f"(искал: {', '.join(CLIENT_FILE_CANDIDATES)})")
    df = rename_canonical(_read(path), CLIENT_COLUMNS)
    df["SYNTHETIC_U"] = df["SYNTHETIC_U"].astype(str).str.strip()
    # ОКВЭД мог распарситься как число: '01.34' -> 1.34 -> '1.'; чиним ведущий ноль
    code = df["MAINOKVED_CODE"].astype("string").str.strip()
    code = code.str.replace(r"^(\d)\.(?=\d)", r"0\1.", regex=True)
    df["MAINOKVED_CODE"] = code
    return df, path


def dedup_clients(clients: pd.DataFrame) -> pd.DataFrame:
    """Убирает строки без SYNTHETIC_U и повторяющиеся ID (keep=first).

    Вызывается ПОСЛЕ профилирования (WP-D1 должен видеть сырые данные),
    но ДО расчётов: дубли меток в индексе ломают reindex/groupby.
    Список убранных ID печатается и остаётся вопросом организаторам.
    """
    ids = clients["SYNTHETIC_U"]
    bad = ids.isna() | ids.isin(["", "nan", "None", "NaN"])
    dup = ids.duplicated(keep="first")
    drop = bad | dup
    if drop.any():
        dropped_ids = sorted(set(ids[drop]))
        print(f"[DA] Дедупликация клиентов: убрано {int(drop.sum())} строк "
              f"(пустые/дубли ID, keep=first): {dropped_ids[:10]}")
        return clients.loc[~drop].reset_index(drop=True)
    return clients


def load_payments(data_dir: str | Path) -> tuple[pd.DataFrame | None, Path | None]:
    """Платежи (имя файла в кейсе уточняется, WP-D1). None, если файла нет."""
    path = find_file(data_dir, PAYMENT_FILE_CANDIDATES)
    if path is None:
        return None, None
    df = rename_canonical(_read(path), PAYMENT_COLUMNS)
    for c in ("PAYER", "BENEFICIARY"):
        df[c] = df[c].astype(str).str.strip()
    return df, path


def load_link(data_dir: str | Path) -> tuple[pd.DataFrame | None, Path | None]:
    """df_Fid_Uid_link_hack.csv — связи юрлиц с физлицами."""
    path = find_file(data_dir, LINK_FILE_CANDIDATES)
    if path is None:
        return None, None
    df = rename_canonical(_read(path), LINK_COLUMNS)
    df["SYNTHETIC_U"] = df["SYNTHETIC_U"].astype(str).str.strip()
    return df, path


def load_references(ref_dir: str | Path | None) -> dict[str, pd.DataFrame | None]:
    """Машиночитаемые справочники: матрица SP x фактор, ОКВЭД -> SP, персоны..."""
    out: dict[str, pd.DataFrame | None] = {k: None for k in REFERENCE_FILES}
    if ref_dir is None:
        return out
    ref_dir = Path(ref_dir)
    for key, fname in REFERENCE_FILES.items():
        p = ref_dir / fname
        if p.exists():
            df = _read(p)
            df.columns = [str(c).strip() for c in df.columns]
            out[key] = df
    return out
