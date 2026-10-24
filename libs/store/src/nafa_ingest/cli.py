import argparse, json
from .pipeline import ingest_all

def main():
    p=argparse.ArgumentParser(description="Collect configured NAFA sources into SQLite")
    p.add_argument("--config",default="config/sources.yaml")
    p.add_argument("--source")
    args=p.parse_args()
    print(json.dumps(ingest_all(args.config,args.source),ensure_ascii=False,indent=2))
if __name__=="__main__": main()
