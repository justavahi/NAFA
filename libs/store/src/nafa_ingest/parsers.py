from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import feedparser
import pandas as pd
import pymupdf
from bs4 import BeautifulSoup
from lxml import etree

from .normalize import clean_text, json_safe, normalize_document, parse_datetime, utc_now


def _get_path(value: Any, path: str | None, default: Any = None) -> Any:
    """Read a dot path from a dict/list, e.g. ``data.items.0.name``."""
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
        if current is None:
            return default
    return current


def _mapped_fields(item: Any, fields: dict[str, str], defaults: dict[str, str] | None = None) -> dict[str, Any]:
    mapping = {**(defaults or {}), **(fields or {})}
    return {target: _get_path(item, path) for target, path in mapping.items()}


def _convert_document(raw: dict[str, Any], source: dict[str, Any], fetched_at: str, base_url: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    return normalize_document(
        raw,
        source,
        fetched_at=fetched_at,
        base_url=base_url,
        default_timezone=settings.get("default_timezone", "UTC"),
        tracking_parameters=settings.get("tracking_parameters", []),
        tracking_prefixes=settings.get("tracking_prefixes", ["utm_"]),
    )


def parse_rss(payload: bytes, source: dict[str, Any], settings: dict[str, Any], *, fetched_at: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    """Parse RSS/Atom leniently into normalized Document dictionaries.

    Example:
        body, final_url, _ = fetch_url(source["url"], settings)
        documents = parse_rss(body, source, settings, base_url=final_url)
    """
    fetched_at = fetched_at or utc_now()
    base_url = base_url or source.get("url", "")
    parsed = feedparser.parse(payload)
    mapping = source.get("fields", {})
    documents = []
    for entry in parsed.entries:
        raw_entry = dict(entry)
        raw = _mapped_fields(raw_entry, mapping, {
            "title": "title", "url": "link", "published_at": "published", "snippet": "summary",
        })
        raw["source_item_id"] = entry.get("id") or entry.get("guid") or raw.get("url")
        tags = entry.get("tags", [])
        raw["category"] = raw.get("category") or ", ".join(
            clean_text(tag.get("term")) for tag in tags if isinstance(tag, dict) and clean_text(tag.get("term"))
        ) or None
        if source.get("extract_text", False):
            content = entry.get("content") or []
            raw["text"] = "\n".join(clean_text(part.get("value")) or "" for part in content if isinstance(part, dict)) or None
        raw["payload"] = json_safe(raw_entry)
        document = _convert_document(raw, source, fetched_at, base_url, settings)
        if document:
            documents.append(document)
    return documents


def _html_value(scope: Any, selector_spec: str | None) -> Any:
    if not selector_spec:
        return None
    # A selector ending in @attr extracts an attribute: "a.article@href".
    selector, sep, attribute = selector_spec.rpartition("@")
    if not sep:
        selector, attribute = selector_spec, ""
    node = scope.select_one(selector)
    if node is None:
        return None
    if attribute:
        return node.get(attribute)
    if node.name == "time" and node.get("datetime"):
        return node.get("datetime")
    return node.get_text(" ", strip=True)


def parse_html(payload: bytes, source: dict[str, Any], settings: dict[str, Any], *, fetched_at: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    """Parse HTML using CSS selectors supplied by config; no site selectors in code."""
    fetched_at = fetched_at or utc_now()
    base_url = base_url or source.get("url", "")
    soup = BeautifulSoup(payload, source.get("html_parser", "html.parser"))
    item_selector = source.get("item_selector")
    items: Iterable[Any] = soup.select(item_selector) if item_selector else [soup]
    if source.get("record_type") == "macro_series":
        macro = source.get("macro", {})
        records = []
        for item in items:
            date_value = _html_value(item, macro.get("date_selector"))
            value_text = _html_value(item, macro.get("value_selector"))
            if not date_value or not value_text:
                continue
            value_text = clean_text(value_text).replace(" ", "")
            separator = macro.get("decimal_separator", ",")
            if separator != ".":
                value_text = value_text.replace(separator, ".")
            try:
                value = float(Decimal(value_text))
            except (InvalidOperation, ValueError):
                continue
            date = _parse_series_date(date_value, macro, settings)
            if not date:
                continue
            records.append({
                "series_id": macro["series_id"], "date": date, "value": value,
                "unit": macro.get("unit", ""), "source_id": source["id"],
                "fetched_at": fetched_at, "revision": None,
            })
        return records

    selector_map = source.get("selectors", {})
    documents = []
    for index, item in enumerate(items):
        raw = {field: _html_value(item, selector) for field, selector in selector_map.items()}
        raw["source_item_id"] = raw.get("url") or f"{source['id']}:{index}:{clean_text(item.get_text(' ', strip=True))}"
        raw["payload"] = {"text": clean_text(item.get_text(" ", strip=True))}
        document = _convert_document(raw, source, fetched_at, base_url, settings)
        if document:
            documents.append(document)
    return documents


def parse_json(payload: bytes, source: dict[str, Any], settings: dict[str, Any], *, fetched_at: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    """Parse a JSON API response using configurable item and field paths."""
    fetched_at = fetched_at or utc_now()
    base_url = base_url or source.get("url", "")
    root = json.loads(payload.decode(source.get("encoding", "utf-8")))
    items = _get_path(root, source.get("items_path"), root)
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        items = [items]
    documents = []
    for item in items:
        raw = _mapped_fields(item, source.get("fields", {}), {
            "title": "title", "url": "url", "published_at": "published_at", "snippet": "description",
        })
        raw["source_item_id"] = _get_path(item, source.get("id_field", "id")) or raw.get("url")
        raw["payload"] = json_safe(item)
        document = _convert_document(raw, source, fetched_at, base_url, settings)
        if document:
            documents.append(document)
    return documents


def _xpath_text(node: Any, xpath: str | None) -> Any:
    if not xpath:
        return None
    values = node.xpath(xpath)
    if not values:
        return None
    if len(values) == 1:
        value = values[0]
        return value.text if hasattr(value, "text") and not isinstance(value, str) else value
    return [value.text if hasattr(value, "text") and not isinstance(value, str) else value for value in values]


def _parse_series_date(value: Any, config: dict[str, Any], settings: dict[str, Any]) -> str | None:
    parsed = parse_datetime(value, default_timezone=settings.get("default_timezone", "UTC"), date_format=config.get("date_format"))
    if not parsed:
        return None
    # MacroSeries uses a calendar date, not a timestamp.
    return parsed[:10]


def parse_xml(payload: bytes, source: dict[str, Any], settings: dict[str, Any], *, fetched_at: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    """Parse XML using configurable XPath expressions; supports macro-series mapping."""
    fetched_at = fetched_at or utc_now()
    base_url = base_url or source.get("url", "")
    parser = etree.XMLParser(recover=bool(source.get("recover_malformed_xml", True)), encoding=source.get("encoding"))
    root = etree.fromstring(payload, parser=parser)

    if source.get("record_type") == "macro_series":
        macro = source.get("macro", {})
        items = root.xpath(macro.get("items_xpath", ".//*"))
        date_value = _xpath_text(root, macro.get("date_xpath"))
        date = _parse_series_date(date_value, macro, settings)
        records = []
        series_map = macro.get("series_map", {})
        for item in items:
            code = clean_text(_xpath_text(item, macro.get("series_field_xpath")))
            series_id = series_map.get(code) if series_map else macro.get("series_id")
            value_text = clean_text(_xpath_text(item, macro.get("value_xpath")))
            if not series_id or not value_text or not date:
                continue
            separator = macro.get("decimal_separator", ",")
            value_text = value_text.replace(" ", "")
            if separator != ".":
                value_text = value_text.replace(separator, ".")
            try:
                value = Decimal(value_text)
                nominal_text = clean_text(_xpath_text(item, macro.get("nominal_xpath")))
                if nominal_text:
                    value /= Decimal(nominal_text.replace(" ", ""))
            except (InvalidOperation, ValueError):
                continue
            records.append({
                "series_id": series_id, "date": date, "value": float(value),
                "unit": macro.get("unit", ""), "source_id": source["id"],
                "fetched_at": fetched_at, "revision": None,
            })
        return records

    items = root.xpath(source.get("item_xpath", ".//*"))
    mapping = source.get("fields", {})
    documents = []
    for index, item in enumerate(items):
        raw = {field: _xpath_text(item, xpath) for field, xpath in mapping.items()}
        raw["source_item_id"] = raw.get("url") or f"{source['id']}:{index}"
        raw["payload"] = {"xml": etree.tostring(item, encoding="unicode")}
        document = _convert_document(raw, source, fetched_at, base_url, settings)
        if document:
            documents.append(document)
    return documents


def parse_csv(path: str | Path, source: dict[str, Any], *, fetched_at: str | None = None) -> list[dict[str, Any]]:
    """Read a CSV into normalized JSON row records with pandas.

    Every input column is preserved; pandas NA/NaN values become JSON nulls.
    """
    fetched_at = fetched_at or utc_now()
    frame = pd.read_csv(
        path,
        sep=source.get("delimiter", ","),
        encoding=source.get("encoding", "utf-8-sig"),
        dtype=source.get("dtype"),
        keep_default_na=True,
    )
    frame = frame.astype(object).where(pd.notna(frame), None)
    rows = []
    for index, row in enumerate(frame.to_dict(orient="records"), start=1):
        rows.append({
            "source_id": source["id"],
            "dataset_name": source.get("dataset_name", source["id"]),
            "source_file": Path(path).name,
            "row_number": index,
            "row_key": clean_text(row.get(source.get("id_field", ""))) if source.get("id_field") else None,
            "payload": json_safe(row),
            "fetched_at": fetched_at,
        })
    return rows


def parse_pdf(payload: bytes, source: dict[str, Any], settings: dict[str, Any], *, fetched_at: str | None = None, document_url: str | None = None) -> list[dict[str, Any]]:
    """Extract text per PDF page; keep page numbers in payload and the URL."""
    fetched_at = fetched_at or utc_now()
    documents = []
    with fitz.open(stream=payload, filetype="pdf") as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            page_url = f"{document_url or source.get('url') or source.get('path') or source['id']}?page={page_number}"
            raw = {
                "title": f"{Path(source.get('path') or document_url or source['id']).name} — page {page_number}",
                "url": page_url,
                "published_at": source.get("published_at"),
                "snippet": text[:1000] if text else None,
                "text": text or None,
                "kind": source.get("kind", "document"),
                "source_item_id": f"{source['id']}:{document_url or source.get('path')}:{page_number}",
                "payload": {"page": page_number, "pdf_metadata": pdf.metadata},
            }
            document = _convert_document(raw, source, fetched_at, document_url or "", settings)
            if document:
                documents.append(document)
    return documents
