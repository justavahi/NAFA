"""Numeric/date token normalization for scoped evidence checks.

This rejects sign/unit substitutions; it cannot establish subject or causality.
"""
from datetime import datetime
from decimal import Decimal
import re

_DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})\b")
_MONTHS={name:i+1 for i,name in enumerate(("января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"))}
_RU_DATE=re.compile(r"\b(\d{1,2})\s+("+"|".join(_MONTHS)+r")\s+(\d{4})(?:\s*г(?:ода|\.)?)?",re.I)
_ID = re.compile(r"\b(?:FM|ET|DOC|CH|PER|SP|CA|GS|F|G|E|N|M|B|Q)-\d+(?:_[A-Za-z0-9]+)*\b")
_NUMBER = re.compile(
    r"(?<!\w)([+-]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?)"
    r"(?:\s*(п\.?\s*п\.?|б\.?\s*п\.?|%|процент(?:а|ов)?|млрд\s*(?:руб\.?|₽)|млн\s*(?:руб\.?|₽)|тыс\.?\s*(?:руб\.?|₽)|руб\.?|₽|RUB|CNY|USD))?",
    re.IGNORECASE)


def numeric_tokens(text: str) -> set[tuple[str, str, str | None]]:
    tokens = set()
    def date_token(match):
        value = match[0]
        fmt = "%Y-%m-%d" if "-" in value else "%d.%m.%Y"
        try:
            tokens.add(("date", datetime.strptime(value, fmt).date().isoformat(), None))
        except ValueError:
            tokens.add(("invalid_date", value, None))
        return " "
    def russian_date(match):
        try:tokens.add(("date",datetime(int(match[3]),_MONTHS[match[2].lower()],int(match[1])).date().isoformat(),None))
        except ValueError:tokens.add(("invalid_date",match[0],None))
        return " "
    text = _ID.sub(" ", _DATE.sub(date_token, _RU_DATE.sub(russian_date,text)))
    for match in _NUMBER.finditer(text):
        value = str(Decimal(re.sub(r"\s", "", match[1]).replace(",", ".")).normalize())
        unit = re.sub(r"[.\s]", "", (match[2] or "").lower())
        if unit.startswith("процент") or unit == "%": unit = "%"
        elif unit in {"руб", "₽", "rub"}: unit = "RUB"
        elif unit.endswith("руб") or unit.endswith("₽"): unit = unit.replace("руб","RUB").replace("₽","RUB")
        tokens.add(("number", value, unit or None))
    return tokens


def unsupported_numeric_tokens(text: str, evidence: str) -> set:
    allowed = numeric_tokens(evidence)
    return {token for token in numeric_tokens(text) if token not in allowed and not (
        token[0] == "number" and token[2] is None
        and any(other[0] == "number" and other[1] == token[1] for other in allowed))}
