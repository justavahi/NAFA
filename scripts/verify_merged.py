"""Reproducible merged-repository checks. Never calls remote LLMs or creates labels."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent


def junit(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
    expected = sum(c.find("skipped") is not None and c.find("skipped").get("type") == "pytest.xfail" for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)-expected
    return dict(passed=len(cases)-failed-skipped-expected, failed=failed, skipped=skipped, expected_unmet_requirements=expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-only", action="store_true")
    args = parser.parse_args()
    local = ROOT / ".local/audit"
    local.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("backend", ROOT / "src/store", ["uv", "run", "--frozen", "--group", "dev", "pytest", "-q", "--junitxml="+str(local / "backend.xml")]),
        ("pe", ROOT / "src/report_scripts", ["uv", "run", "--frozen", "--extra", "backend", "pytest", "-q", "--junitxml="+str(local / "pe.xml")]),
        ("da_vectors", ROOT / "src/da_scripts", [str(ROOT / "src/store/.venv/bin/python"), "-m", "tests.test_vectors"]),
        ("da_regressions", ROOT / "src/da_scripts", [str(ROOT / "src/store/.venv/bin/python"), "-m", "unittest", "tests.test_client_type", "tests.test_holdout", "-v"]),
    ]
    if not args.tests_only:
        for name, script in [("tcp_startup", "check_server_startup.py"), ("profiles", "audit_profiles.py"), ("corpus", "audit_corpus.py")]:
            jobs.append((name, ROOT, ["uv", "run", "--frozen", "--project", "src/store", "python", "scripts/"+script]))
        jobs.append(("da_pipeline", ROOT, ["uv", "run", "--frozen", "--project", "src/store", "--with", "matplotlib==3.11.2",
            "--with", "tabulate==0.10.0", "python", "scripts/check_da_pipeline.py"]))
    results = {}
    for name, cwd, command in jobs:
        print("Checking", name, flush=True)
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180)
            output = result.stdout+result.stderr
            (local / (name+".log")).write_text(output)
            results[name] = dict(exit_code=result.returncode, completed=result.returncode == 0)
            if name in {"backend", "pe"} and (local / (name+".xml")).exists():
                results[name].update(junit(local / (name+".xml")))
            elif name == "da_vectors":
                match = re.search(r"Итог: (\d+) OK / (\d+) FAIL", output)
                if match:
                    results[name].update(passed=int(match[1]), failed=int(match[2]))
            elif name == "da_regressions":
                match = re.search(r"Ran (\d+) tests?", output)
                if match:
                    results[name]["tests_run"] = int(match[1])
            print(name, results[name], flush=True)
            if result.returncode:
                print(output[-3000:], flush=True)
        except subprocess.TimeoutExpired:
            results[name] = dict(completed=False, error="timeout")
    paths = []
    for folder in ["src/store/app", "src/store/tests", "src/store/config", "src/store/web", "src/report_scripts/schemas", "src/report_scripts/pe", "src/report_scripts/prompts",
                   "src/da_scripts/analytics", "src/da_scripts/tests", "src/report_scripts/scripts", "scripts"]:
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in {".py", ".txt", ".json", ".yaml", ".html", ".css", ".js", ".svg"} and "__pycache__" not in p.parts)
    paths.extend(ROOT / name for name in [".python-version", "Dockerfile", "compose.yaml", ".dockerignore", "src/store/pyproject.toml", "src/store/uv.lock",
        "src/report_scripts/pyproject.toml", "src/report_scripts/uv.lock", "src/da_scripts/requirements_da.txt"])
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    report = dict(base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        branch=subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        includes_uncommitted_fixes=True, checks=results, remote_llm_calls=0,
        automated_checks_completed=all(v["completed"] for v in results.values()), full_case_acceptance=False,
        note="Passing infrastructure tests does not establish model quality, human gold labels, live integrations, UI or Docker acceptance.",
        tested_source_hashes=hashes)
    output = ROOT / "docs/verification/test_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print("Report:", output.relative_to(ROOT), flush=True)
    return 0 if report["automated_checks_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
