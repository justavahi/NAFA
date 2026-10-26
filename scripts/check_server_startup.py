"""Start real Uvicorn/TCP; verify PE wiring and fail-closed behavior without models."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main():
    output = ROOT / ".local/audit"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nafa-acceptance-") as directory:
        environment = os.environ.copy()
        environment.update(NAFA_BACKEND_ROOT=str(ROOT / "src/store"), NAFA_ROOT=str(ROOT / "src/store"),
            NAFA_DB_PATH=directory+"/api.db", NAFA_DATA_DIR=directory,
            NAFA_PE_LOCAL="true", LLM_BASE_URL="http://127.0.0.1:9/v1", LLM_API_KEY="local-fixture",
            NAFA_ADMIN_TOKEN="local-test-admin", NAFA_PSEUDONYM_SALT="local-test-salt",
            PYTHONPATH=os.pathsep.join([str(ROOT / "src/store"), str(ROOT / "src/report_scripts")]))
        environment.pop("NAFA_PE_PROFILE", None)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        with (output / "server.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "pe.backend_app:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=environment, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
                    deadline = time.monotonic()+40
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("server exited; inspect .local/audit/server.log")
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError("server did not become healthy")
                        time.sleep(.1)
                    checks = {"tcp_health": True}
                    assert client.get("/").status_code == 200
                    assert client.get("/assets/app.js").status_code == 200
                    assert client.get("/runtime").status_code == 401
                    checks["ui_served_and_private_api_requires_login"] = True
                    assert client.post("/session", json={"token":"local-test-admin"}).status_code == 200
                    schema = client.get("/openapi.json").json()
                    assert "/internal/pe/cases" in schema["paths"]
                    checks["pe_routes_registered"] = True
                    case = json.loads((ROOT / "src/report_scripts/examples/demo/case.json").read_text())
                    client_id = "integration-demo-client"
                    alias = "u_"+hmac.new(b"local-test-salt", client_id.encode(), hashlib.sha256).hexdigest()[:10]
                    case["ledger"]["client_id"] = alias
                    for entry in case["ledger"]["entries"]:
                        if entry["kind"] == "COMPANY_FEATURE":
                            entry["client_id"] = alias
                    text = case["ledger"]["documents"][0]["text"]
                    # Keep is_mock=true. Real startup must refuse to publish this.
                    conn = sqlite3.connect(directory+"/api.db")
                    conn.execute("INSERT INTO clients(synthetic_u) VALUES(?)", (client_id,))
                    conn.execute("""INSERT INTO documents(doc_id,source_id,title,url,canonical_url,published_at,published_at_utc,
                        fetched_at,text,snippet,tier_weight,content_hash,ready_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("demo_news", "fixture", "Test", "synthetic://fixture", "synthetic://fixture", "2026-10-01T00:00:00Z",
                        "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", text, text, .5, "fixture", "ready"))
                    conn.commit()
                    body = dict(client_id=client_id, event_doc_id="demo_news", prepared=dict(case=case))
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}") as anonymous:
                        assert anonymous.post("/internal/pe/cases", json=body).status_code == 401
                    checks["case_registration_requires_admin"] = True
                    response = client.post("/internal/pe/cases", json=body, headers={"X-Admin-Token": "local-test-admin"})
                    assert response.status_code == 200, response.text
                    checks["trusted_case_registered"] = True
                    generate = dict(client_id=client_id, event_doc_id="demo_news")
                    response = client.post("/cards/generate", json=generate)
                    assert response.status_code == 409, response.text
                    assert response.json()["detail"]["publishable"] is False and response.json()["detail"]["card"] is None
                    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
                    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
                    checks["mock_source_withheld_before_any_model_call"] = True
                    assert client.get(f"/clients/{client_id}/profile").status_code == 200
                    feed_response = client.get("/feed", params=dict(client_id=client_id))
                    assert feed_response.status_code == 422 and "PE_PROFILE_NOT_READY" in feed_response.text
                    checks["profile_reachable_feed_rejects_missing_da"] = True
                    response = client.post("/events/ingest", json=dict(title="Проверка BYO", published_at="2026-10-20T10:00:00Z",
                        client_id=client_id, text="Опубликован новый обзор кредитования компаний и спроса на финансирование."))
                    assert response.status_code == 200, response.text
                    generate["event_doc_id"] = response.json()["doc_id"]
                    response = client.post("/cards/generate", json=generate)
                    assert response.status_code == 422 and ("PE_CASE_NOT_READY" in response.text or "PE_PROFILE_NOT_READY" in response.text)
                    checks["missing_da_context_fails_closed"] = True
                    assert client.get("/budget").status_code == 200
                    assert client.get("/quality/summary").status_code == 200
                    checks["quality_and_budget_reachable"] = True
                    conn.close()
                    report = dict(real_tcp_server=True, checks=checks, model_calls=0, real_llm_quality_tested=False,
                        automatic_da_case_builder=True, full_case_acceptance=False)
                    path = ROOT / "docs/verification/server_startup.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
                    print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()


if __name__ == "__main__":
    main()
