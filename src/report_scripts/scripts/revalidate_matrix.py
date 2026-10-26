"""Recheck successful stored model answers after changes to deterministic gates.

Preserves original API telemetry and signature; never promotes failed cases or
changes the prompt version. It does not claim a fresh model call or human review.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pe.acceptance import signature
from pe.contracts import INPUT_SCHEMAS,OUTPUT_SCHEMAS
from pe.prompts import PROMPT_DIR,prepare_request
from pe.seed_cases import golden_cases,task_payloads
from pe.tasks import validate_task_output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();cases=golden_cases()
    manifest=json.loads((PROMPT_DIR/'manifest.json').read_text());sig=signature();copied=0
    for path in sorted((args.source/'matrix').glob('*.json')):
        row=json.loads(path.read_text());target=args.out/'matrix'/path.name
        if target.exists() or row.get('status')!='passed':continue
        pid,gs=row['task'],row['case_id'];payload=task_payloads(cases[gs])[pid]
        if pid=='PRM-03':
            did={'GS-01':'DOC-07','GS-04':'DOC-06','GS-06':'DOC-04'}[gs]
            payload=dict(doc_id=did,page=1,image_description='Страница корпуса кейса')
        spec=manifest['prompts'][pid];request=prepare_request(pid,payload,validate_input=True)
        calls=[c for c in row['calls'] if c['prompt_id']==pid]
        if not calls or any(c.get('version')!=spec['version'] or c.get('prompt_hash')!=request['prompt_hash'] for c in calls):continue
        output=OUTPUT_SCHEMAS[spec['schema']].model_validate(row['output'])
        if pid=='PRM-03':output.needs_manual_check=True
        validate_task_output(pid,output,payload,images=[{'stored_image_call':True}] if pid=='PRM-03' else None)
        row.update(original_signature=row['signature'],signature=sig,
                   output=output.model_dump(mode='json'),reused_from=str(path),
                   revalidation='Current input/output contracts, source, numeric and task gates replayed; original API response and critic result retained. No new API call or human review.')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');copied+=1
    print('Stored successful answers revalidated:',copied)


if __name__=='__main__':main()
