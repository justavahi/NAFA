"""Prepare blank human annotation material; no API calls or inferred labels."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pe.context import fragment_news, role_card
from pe.evidence import Document


def prepare(out: Path):
    if out.exists():
        raise ValueError("new output directory required; preserve existing annotations")
    source = ROOT / "reference/corpus/DOC-07_p1.json"
    raw = source.read_bytes()
    page = json.loads(raw)
    rows = []
    for index, block in enumerate(page["blocks"]):
        doc = Document(id=f"DOC-07_p1_block_{index}", kind="news", text=block["text"])
        # NEWS here is a segmentation input type only. The publication remains
        # bank-authored; this conversion does not certify it as an external fact.
        for fragment in fragment_news(doc):
            span = fragment.source
            rows.append(dict(fragment_id=fragment.id, doc_id="DOC-07", page=1,
                block_index=index, start_in_block=span.start, end_in_block=span.end,
                quote=span.quote, factors="", content_type="", attribution="",
                context_sufficient="", reviewer="", review_notes=""))
    out.mkdir(parents=True)
    with (out / "fragments.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    roles = [role_card(f"PER-0{i}").model_dump(mode="json") for i in range(1, 7)]
    (out / "roles.json").write_text(json.dumps(roles, ensure_ascii=False, indent=2)+"\n")
    manifest = dict(source="reference/corpus/DOC-07_p1.json",
        source_sha256=hashlib.sha256(raw).hexdigest(), source_meta=page["_meta"],
        fragments=len(rows), human_reviewed=0, automatic_factor_labels=0,
        roles=len(roles), source_truth_independently_verified=False,
        offsets="Python Unicode offsets in original block text; end exclusive",
        content_type_options=["event", "forecast", "background", "mixed", "unknown"],
        annotation_instructions="Do not guess. Keep attribution/date/negation; mark insufficient context. factors: semicolon-separated BA factor IDs. Review original PDF where text is ambiguous.",
        live_api_calls=0, measured_quality_improvement=None)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args().out), ensure_ascii=False, indent=2))
