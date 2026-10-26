"""Fetch the three public case CSVs; pin size/hash and validate without printing IDs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parent.parent
SOURCES=[
 ('df_Uid_data_hack.csv','12H7sbLwBcpur0fKaGHPqwPwfXfgQq10b',31414716,'cb48983806ce7ca0ae045a40c9627ccbe47cc72bfb54058db04df2386da4a983'),
 ('df_Uid_payment_hack.csv','1zkvWkAS0kZqijD-og_0Cv3o01SoUMY_Z',38752490,'30095f75bead1a1dac0d7232b0da09a326b7ef643ee3b77b36ad3b4458a1cab3'),
 ('df_Fid_Uid_link_hack.csv','1ZJCiqgY1ma4iQ1_ltabtUuaFIBkrqnOe',697194,'3d9c718e2a87304f48bbc7447607116bb8e4bd61dded70b0a1d0558f086a1b58'),
]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'.local/participant_data');a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    def fetch(item):
        name,file_id,size,expected=item;target=a.out/name
        if not target.exists():
            temporary=target.with_suffix('.part')
            with httpx.stream('GET','https://drive.usercontent.google.com/download',params={'id':file_id,'export':'download','confirm':'t'},follow_redirects=True,timeout=60) as response:
                response.raise_for_status()
                with temporary.open('wb') as stream:
                    for chunk in response.iter_bytes():stream.write(chunk)
            if temporary.stat().st_size!=size or hashlib.sha256(temporary.read_bytes()).hexdigest()!=expected:
                temporary.unlink();raise ValueError('source changed: '+name)
            temporary.replace(target)
        if target.stat().st_size!=size or hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise ValueError('local input changed: '+name)
        return dict(name=name,file_id=file_id,size=size,sha256=expected)
    with ThreadPoolExecutor(max_workers=3) as pool:files=list(pool.map(fetch,SOURCES))
    report={}
    for item in files:
        path=a.out/item['name']
        with path.open(encoding='utf-8-sig',newline='') as stream:
            sample=stream.read(65536);stream.seek(0);sep=csv.Sniffer().sniff(sample,delimiters=',;\t|').delimiter
            reader=csv.DictReader(stream,delimiter=sep);columns=reader.fieldnames;count=0;missing={k:0 for k in columns};ids=set();duplicate=0
            key='SYNTHETIC_U' if item['name']=='df_Uid_data_hack.csv' else None
            if key and key not in columns:raise ValueError('client key missing')
            for row in reader:
                count+=1
                for field in columns:
                    if row.get(field) is None or row[field].strip().lower() in ('','nan','none','null'):missing[field]+=1
                if key:
                    value=row[key];duplicate+=value in ids;ids.add(value)
        report[item['name']]=dict(rows=count,columns=columns,separator=repr(sep),missing=missing,
            duplicate_client_ids=duplicate if key else None,unique_client_ids=len(ids) if key else None,sha256=item['sha256'])
        print(item['name'],count,'rows; no values printed')
    (a.out/'source_manifest.json').write_text(json.dumps(dict(folder='https://drive.google.com/drive/folders/1Ep8wWoEHTg5Q8kwrLx2j_xFyxGUEVxFq',files=files),indent=2)+'\n')
    (a.out/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
