import sqlite3

import pytest

from app.ingest.clients import get_client_record, list_clients, load_all, load_clients, load_payments
from app.synth import generate_clients


@pytest.fixture
def files(tmp_path):
    return generate_clients(tmp_path / "syn")


def test_load_counts_nulls_and_report(conn, files):
    reps = load_all(conn, files["clients"], files["links"], files["payments"])
    assert [r["rows_loaded"] for r in reps] == [r["rows_in_file"] for r in reps]           # acceptance WP-B2: counts match the files
    assert conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == reps[0]["rows_in_file"] == 48
    ca08 = conn.execute("SELECT * FROM clients WHERE cl_type='prospective' AND mainokved_code='29.10' AND synthetic_u='SYN-U-0008'").fetchone()
    assert ca08["revenue_company"] is None and ca08["cl_product_loa_mark"] is None          # NULL stays NULL, not 0
    flags = conn.execute("SELECT * FROM v_clients_flags WHERE synthetic_u='SYN-U-0008'").fetchone()
    assert flags["loa"] is None and flags["dep"] is None                                    # prospect: products unknown, not "absent"
    f1 = conn.execute("SELECT * FROM v_clients_flags WHERE synthetic_u='SYN-U-0001'").fetchone()
    assert (f1["loa"], f1["lea"], f1["gar"], f1["zpp"], f1["dep"], f1["fts"]) == (1, 1, 1, 1, 0, 0)    # CA-01 archetype
    rep = conn.execute("SELECT * FROM load_reports WHERE table_name='clients'").fetchone()
    assert rep["status"] == "ok" and '"revenue_company": 0.0208' in rep["null_share_json"]


def test_reload_is_idempotent_and_changed_file_replaces(conn, files):
    first = load_clients(conn, files["clients"])
    second = load_clients(conn, files["clients"])
    assert second["skipped"] and conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == first["rows_loaded"]
    forced = load_clients(conn, files["clients"], force=True)
    assert not forced["skipped"] and conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == first["rows_loaded"]   # no doubling
    lines = files["clients"].read_text(encoding="utf-8").splitlines()
    files["clients"].write_text("\n".join(lines[:-5]) + "\n", encoding="utf-8")                                     # file changed
    third = load_clients(conn, files["clients"])
    assert not third["skipped"] and conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == first["rows_loaded"] - 5


def test_tolerant_csv_cp1251_semicolon_decimal_comma_extras_aliases(conn, tmp_path):
    p = tmp_path / "c.csv"
    text = "synthetic_u;MAINOKVED_CODE;REVENUE_COMPANY;LOA_MARK;SOME_NEW_COL\nA1;41.20;22 000,5;Y;x\nA2;62.01;;N;\nA1;00.00;1;Y;dup\n"
    p.write_bytes(text.encode("cp1251"))
    rep = load_clients(conn, p)
    assert rep["rows_in_file"] == 3 and rep["rows_loaded"] == 2 and rep["quality"]["null_or_duplicate_keys_skipped"] == 1
    r = conn.execute("SELECT * FROM clients WHERE synthetic_u='A1'").fetchone()
    assert r["revenue_company"] == 22000.5 and r["cl_product_loa_mark"] == "Y" and "SOME_NEW_COL" in r["extra_json"]
    assert any("missing columns" in w for w in rep["warnings"])
    assert conn.execute("SELECT loa FROM v_clients_flags WHERE synthetic_u='A1'").fetchone()[0] == 1
    assert conn.execute("SELECT loa FROM v_clients_flags WHERE synthetic_u='A2'").fetchone()[0] == 0


def test_required_column_missing_is_an_error_and_rolls_back(conn, tmp_path, files):
    load_clients(conn, files["clients"])
    bad = tmp_path / "bad.csv"
    bad.write_text("ID,REVENUE_COMPANY\n1,2\n")
    with pytest.raises(ValueError):
        load_clients(conn, bad)
    assert conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 48          # previous data untouched


def test_payments_quality_report_and_client_record(conn, files, tmp_path):
    load_clients(conn, files["clients"])
    p = tmp_path / "pay.csv"
    p.write_text("PAYER,BENEFICIARY,PAY_AMT,PAY_CNT\nSYN-U-0002,SYN-U-0100,1000,3\nSYN-U-0002,SYN-U-0002,50,1\nUNKNOWN,SYN-U-0002,-5,0\nSYN-U-0100,SYN-U-0002,200.5,2\n")
    rep = load_payments(conn, p)
    q = rep["quality"]
    assert (q["self_payments"], q["non_positive_amounts"], q["zero_pay_cnt"], q["payer_not_in_clients"]) == (1, 1, 1, 1)
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 4          # nothing silently dropped: DA decides the cleaning rules
    rec = get_client_record(conn, "SYN-U-0002")
    assert rec["payments_summary"]["has_payment_data"] and rec["flags"]["loa"] == 1
    assert get_client_record(conn, "SYN-U-0001")["payments_summary"] == {"has_payment_data": False, "inflow": None, "outflow": None}
    assert get_client_record(conn, "nope") is None and len(list_clients(conn, 5)) == 5


def test_pseudonym_is_stable_and_hides_id():
    from app.util import pseudonym
    assert pseudonym("SYN-U-0001") == pseudonym("SYN-U-0001") != pseudonym("SYN-U-0002") and "0001" not in pseudonym("SYN-U-0001")


def test_db_path_resolution_and_dotenv(tmp_path, monkeypatch):
    from app import config
    monkeypatch.delenv("NAFA_DB_PATH", raising=False)
    monkeypatch.delenv("NAFA_DATA_DIR", raising=False)
    assert config.db_path() == config.ROOT / "data" / "nafa.db"                      # documented default
    monkeypatch.setenv("NAFA_DB_PATH", "data/other.db")
    assert config.db_path() == config.ROOT / "data" / "other.db"                     # relative -> project root, not cwd
    env = tmp_path / ".env"
    env.write_text('# c\nFOO_NAFA_TEST="abc"\nEMPTY_ONE=\n')
    monkeypatch.delenv("FOO_NAFA_TEST", raising=False)
    config._load_dotenv(env)
    import os
    assert os.environ["FOO_NAFA_TEST"] == "abc" and "EMPTY_ONE" not in os.environ
    monkeypatch.setenv("FOO_NAFA_TEST", "real")
    config._load_dotenv(env)
    assert os.environ["FOO_NAFA_TEST"] == "real"                                     # real environment wins over .env
    monkeypatch.delenv("FOO_NAFA_TEST", raising=False)


def test_cli_db_info(capsys):
    import json
    from app.cli import main
    assert main(["db-info"]) == 0
    out = capsys.readouterr().out
    info = json.loads(out[out.index("{"):])
    assert info["exists"] and info["rows"]["sources"] >= 12 and "news_fts_data" not in info["rows"]
