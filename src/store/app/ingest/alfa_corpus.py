"""WP-B5/B6: Alfa-Bank analytics corpus. PDF/slide image -> page PNG -> VLM (Qwen3-VL via the gateway) -> Chunk records.
Offline, once; cached by file hash (and the gateway caches every page request). Numbers are flagged needs_manual_check;
numbers read from charts without printed labels become `chart_estimate` and are never facts.
File naming: the DOC id must be in the file name (e.g. 'DOC-07_key_rate.pdf'). DOC-01 slides: several images with DOC-01 in the name."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import os
from io import BytesIO
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pymupdf

from ..config import ROOT, data_dir, get_config
from ..db import tx
from ..llm_gateway import BudgetExceeded, LLMError, LLMGateway, image_message
from ..models import Chunk, NumberFact
from ..search import index_chunk
from ..util import iso, now_utc

DOC_RE = re.compile(r"DOC[-_ ]?(\d{2})", re.IGNORECASE)
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
# Friendly filename aliases for the files supplied in the case Google Drive. Explicit DOC-xx
# in a filename always wins; aliases only fill in the ID when it is missing.
DOC_ALIASES = (
    (re.compile(r"FI[-_ ]?Strategy", re.I), "DOC-01"),          # Appendix 2 slide deck (3Q26 debt-market strategy)
    (re.compile(r"ALFA[-_ ]?macro[-_ ]?stat1H26", re.I), "DOC-02"),
    (re.compile(r"ALFA[-_ ]?consumption", re.I), "DOC-03"),
    (re.compile(r"Inflation[-_ ]?tracker[-_ ]?02", re.I), "DOC-04"),
    (re.compile(r"ALFA[-_ ]?7M26", re.I), "DOC-05"),
    (re.compile(r"Gold[-_ ]?digest", re.I), "DOC-06"),
    (re.compile(r"ALFA[-_ ]?Macro[-_ ]?Insights[-_ ]?Policy[-_ ]?Rate", re.I), "DOC-07"),
    (re.compile(r"Бонд[-_ ]?Бриф", re.I), "DOC-08"),
    (re.compile(r"ALFA[-_ ]?Russian[-_ ]?Steels", re.I), "DOC-09"),
)
REF_REGISTRY = ROOT / "data" / "ref" / "alfa_corpus_registry.csv"

VLM_PROMPT = """You extract structured content from ONE page of an analytical report of a bank (Russian, scanned image, no text layer).
Return ONLY a JSON object, no markdown fences, with this shape:
{"page_title": string|null, "as_of_date": "YYYY-MM-DD"|null,
 "blocks": [{"block_type": "text"|"table"|"chart_caption"|"chart_estimate", "text": string,
   "numbers": [{"value": number, "unit": string|null, "subject": string, "date": "YYYY-MM-DD"|null}],
   "bbox": [x0,y0,x1,y1]|null, "confidence": number between 0 and 1}]}
