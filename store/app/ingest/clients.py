"""WP-B2: load df_Uid_data_hack.csv, df_Fid_Uid_link_hack.csv and the payments file.
Rules: schema check with tolerant header matching; missing values stay NULL (never 0); raw product flags are stored as-is
(their dictionary is unknown, OQ-09); idempotent (same file hash -> skipped, changed file -> table replaced); a load report
with row counts and NULL share per field is written; logs carry pseudonyms, never raw IDs."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd

from ..db import tx
from ..util import iso, new_id, now_utc, pseudonym

log = logging.getLogger("nafa.load")

NA = ["", "NULL", "null", "NaN", "nan", "None", "NONE", "NA", "N/A", "\\N", "-"]

# canonical db column -> (accepted header aliases, kind)
CLIENT_SPEC = {
    "synthetic_u": (["SYNTHETIC_U"], "text"),
    "mainokved_code": (["MAINOKVED_CODE"], "text"),
    "mainokved_name": (["MAINOKVED_NAME"], "text"),
    "revenue_company": (["REVENUE_COMPANY"], "real"),
    "amount_workers_fns_gradation": (["AMOUNT_WORKERS_FNS_GRADATION"], "text"),
    "amount_workers_spark_gradation": (["AMOUNT_WORKERS_SPARK_GRADATION"], "text"),
    # Exact names of the LOA/LEA/... flags are not fixed in the case docs ("CL_PRODUCT_RKO_MARK, _GAR_, _LOA_ ..."): aliases cover both spellings.
    "cl_product_rko_mark": (["CL_PRODUCT_RKO_MARK", "RKO_MARK"], "text"),
    "cl_product_gar_mark": (["CL_PRODUCT_GAR_MARK", "GAR_MARK"], "text"),
    "cl_product_loa_mark": (["CL_PRODUCT_LOA_MARK", "LOA_MARK"], "text"),
    "cl_product_lea_mark": (["CL_PRODUCT_LEA_MARK", "LEA_MARK"], "text"),
    "cl_product_dep_mark": (["CL_PRODUCT_DEP_MARK", "DEP_MARK"], "text"),
    "cl_product_zpp_mark": (["CL_PRODUCT_ZPP_MARK", "ZPP_MARK"], "text"),
    "factoring_mark": (["FACTORING_MARK", "CL_PRODUCT_FACTORING_MARK"], "text"),
    "fts_flag": (["FTS_FLAG"], "text"),
    "fts_country_name": (["FTS_COUNTRY_NAME"], "text"),
    "group_id": (["GROUP_ID"], "text"),
    "city": (["CITY"], "text"),
    "region": (["REGION"], "text"),
    "cl_type": (["CL_TYPE"], "text"),
    "company_age_years_gradation": (["COMPANY_AGE_YEARS_GRADATION"], "text"),
    "eco_mark_label": (["ECO_MARK_LABEL"], "text"),
    "okfs_name": (["OKFS_NAME"], "text"),
    "org_type": (["ORG_TYPE"], "text"),
    "negativemark": (["NEGATIVEMARK"], "text"),
}
LINK_SPEC = {
    "synthetic_f": (["SYNTHETIC_F"], "text"), "synthetic_u": (["SYNTHETIC_U"], "text"),
    "city_name": (["CITY_NAME"], "text"), "rc_name": (["RC_NAME"], "text"),
}
PAY_SPEC = {
    "payer": (["PAYER"], "text"), "beneficiary": (["BENEFICIARY"], "text"),
    "pay_amt": (["PAY_AMT"], "real"), "pay_cnt": (["PAY_CNT"], "real"),
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _csv_kwargs(path: Path) -> dict:
    raw = open(path, "rb").read(1 << 20)
    enc = "utf-8-sig"
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        if e.start < len(raw) - 4:               # a real failure, not a multibyte char cut at the 1 MB boundary
            enc = "cp1251"
    first = raw.decode(enc, errors="ignore").splitlines()[0] if raw else ""
    sep = max([",", ";", "\t", "|"], key=first.count)
    return dict(encoding=enc, sep=sep, dtype=str, keep_default_na=False, na_values=NA)


def _to_real(series: pd.Series) -> tuple[pd.Series, int]:
    cleaned = series.astype("string").str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False)
    num = pd.to_numeric(cleaned, errors="coerce")
    bad = int((cleaned.notna() & num.isna()).sum())
    return num, bad


def _map_columns(df: pd.DataFrame, spec: dict) -> tuple[dict[str, str], list[str], list[str]]:
    upper = {c.strip().lstrip("\ufeff").upper(): c for c in df.columns}
    mapping, missing = {}, []
    for canon, (aliases, _) in spec.items():
        hit = next((upper[a] for a in aliases if a in upper), None)
        if hit is None:
            missing.append(canon)
        else:
            mapping[canon] = hit
    known = set(mapping.values())
    extras = [c for c in df.columns if c not in known]
    return mapping, missing, extras


def _already_loaded(conn, table: str, fhash: str) -> bool:
    return conn.execute("SELECT 1 FROM load_reports WHERE table_name=? AND file_hash=? AND status='ok'", (table, fhash)).fetchone() is not None


def _load_table(conn: sqlite3.Connection, path: str | Path, table: str, spec: dict, required: list[str],
                force: bool = False, chunksize: int = 200_000, has_extra: bool = False) -> dict:
    path = Path(path)
    fhash = file_sha256(path)
    if not force and _already_loaded(conn, table, fhash):
        return {"table": table, "skipped": True, "reason": "same file hash already loaded", "file_hash": fhash}

    kw = _csv_kwargs(path)
    warnings: list[str] = []
    rows_in_file = 0
    nulls: dict[str, int] = {c: 0 for c in spec}
    bad_numeric: dict[str, int] = {}
    seen_keys: set[str] = set()
    dup_keys = 0
    cols = list(spec) + (["extra_json", "loaded_at"] if has_extra else [])
    insert = f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
    loaded_at = iso(now_utc())
    rows_loaded = 0
    key_col = required[0] if table == "clients" else None

    with tx(conn):
        conn.execute(f"DELETE FROM {table}")                       # replace-all inside one transaction -> idempotent
        mapping = None
        for chunk in pd.read_csv(path, chunksize=chunksize, **kw):
            if mapping is None:
                mapping, missing, extras = _map_columns(chunk, spec)
                miss_req = [m for m in required if m in missing]
                if miss_req:
                    raise ValueError(f"{path.name}: required columns missing: {miss_req}")
                if missing:
                    warnings.append(f"missing columns (stored as NULL): {missing}")
                if extras:
                    warnings.append(f"unexpected columns (kept in extra_json: {has_extra}): {extras}")
            rows_in_file += len(chunk)
            data: dict[str, list] = {}
            for canon, (aliases, kind) in spec.items():
                if canon not in mapping:
                    data[canon] = [None] * len(chunk)
                    nulls[canon] += len(chunk)
                    continue
                col = chunk[mapping[canon]]
                if kind == "real":
                    col, bad = _to_real(col)
                    if bad:
                        bad_numeric[canon] = bad_numeric.get(canon, 0) + bad
                    vals = [None if pd.isna(v) else float(v) for v in col]
                else:
                    vals = [None if pd.isna(v) else str(v).strip() for v in col]
                nulls[canon] += sum(v is None for v in vals)
                data[canon] = vals
            extra_cols = [c for c in chunk.columns if c not in set(mapping.values())] if has_extra else []
            batch = []
            for i in range(len(chunk)):
                if key_col:
                    k = data[key_col][i]
                    if k is None or k in seen_keys:
                        dup_keys += 1                                # NULL or duplicate key: keep the first, report
                        continue
                    seen_keys.add(k)
                row = [data[c][i] for c in spec]
                if has_extra:
                    extra = {c: (None if pd.isna(chunk.iloc[i][c]) else chunk.iloc[i][c]) for c in extra_cols}
                    row += [json.dumps(extra, ensure_ascii=False) if extra else None, loaded_at]
                batch.append(row)
            conn.executemany(insert, batch)
            rows_loaded += len(batch)
        if mapping is None:
            raise ValueError(f"{path.name}: file is empty")

        quality: dict = {"bad_numeric_values": bad_numeric}
        if key_col:
            quality["null_or_duplicate_keys_skipped"] = dup_keys
        if table == "payments":
            q = lambda sql: conn.execute(sql).fetchone()[0]
            quality.update({
                "non_positive_amounts": q("SELECT COUNT(*) FROM payments WHERE pay_amt IS NOT NULL AND pay_amt<=0"),
                "self_payments": q("SELECT COUNT(*) FROM payments WHERE payer=beneficiary"),
                "zero_pay_cnt": q("SELECT COUNT(*) FROM payments WHERE pay_cnt=0"),
                "payer_not_in_clients": q("SELECT COUNT(*) FROM payments WHERE payer NOT IN (SELECT synthetic_u FROM clients)"),
                "beneficiary_not_in_clients": q("SELECT COUNT(*) FROM payments WHERE beneficiary NOT IN (SELECT synthetic_u FROM clients)"),
            })
        null_share = {c: round(n / rows_in_file, 4) if rows_in_file else None for c, n in nulls.items()}
        conn.execute(
            """INSERT INTO load_reports(load_id,table_name,file_name,file_hash,rows_in_file,rows_loaded,null_share_json,quality_json,warnings_json,status,loaded_at)
               VALUES(?,?,?,?,?,?,?,?,?,'ok',?)""",
            (new_id("load_"), table, path.name, fhash, rows_in_file, rows_loaded, json.dumps(null_share), json.dumps(quality),
             json.dumps(warnings, ensure_ascii=False), iso(now_utc())))
    log.info("loaded %s: %d/%d rows from %s", table, rows_loaded, rows_in_file, path.name)
    return {"table": table, "skipped": False, "rows_in_file": rows_in_file, "rows_loaded": rows_loaded,
            "null_share": null_share, "quality": quality, "warnings": warnings, "file_hash": fhash}


def load_clients(conn, path, force=False) -> dict:
    return _load_table(conn, path, "clients", CLIENT_SPEC, ["synthetic_u"], force, has_extra=True)


def load_links(conn, path, force=False) -> dict:
    return _load_table(conn, path, "client_links", LINK_SPEC, ["synthetic_u"], force)


def load_payments(conn, path, force=False) -> dict:
    return _load_table(conn, path, "payments", PAY_SPEC, ["payer", "beneficiary", "pay_amt"], force)


def load_all(conn, uid_path, link_path=None, payments_path=None, force=False) -> list[dict]:
    out = [load_clients(conn, uid_path, force)]
    if link_path:
        out.append(load_links(conn, link_path, force))
    if payments_path:
        out.append(load_payments(conn, payments_path, force))
    return out


# ---------- read access used by tools / API: raw record only (F/G/E metrics are DA's functions) ----------
def list_clients(conn: sqlite3.Connection, limit: int = 100, offset: int = 0) -> list[dict]:
    return [{"client_id": r["synthetic_u"], "pseudonym": pseudonym(r["synthetic_u"]), "okved": r["mainokved_code"],
             "region": r["region"], "cl_type": r["cl_type"]}
            for r in conn.execute("SELECT synthetic_u,mainokved_code,region,cl_type FROM clients ORDER BY synthetic_u LIMIT ? OFFSET ?", (limit, offset))]


def get_client_record(conn: sqlite3.Connection, client_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM clients WHERE synthetic_u=?", (client_id,)).fetchone()
    if not r:
        return None
    rec = dict(r)
    rec["flags"] = dict(conn.execute("SELECT * FROM v_clients_flags WHERE synthetic_u=?", (client_id,)).fetchone())
    pay = conn.execute(
        "SELECT (SELECT COALESCE(SUM(pay_amt),0) FROM payments WHERE beneficiary=?) AS inflow, (SELECT COALESCE(SUM(pay_amt),0) FROM payments WHERE payer=?) AS outflow,"
        " (SELECT COUNT(*) FROM payments WHERE beneficiary=? OR payer=?) AS n", (client_id,) * 4).fetchone()
    rec["payments_summary"] = {"has_payment_data": pay["n"] > 0, "inflow": pay["inflow"] if pay["n"] else None,
                               "outflow": pay["outflow"] if pay["n"] else None}
    return rec
