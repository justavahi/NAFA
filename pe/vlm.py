"""Conservative page extraction normalization; every omission requires review."""
from datetime import date
import json
import re


def normalize_page(text: str) -> tuple[dict, list[str]]:
    data=json.loads(text)
    changes=[]
    if not isinstance(data,dict):return data,changes

    def exact_date(value):
        if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value):return False
        try:date.fromisoformat(value);return True
        except ValueError:return False

    if data.get("as_of_date") is not None and not exact_date(data["as_of_date"]):
        data["as_of_date"]=None;changes.append("as_of_date: imprecise date omitted")
    blocks=data.get("blocks")
    if not isinstance(blocks,list):return data,changes
    if len(blocks)>6:
        data["blocks"]=blocks=blocks[:6];changes.append("blocks: extra blocks omitted")
    remaining=12
    for i,block in enumerate(blocks):
        if not isinstance(block,dict):continue
        before=len(changes)
        value=block.get("text")
        if isinstance(value,str) and len(value)>300:
            block["text"]=value[:297].rsplit(" ",1)[0]+"…"
            changes.append(f"blocks.{i}.text: truncated; source review required")
        numbers=block.get("numbers")
        if isinstance(numbers,list):
            if block.get("block_type")=="chart_estimate" and numbers:
                block["numbers"]=numbers=[];changes.append(f"blocks.{i}.numbers: chart estimates omitted")
            if len(numbers)>remaining:
                block["numbers"]=numbers=numbers[:remaining];changes.append(f"blocks.{i}.numbers: extra numbers omitted")
            remaining-=len(numbers)
            for j,number in enumerate(numbers):
                if not isinstance(number,dict):continue
                period=number.get("date")
                if period is not None and not exact_date(period):
                    number["date"]=None
                    if isinstance(number.get("subject"),str):number["subject"]+=f" (период в исходном ответе: {period})"
                    changes.append(f"blocks.{i}.numbers.{j}.date: period retained in subject, exact date unknown")
        if len(changes)>before:block["needs_manual_check"]=True
    # A valid JSON shape cannot verify the number's subject or attribution.
    # Raw VLM extraction must be reviewed before entering trusted BankInput.
    data["needs_manual_check"]=True
    changes.append("page: unverified VLM extraction requires source review")
    return data,changes
