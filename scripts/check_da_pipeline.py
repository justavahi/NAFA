"""Run current merged DA on supplied data or a clearly labelled synthetic fixture."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src/da_scripts"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    synthetic = args.data_dir is None
    name = "da_synthetic" if synthetic else "da_participant"
    output = ROOT / ".local/audit" / name
    output.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    from analytics.data_io import load_clients, load_payments, load_link
    from analytics.run_all import run_pipeline
    source = args.data_dir
    with (output / "processing.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
        if synthetic:
            from examples.make_synthetic_demo import make_demo
            source = output / "input"
            make_demo(source)
        clients, cp = load_clients(source)
        payments, pp = load_payments(source)
        links, lp = load_link(source)
        counts = dict(clients=len(clients), payments=len(payments) if payments is not None else 0, links=len(links) if links is not None else 0)
        ambiguous = int(clients.loc[clients.SYNTHETIC_U.duplicated(keep=False), "SYNTHETIC_U"].nunique())
        del clients, payments, links
        artifacts = run_pipeline(source, output / "processed", do_charts=False, do_quality=False)
    frames = {name: pd.read_csv(artifacts[name], dtype={"SYNTHETIC_U": "string"}) for name in ("features", "payments", "exposures")}
    keys = [set(frame.SYNTHETIC_U) for frame in frames.values()]
    assert keys[0] == keys[1] == keys[2]
    assert all(frame.SYNTHETIC_U.is_unique for frame in frames.values())
    exp = frames["exposures"]
    columns = [c for c in exp if c.startswith("eps_")]
    assert exp[columns].notna().all().all() and ((exp[columns] >= 0) & (exp[columns] <= 1)).all().all()
    assert exp.E15_rate_net.between(-1, 1).all()
    types = frames["features"].F10_cl_type.value_counts(dropna=False)
    report = dict(synthetic=synthetic, pandas_version=pd.__version__, input_rows=counts,
        unique_company_rows=len(frames["features"]), ambiguous_client_ids=ambiguous,
        aligned_unique_feature_keys=True, exposure_range_check=True, client_type_counts={str(k): int(v) for k,v in types.items()},
        source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (cp, pp, lp) if p is not None},
        charts_generated=False, quality_labels_generated=False, model_calls=0, full_case_acceptance=False,
        limitations=["Revenue units and temporal alignment not confirmed", "Ambiguous IDs deduplicated by first row; must be excluded from real-card acceptance sampling"])
    path = ROOT / "docs/verification" / (name+".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
