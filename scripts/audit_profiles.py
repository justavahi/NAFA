"""Exercise 8 archetypes x 6 personas on merged HTTP routes using synthetic data."""
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def divergence(a, b):
    a, b = set(a), set(b)
    return 1-len(a & b)/len(a | b) if a | b else 0.0


def main():
    with tempfile.TemporaryDirectory(prefix="nafa-profiles-") as directory:
        os.environ.update(NAFA_ROOT=str(ROOT / "src/store"), NAFA_DATA_DIR=directory,
            NAFA_DB_PATH=directory+"/test.db", LLM_BASE_URL="")
        sys.path.insert(0, str(ROOT / "src/store"))
        from fastapi.testclient import TestClient
        from app.api import app
        from app.db import connect
        from app.ingest.clients import load_all
        from app.synth import generate_clients, load_synthetic_news
        with TestClient(app) as api:
            conn = connect()
            files = generate_clients(Path(directory) / "synthetic")
            load_all(conn, files["clients"], files["links"], files["payments"])
            load_synthetic_news(conn)
            feeds, card_checks = {}, []
            for i in range(1, 9):
                uid = f"SYN-U-{i:04d}"
                for j in range(1, 7):
                    response = api.get("/feed", params=dict(client_id=uid, persona=f"PER-0{j}", k=5))
                    assert response.status_code == 200, response.text
                    feeds[f"CA-0{i}/PER-0{j}"] = [row["doc_id"] for row in response.json()]
            sets = [feeds[f"CA-0{i}/PER-03"] for i in range(1, 9)]
            pd5 = sum(divergence(a, b) for a, b in itertools.combinations(sets, 2))/28
            # Equal feature clone before either client has card-view history.
            columns = [row[1] for row in conn.execute("PRAGMA table_info(clients)")]
            original = dict(conn.execute("SELECT * FROM clients WHERE synthetic_u='SYN-U-0001'").fetchone())
            original["synthetic_u"] = "SYN-U-CLONE"
            conn.execute(f"INSERT INTO clients({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [original[c] for c in columns])
            clone = api.get("/feed", params=dict(client_id="SYN-U-CLONE", persona="PER-03", k=5)).json()
            clone_pd = divergence(feeds["CA-01/PER-03"], [row["doc_id"] for row in clone])
            for i in range(1, 9):
                uid = f"SYN-U-{i:04d}"
                ids = feeds[f"CA-0{i}/PER-03"]
                if not ids:
                    card_checks.append(dict(archetype=f"CA-0{i}", status="empty_feed"))
                    continue
                response = api.post("/cards/generate", json=dict(client_id=uid, event_doc_id=ids[0]))
                assert response.status_code == 200, response.text
                card = response.json()
                question = api.post(f"/cards/{card['card_id']}/ask", json=dict(question="Что это означает для компании?"))
                assert question.status_code == 200
                trace = api.get(f"/cards/{card['card_id']}/trace").json()
                card_checks.append(dict(archetype=f"CA-0{i}", status=card.get("status"), publishable=card.get("publishable"),
                    trace_steps=len(trace), answer_mode=question.json().get("mode")))
            report = dict(synthetic=True, backend_mode="provisional_baseline", feed_requests=48,
                archetypes=8, personas=6, pd_at_5=pd5, pd_target=.4, pd_target_met=pd5 >= .4,
                clone_pd_at_5=clone_pd, clone_stability_target_met=clone_pd <= .1,
                cases=card_checks, model_calls=conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0],
                independent_personalization_quality_established=False, full_case_acceptance=False)
            conn.close()
    path = ROOT / "docs/verification/profile_scenarios.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