Rules:
- Copy text exactly in its original language. Tables: one block, rows separated by newlines, cells by " | ".
- "chart_caption": title, axis labels, legend and PRINTED data labels of a chart. Put printed values in "numbers".
- "chart_estimate": ONLY values you visually read off a chart that have no printed label. Never invent numbers.
- bbox is normalised to the page (0..1) for charts and tables, else null.
- Numbers use a dot as decimal separator. Include forecasts with their date/horizon in "subject".
- If something is unreadable, lower the confidence instead of guessing."""


# ---------- registry ----------
def _parse_ru_date(s: str | None) -> str | None:
    try:
        return datetime.strptime((s or "").strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def load_registry(conn: sqlite3.Connection, path: Path = REF_REGISTRY) -> int:
    stale_days = get_config().get("corpus.stale_after_days", 365)
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f, tx(conn):
        for r in csv.DictReader(f):
            d = _parse_ru_date(r["date"])
            stale = "УСТАРЕЛ" in (r["status"] or "").upper() or (d is not None and (date.today() - date.fromisoformat(d)) > timedelta(days=stale_days))
            conn.execute(
                """INSERT INTO alfa_docs(doc_id,title,doc_date,format,registry_status,stale) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title, doc_date=excluded.doc_date, format=excluded.format,
                     registry_status=excluded.registry_status, stale=excluded.stale""",
                (r["doc_id"], r["title"], d, r["format"], r["status"], int(stale)))
            n += 1
    return n


# ---------- page rendering ----------
class PageSource:
    def __init__(self, path: Path, page_index: int):
        self.path, self.page_index = path, page_index

    def render(self, dpi: int, max_side: int, clip_norm: list[float] | None = None) -> bytes:
        doc = pymupdf.open(self.path)
        try:
            page = doc.load_page(self.page_index)
            rect = page.rect
            zoom = min(dpi / 72.0, max_side / max(rect.width, rect.height))
            clip = None
            if clip_norm:
                x0, y0, x1, y1 = clip_norm
                clip = pymupdf.Rect(rect.x0 + x0 * rect.width, rect.y0 + y0 * rect.height, rect.x0 + x1 * rect.width, rect.y0 + y1 * rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()


def scan_dir(directory: Path) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    if not Path(directory).exists():
        return groups
    for p in sorted(Path(directory).iterdir()):
        if not p.is_file() or p.suffix.lower() not in IMG_EXT | {".pdf"}:
            continue
        m = DOC_RE.search(p.name)
        doc_id = f"DOC-{m.group(1)}" if m else None
        if doc_id is None:
            doc_id = next((alias for pattern, alias in DOC_ALIASES if pattern.search(p.stem)), None)
        if doc_id:
            groups.setdefault(doc_id, []).append(p)
    return groups


@lru_cache(maxsize=8)
def _tesseract_languages(executable: str) -> frozenset[str]:
    """Return installed Tesseract languages; cache the probe for this process."""
    try:
        proc = subprocess.run([executable, "--list-langs"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=10, check=False, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LLMError(f"Could not inspect Tesseract language packs: {exc}") from exc
    if proc.returncode != 0:
        raise LLMError(f"Could not inspect Tesseract language packs: {proc.stdout[-300:]}")
    # The first line is a heading; language names appear one per following line.
    return frozenset(line.strip() for line in proc.stdout.splitlines()[1:] if line.strip())


def _local_ocr(png: bytes) -> str:
    """Offline OCR fallback when no VLM is configured.

    Tesseract and its Russian/English language data must be installed on the host. OCR
    output is always unverified and never creates numeric facts; the original rendered
    page is kept separately for attribution/manual checking.
    """
    executable = shutil.which("tesseract")
    if not executable:
        raise LLMError(
            "Local OCR is unavailable: 'tesseract' was not found on PATH. Install Tesseract "
            "with the rus and eng language packs, or configure LLM_BASE_URL/LLM_API_KEY."
        )
    languages = _tesseract_languages(executable)
    missing = {"rus", "eng"} - languages
    if missing:
        raise LLMError(
            "Local OCR is unavailable: Tesseract language pack(s) missing: "
            + ", ".join(sorted(missing))
            + ". Install the rus and eng language packs, or configure LLM_BASE_URL/LLM_API_KEY."
        )

    # Downscale for a predictable offline runtime. The original page render remains
    # available separately for attribution and manual checking.
    try:
        from PIL import Image
        with Image.open(BytesIO(png)) as image:
            image.thumbnail((1000, 1000))
            tmp = BytesIO()
            image.convert("RGB").save(tmp, format="PNG", optimize=True)
            ocr_png = tmp.getvalue()
    except Exception:
        ocr_png = png
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"
    timeout_s = int(get_config().get("corpus.ocr_timeout_s", 60))
    try:
        proc = subprocess.run(
            [executable, "stdin", "stdout", "-l", "rus+eng", "--psm", "11"],
            input=ocr_png, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s,
            check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"Tesseract OCR timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise LLMError(f"Could not start Tesseract OCR: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise LLMError(f"Tesseract OCR failed: {detail}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def pages_of(files: list[Path]) -> list[PageSource]:
    pages = []
    for f in files:
        doc = pymupdf.open(f)
        n = doc.page_count
        doc.close()
        pages += [PageSource(f, i) for i in range(n)]
    return pages


def group_hash(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


# ---------- VLM output ----------
def parse_vlm_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


# ---------- offline extraction cache ----------
# The spec asks for "PDF -> pages -> VLM -> Chunk, cached by file hash, offline, once". When an
# approved VLM endpoint is configured it is used directly. When it is not available yet, the
# pipeline consumes the per-page JSON that a VLM run produced earlier (the same shape VLM_PROMPT
# returns), stored on disk under corpus.extraction_dir/<doc_id>/p<n>.json. This keeps the ingest
# path identical and lets the corpus be indexed offline without fabricating anything: whatever
# the page JSON says is what gets stored, and every number stays needs_manual_check.
DEFAULT_CACHED_MODEL = "offline-multimodal-review (cached; re-run with the approved VLM when configured)"


def extraction_dir() -> Path:
    return data_dir() / get_config().get("corpus.extraction_dir", "corpus_extraction")


def precomputed_path(doc_id: str, page: int) -> Path:
    return extraction_dir() / doc_id / f"p{page}.json"


def load_precomputed(doc_id: str, page: int, file_hash: str | None = None) -> dict | None:
    """Per-page cached extraction. Ignored when the stored file_hash does not match (stale cache)."""
    p = precomputed_path(doc_id, page)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    meta = data.get("_meta") or {}
    if file_hash and meta.get("file_hash") and meta["file_hash"] != file_hash:
        return None
    return data


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ".").replace("\u00a0", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def blocks_to_chunks(doc_id: str, page: int, data: dict, model: str, doc_date: str | None, thr: float) -> list[tuple[Chunk, list[float] | None]]:
    page_date = data.get("as_of_date") or doc_date
    out = []
    for i, b in enumerate(data.get("blocks") or [], start=1):
        text = (b.get("text") or "").strip()
        if not text:
            continue
        btype = b.get("block_type") if b.get("block_type") in ("text", "table", "chart_caption", "chart_estimate") else "text"
        is_fact = btype != "chart_estimate"
        nums = []
        for n in b.get("numbers") or []:
            v = _num(n.get("value"))
            if v is not None:
                nums.append(NumberFact(value=v, unit=n.get("unit"), subject=n.get("subject"), date=n.get("date"), is_fact=is_fact))
        try:
            conf = min(1.0, max(0.0, float(b.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        bbox = b.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(x, (int, float)) for x in bbox)
                and 0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1):
            bbox = None
        ch = Chunk(chunk_id=f"{doc_id}-p{page}-b{i}", doc_id=doc_id, page=page, block_type=btype, text=text, numbers=nums,
                   as_of_date=page_date, extraction_model=model,
                   extraction_confidence=conf, needs_manual_check=bool(nums) or btype == "chart_estimate" or conf < thr)
        out.append((ch, bbox))
    return out


def _save(conn, ch: Chunk) -> None:
    conn.execute(
        """INSERT INTO chunks(chunk_id,doc_id,page,block_type,text,numbers_json,as_of_date,extraction_model,extraction_confidence,
           needs_manual_check,verified_by,image_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ch.chunk_id, ch.doc_id, ch.page, ch.block_type, ch.text, json.dumps([n.model_dump() for n in ch.numbers], ensure_ascii=False),
         ch.as_of_date, ch.extraction_model, ch.extraction_confidence, int(ch.needs_manual_check), ch.verified_by, ch.image_path))
    if ch.extraction_model is not None:          # failed-page placeholders are stored but not searchable
        index_chunk(conn, ch.chunk_id, ch.text)


