"""Copy snippets from here into your own modules as needed."""

from nafa_ingest.config import load_config
from nafa_ingest.fetch import fetch_url
from nafa_ingest.parsers import parse_rss, parse_html, parse_json, parse_xml, parse_csv, parse_pdf
from nafa_ingest.pipeline import ingest_source
from nafa_ingest.storage import search_documents


# Example: ingest one configured source through the whole pipeline.
def collect_one(source_id: str = "SRC-01"):
    settings, sources, config_dir = load_config("config/sources.yaml")
    source = next(source for source in sources if source["id"] == source_id)
    return ingest_source(
        source,
        settings,
        database_path=settings["database_path"],
        config_dir=config_dir,
    )


# Example: call lower-level functions directly when another module needs them.
def get_rss_documents(source_id: str = "SRC-01"):
    settings, sources, _ = load_config("config/sources.yaml")
    source = next(source for source in sources if source["id"] == source_id)
    body, final_url, _content_type = fetch_url(source["url"], settings)
    return parse_rss(body, source, settings, base_url=final_url)


# Example: query documents from another module.
def find_news(query: str):
    settings, _, _ = load_config("config/sources.yaml")
    return search_documents(settings["database_path"], query, limit=20)
