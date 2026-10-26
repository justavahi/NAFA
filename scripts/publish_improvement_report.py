"""Export synthetic pilot evidence and a genuinely blank blind human-review pack."""
import argparse
import csv
import json
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pe.improvement import load,report,save


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--name',default='improvement_v1')
    args=parser.parse_args();run=args.run
    report(run);protocol,records=load(run)
    if Path(args.name).name!=args.name:parser.error('report name must be a single directory name')
    dest=ROOT/'docs/pe/evaluation'/args.name;dest.mkdir(parents=True,exist_ok=True)
    for name in ('summary.json','summary.md','protocol.json'):(dest/name).write_bytes((run/name).read_bytes())
    results=[]
    for phase in ('cards','scores','pairs'):
        for path in sorted((run/phase).glob('*.json')):
            results.append(dict(phase=phase,file=path.name,result=json.loads(path.read_text())))
    (dest/'case_results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results))
    pack=[];keys={};review=[]
    for cid in protocol['pre_registered_cases']['dev']:
        paths={v:run/'cards'/f'{cid}_{v}.json' for v in ('baseline','candidate')}
        if not all(p.exists() for p in paths.values()):continue
        rows={v:json.loads(p.read_text()) for v,p in paths.items()}
        if not all(r.get('card') and r['status']=='evaluated' for r in rows.values()):continue
        variants=['baseline','candidate'];random.Random('human-v1-'+cid).shuffle(variants)
        key=dict(zip(('A','B'),variants));keys[cid]=key
        pack.append(dict(case_id=cid,ledger=records[cid]['case']['ledger'],A=rows[key['A']]['card'],B=rows[key['B']]['card']))
        review.append(dict(case_id=cid,winner='',critical_error_a='',critical_error_b='',notes=''))
    (dest/'human_review.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in pack))
    save(run/'human_blinding_key.json',keys)
    with (dest/'human_review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['case_id','winner','critical_error_a','critical_error_b','notes']);writer.writeheader();writer.writerows(review)
    print(dest)


if __name__=='__main__':main()
