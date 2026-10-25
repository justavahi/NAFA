"""Bank of Russia connectors: SRC-02 (XML_daily, M-03..M-05) and SRC-16 (key rate HTML table, M-01)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from lxml import etree

from ..http import FetchError
from ..models import MacroPoint
from ..util import now_utc
from .base import Connector, FetchOutcome, ItemError


def _num(s: str) -> float:
    return float(s.strip().replace("\xa0", "").replace(" ", "").replace(",", "."))


def parse_xml_daily(content: bytes, series_map: dict[str, str], source_id: str = "SRC-02") -> tuple[list[MacroPoint], int, list[ItemError]]:
    """ValCurs/Valute: windows-1251 is declared in the XML prolog, lxml decodes from bytes. Decimal comma."""
    try:
        root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise FetchError("parse_error", str(e)) from e
    if root.tag != "ValCurs":
        raise FetchError("parse_error", f"unexpected root <{root.tag}>")
    try:
        d = datetime.strptime(root.get("Date", ""), "%d.%m.%Y").date()
    except ValueError as e:
        raise FetchError("parse_error", "missing/invalid ValCurs@Date") from e
    points, errors, total = [], [], 0
    for i, v in enumerate(root.findall("Valute")):
        code = (v.findtext("CharCode") or "").strip()
        if code not in series_map:
            continue
        total += 1
        try:
            nominal = _num(v.findtext("Nominal") or "1")
            vunit = v.findtext("VunitRate")
            value = _num(vunit) if vunit else _num(v.findtext("Value")) / nominal
            points.append(MacroPoint(series_id=series_map[code], date=d, value=value, unit="RUB", source_id=source_id))
        except (ValueError, TypeError, ZeroDivisionError) as e:
            errors.append(ItemError(i, "bad_value", f"{code}: {e}"))
    return points, total, errors


class CbrDailyConnector(Connector):
    def fetch(self, now: datetime | None = None, *, day: date | None = None, **kwargs) -> FetchOutcome:
        params = {"date_req": day.strftime("%d/%m/%Y")} if day else None
        res = self.http.get(self.cfg["url"], params=params, allowed_domains=["cbr.ru"])
        points, total, errors = parse_xml_daily(res.content, self.cfg["series"], self.source_id)
        if total == 0:
            raise FetchError("parse_error", "no requested currencies in XML")
        return FetchOutcome(points, total, errors, res.status, res.attempts, res.robots)


def parse_key_rate_html(content: bytes | str, source_id: str = "SRC-16") -> tuple[list[MacroPoint], int, list[ItemError]]:
    """Table 'date - rate'. We look for any table whose rows are (dd.mm.yyyy, number) so layout tweaks do not break us."""
    soup = BeautifulSoup(content, "lxml")
    points, errors, total = [], [], 0
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) >= 2 and _looks_like_date(cells[0]):
                rows.append(cells)
        if not rows:
            continue
        for i, cells in enumerate(rows):
            total += 1
            try:
                d = datetime.strptime(cells[0], "%d.%m.%Y").date()
                points.append(MacroPoint(series_id="M-01", date=d, value=_num(cells[1].replace("%", "")),
                                         unit="% p.a.", source_id=source_id))
            except (ValueError, TypeError) as e:
                errors.append(ItemError(i, "bad_row", str(e)))
        break
    if total == 0:
        raise FetchError("parse_error", "key-rate table not found (layout changed?)")
    return points, total, errors


def _looks_like_date(s: str) -> bool:
    try:
        datetime.strptime(s.strip(), "%d.%m.%Y")
        return True
    except ValueError:
        return False


class CbrKeyRateConnector(Connector):
    def fetch(self, now: datetime | None = None, *, start: date | None = None, end: date | None = None, **kwargs) -> FetchOutcome:
        end = end or (now or now_utc()).date()
        start = start or end - timedelta(days=30)
        # UniDbQuery.* parameters are what the site's own form submits; they are UNVERIFIED in the case docs -> check-sources tests them.
        params = {"UniDbQuery.Posted": "True", "UniDbQuery.From": start.strftime("%d.%m.%Y"), "UniDbQuery.To": end.strftime("%d.%m.%Y")}
        res = self.http.get(self.cfg["url"], params=params, allowed_domains=["cbr.ru"])
        points, total, errors = parse_key_rate_html(res.content, self.source_id)
        return FetchOutcome(points, total, errors, res.status, res.attempts, res.robots)