# ---------- main entry ----------
def parse_corpus(conn: sqlite3.Connection, directory: str | Path, gateway: LLMGateway, force: bool = False) -> dict:
    cfg = get_config()
    dpi, max_side = cfg.get("corpus.dpi", 150), cfg.get("corpus.max_side_px", 2200)
    thr = cfg.get("corpus.needs_check_confidence", 0.8)
    pages_dir = data_dir() / cfg.get("corpus.pages_dir", "corpus_pages")
    role = cfg.get("corpus.extraction_model_role", "vlm")
    load_registry(conn)
    groups = scan_dir(Path(directory))
    registry = {r["doc_id"]: r for r in conn.execute("SELECT * FROM alfa_docs")}
    report = {"parsed": [], "skipped": [], "missing": [], "unknown_files": [],
              "partial": [], "failed": [], "errors": [], "budget_blocked": False}
    report["missing"] = sorted(d for d in registry if d not in groups)
    report["unknown_files"] = sorted(d for d in groups if d not in registry)

    for doc_id, files in sorted(groups.items()):
        if doc_id not in registry:
            continue
        fhash = group_hash(files)
        row = registry[doc_id]
        if not force and row["file_hash"] == fhash and row["parse_status"] == "parsed":
            report["skipped"].append(doc_id)
            continue
        pages = pages_of(files)
        out_dir = pages_dir / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        all_chunks: list[Chunk] = []
        failed_pages: list[int] = []
        successful_pages: set[int] = set()
        for n, src in enumerate(pages, start=1):
            png = src.render(dpi, max_side)
            img_path = out_dir / f"p{n}.png"
            img_path.write_bytes(png)
            rel = str(img_path.relative_to(data_dir()))
            cached = None if gateway.base_url else load_precomputed(doc_id, n, fhash)
            try:
                if cached is not None:
                    data, model = cached, (cached.get("extraction_model") or DEFAULT_CACHED_MODEL)
                else:
                    res = gateway.chat(operation="pdf_parse", role=role, trace_id=f"corpus:{doc_id}:p{n}",
                                       messages=[{"role": "user", "content": image_message(VLM_PROMPT, png)}], max_tokens=4000)
                    data, model = parse_vlm_json(res.text), res.model
                pairs = blocks_to_chunks(doc_id, n, data, model, row["doc_date"], thr)
                if not pairs:
                    raise ValueError("no blocks extracted")
            except BudgetExceeded as e:
                report["budget_blocked"] = True
                failed_pages += list(range(n, len(pages) + 1))
                report["errors"].append({"doc_id": doc_id, "page": n, "stage": "budget",
                                         "error": str(e)})
                break
            except LLMError as e:
                # If no VLM endpoint is configured, fall back to local OCR for a useful offline
                # index. It never fabricates number records; all OCR chunks require manual review.
                if not gateway.base_url:
                    try:
                        ocr_text = _local_ocr(png)
                    except LLMError as ocr_error:
                        failed_pages.append(n)
                        report["errors"].append({"doc_id": doc_id, "page": n, "stage": "ocr",
                                                 "error": str(ocr_error)})
                        all_chunks.append(Chunk(chunk_id=f"{doc_id}-p{n}-b0", doc_id=doc_id, page=n, block_type="text",
                                                text=f"[extraction failed: {type(ocr_error).__name__}] {ocr_error}",
                                                extraction_model=None, extraction_confidence=0.0, needs_manual_check=True,
                                                image_path=rel, as_of_date=row["doc_date"]))
                        continue
                    if ocr_text:
                        successful_pages.add(n)
                        all_chunks.append(Chunk(chunk_id=f"{doc_id}-p{n}-b1", doc_id=doc_id, page=n, block_type="text",
                                                text=ocr_text, numbers=[], as_of_date=row["doc_date"],
                                                extraction_model="tesseract-ocr (unverified fallback)",
                                                extraction_confidence=0.35, needs_manual_check=True, image_path=rel))
                    else:
                        failed_pages.append(n)
                        report["errors"].append({"doc_id": doc_id, "page": n, "stage": "ocr",
                                                 "error": "Tesseract returned no text"})
                        all_chunks.append(Chunk(chunk_id=f"{doc_id}-p{n}-b0", doc_id=doc_id, page=n, block_type="text",
                                                text="[OCR returned no text; manual review required]", extraction_model=None,
                                                extraction_confidence=0.0, needs_manual_check=True, image_path=rel, as_of_date=row["doc_date"]))
                    continue
                failed_pages.append(n)
                report["errors"].append({"doc_id": doc_id, "page": n, "stage": "vlm",
                                         "error": str(e)})
                all_chunks.append(Chunk(chunk_id=f"{doc_id}-p{n}-b0", doc_id=doc_id, page=n, block_type="text",
                                        text=f"[VLM extraction failed: {type(e).__name__}]", extraction_model=None,
                                        extraction_confidence=0.0, needs_manual_check=True, image_path=rel, as_of_date=row["doc_date"]))
                continue
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                failed_pages.append(n)
                report["errors"].append({"doc_id": doc_id, "page": n, "stage": "parse", "error": str(e)})
                all_chunks.append(Chunk(chunk_id=f"{doc_id}-p{n}-b0", doc_id=doc_id, page=n, block_type="text",
                                        text=f"[extraction failed: {type(e).__name__}] ", extraction_model=None,
                                        extraction_confidence=0.0, needs_manual_check=True, image_path=rel, as_of_date=row["doc_date"]))
                continue
            successful_pages.add(n)
            for ch, bbox in pairs:
                ch.image_path = rel
                if bbox and ch.block_type in ("chart_caption", "chart_estimate", "table"):
                    crop = out_dir / f"{ch.chunk_id}.png"
                    crop.write_bytes(src.render(dpi, max_side, clip_norm=bbox))     # chart fragment for UI attribution
                    ch.image_path = str(crop.relative_to(data_dir()))
                all_chunks.append(ch)
        # `partial` means at least one page succeeded and at least one failed.
        # If every page failed, use `failed`; do not report error placeholders as parsed content.
        status = "parsed" if not failed_pages else ("partial" if successful_pages else "failed")
        with tx(conn):
            old = [r["chunk_id"] for r in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,))]
            for cid in old:
                conn.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (cid,))
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            for ch in all_chunks:
                _save(conn, ch)
            conn.execute("UPDATE alfa_docs SET file_hash=?, source_files=?, pages=?, parse_status=?, parsed_at=? WHERE doc_id=?",
                         (fhash, json.dumps([f.name for f in files], ensure_ascii=False), len(pages), status, iso(now_utc()), doc_id))
        if status in ("parsed", "partial", "failed"):
            report[status].append(doc_id)
        if report["budget_blocked"]:
            break
    return report


