import json
import feedparser
import pandas as pd
from bs4 import BeautifulSoup
from lxml import etree
from .normalize import document_record, clean_text

def parse_rss(payload, source):
    """Parse tolerant RSS/Atom. Example: records=parse_rss(xml_bytes, source_config)."""
    feed=feedparser.parse(payload)
    out=[]
    for e in feed.entries:
        link=e.get("link") or e.get("id")
        if not link: continue
        tags=", ".join(t.get("term","") for t in e.get("tags",[]) if t.get("term"))
        rec=document_record(source,e.get("title",""),link,
            e.get("published") or e.get("updated"),e.get("summary",""),None,tags)
        if rec: out.append(rec)

    error = getattr(feed, "bozo_exception", None)
    return out, { "bozo": bool(getattr(feed, "bozo", False)), "bozo_exception": type(error).__name__ if error else None, }

def parse_html(payload, source, final_url):
    """Extract configured CSS selectors; no site-specific selectors in code."""
    soup=BeautifulSoup(payload, "html.parser")
    selectors=source.get("selectors", {})
    title_node=soup.select_one(selectors.get("title","title"))
    date_selector=selectors.get("published")
    date_node=soup.select_one(date_selector) if date_selector else None
    snippet_selector=selectors.get("snippet", "meta[name=description]")
    snippet_node=soup.select_one(snippet_selector) if snippet_selector else None
    title=title_node.get("content") if title_node and title_node.name=="meta" else (title_node.get_text(" ",strip=True) if title_node else "")
    date=(date_node.get("datetime") or date_node.get_text(" ",strip=True)) if date_node else None
    snippet=(snippet_node.get("content") or "") if snippet_node and snippet_node.name=="meta" else (snippet_node.get_text(" ",strip=True) if snippet_node else "")
    rec=document_record(source,title,final_url,date,snippet)
    return [rec] if rec else []

def parse_xml_series(payload, source):
    """Generic XML series parser driven by XPath field mapping in YAML."""
    root=etree.fromstring(payload)
    cfg=source["xml"]
    rows=[]
    for node in root.xpath(cfg["row_xpath"]):
        def get(key):
            found=node.xpath(cfg["fields"][key])
            if not found: return None
            v=found[0]
            return str(v.text if hasattr(v,"text") and v.text is not None else v).strip()

        date, series, value = get("date"), get("series_id"), get("value")

        series = cfg.get("series_map", {}).get(series, series)

        if not date:
            date = root.get("Date")

        if not date or not series or not value:
            continue

        if not date or not series or not value: continue
        decimal=cfg.get("decimal_separator",",")
        value=float(value.replace(decimal,"."))
        nominal=float(get("nominal") or 1)
        rows.append({"series_id":series,"date":date,"value":value/nominal,
                     "unit":cfg.get("unit",""),"source_id":source["id"]})
    return rows

def parse_csv_file(path, source):
    """Read CSV without replacing empty values by zero; returns original columns."""
    df=pd.read_csv(path, dtype="string", keep_default_na=True)
    return df, {"path":str(path), "rows":int(len(df)), "columns":list(df.columns),
                "nulls":{str(k):int(v) for k,v in df.isna().sum().items()}}

def parse_pdf(path):
    """Text extraction only; visual charts need the configured VLM adapter."""
    import fitz
    result=[]
    with fitz.open(path) as pdf:
        for page_no, page in enumerate(pdf, start=1):
            result.append({"page":page_no,"text":page.get_text("text").strip()})
    return result