def verify_chunk(conn: sqlite3.Connection, chunk_id: str, text: str | None = None, numbers: list[dict] | None = None) -> bool:
    """Manual verification of key numbers (RSK-08): marks verified_by='manual' and optionally corrects text/numbers."""
    row = conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    if not row:
        return False
    with tx(conn):
        if text is not None:
            conn.execute("UPDATE chunks SET text=? WHERE chunk_id=?", (text, chunk_id))
            index_chunk(conn, chunk_id, text)
        if numbers is not None:
            conn.execute("UPDATE chunks SET numbers_json=? WHERE chunk_id=?", (json.dumps(numbers, ensure_ascii=False), chunk_id))
        conn.execute("UPDATE chunks SET verified_by='manual', needs_manual_check=0 WHERE chunk_id=?", (chunk_id,))
    return True


def verify_key_numbers(conn: sqlite3.Connection, path: str | Path) -> dict:
    """WP-B5 acceptance: check a curated list of key financial numbers against the stored chunks.

    CSV columns: chunk_id,value,tolerance,note. A value is accepted if the chunk carries a number
    within `tolerance` (default exact to 1e-6). Verified chunks get verified_by='manual' and
    needs_manual_check=0. Any mismatch or unknown chunk is reported, so "no discrepancies" is evidence,
    not an assertion. Re-runnable: run `python -m app.cli corpus-verify-key <csv>`.
    """
    path = Path(path)
    checked = matched = 0
    mismatches: list[dict] = []
    missing_chunks: list[str] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    with tx(conn):
        for row in rows:
            cid = (row.get("chunk_id") or "").strip()
            r = conn.execute("SELECT numbers_json FROM chunks WHERE chunk_id=?", (cid,)).fetchone()
            if not r:
                missing_chunks.append(cid)
                continue
            checked += 1
            exp = _num(row.get("value"))
            tol = _num(row.get("tolerance")) or 1e-6
            nums = json.loads(r["numbers_json"] or "[]")
            ok = exp is not None and any(
                abs(n["value"] - exp) <= tol for n in nums if "value" in n
            )
            if ok:
                matched += 1
                conn.execute("UPDATE chunks SET verified_by='manual', needs_manual_check=0 WHERE chunk_id=?", (cid,))
            else:
                mismatches.append({"chunk_id": cid, "expected": exp, "note": row.get("note")})
    return {"file": str(path), "checked": checked, "matched": matched, "mismatches": mismatches,
            "missing_chunks": missing_chunks, "verified": matched == checked and checked == len(rows)}


def corpus_report(conn: sqlite3.Connection) -> dict:
    docs = [dict(r) for r in conn.execute(
        """SELECT a.doc_id, a.title, a.doc_date, a.stale, a.parse_status, a.pages, a.parsed_at,
           (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=a.doc_id) AS chunks,
           (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=a.doc_id AND c.needs_manual_check=1) AS needs_check,
           (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=a.doc_id AND c.verified_by='manual') AS verified
           FROM alfa_docs a ORDER BY a.doc_id""")]
    return {
        "docs": docs,
        # Kept for compatibility: a partial document is not fully parsed.
        "not_parsed": [d["doc_id"] for d in docs if d["parse_status"] != "parsed"],
        "partial": [d["doc_id"] for d in docs if d["parse_status"] == "partial"],
        "failed": [d["doc_id"] for d in docs if d["parse_status"] == "failed"],
        "missing": [d["doc_id"] for d in docs if d["parse_status"] == "missing"],
        "parsed": [d["doc_id"] for d in docs if d["parse_status"] == "parsed"],
    }
