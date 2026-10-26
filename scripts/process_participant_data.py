"""Run pinned DA without demo quality; audit duplicate IDs and adapt client types."""
import argparse
import ast
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pe.participant import client_type


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,default=ROOT/'.local/participant_data')
    p.add_argument('--out',type=Path,default=ROOT/'.local/participant_da_safe')
    a=p.parse_args();source=ROOT/'.local/da_snapshot/da_scripts'
    sys.path.insert(0,str(source));import pandas as pd
    from analytics.run_all import run_pipeline
    base=ROOT/'.local/participant_da'
    base.mkdir(parents=True,exist_ok=True)
    source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(a.data_dir.glob('*.csv'))}
    ready=all((base/'features'/f'{name}.csv').exists() for name in ('client_features','payment_features','exposures'))
    if ready and (not (base/'input_manifest.json').exists() or json.loads((base/'input_manifest.json').read_text())!=source_hashes):
        raise ValueError('existing DA output has missing/different input provenance; use a fresh DA snapshot/output')
    if not ready:
        with (base/'run.log').open('w') as stream,contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
            run_pipeline(a.data_dir,base,ref_dir=source/'ref_data',do_charts=False,do_quality=False)
        (base/'input_manifest.json').write_text(json.dumps(source_hashes,indent=2)+'\n')
    if a.out.exists():raise ValueError('new corrected output directory required')
    a.out.mkdir(parents=True)
    inventory=json.loads((a.data_dir/'validation.json').read_text());sep=ast.literal_eval(inventory['df_Uid_data_hack.csv']['separator'])
    raw=pd.read_csv(a.data_dir/'df_Uid_data_hack.csv',sep=sep,dtype='string',keep_default_na=False)
    duplicated=raw[raw.SYNTHETIC_U.duplicated(keep=False)]
    conflicts=[str(cid) for cid,frame in duplicated.groupby('SYNTHETIC_U') if len(frame.drop_duplicates())>1]
    (a.out/'ambiguous_ids.json').write_text(json.dumps(conflicts))
    features=pd.read_csv(base/'features/client_features.csv',dtype={'SYNTHETIC_U':'string'})
    before=int(features.F10_cl_type.isna().sum())
    raw_types=features.F10_cl_type_raw
    mapped=raw_types.map(client_type)
    corrected=mapped.notna();features.loc[corrected,'F10_cl_type']=mapped[corrected]
    features.to_csv(a.out/'client_features.csv',index=False,encoding='utf-8-sig')
    report=dict(da_revision='a623ed3e3c73315d2d94d70f11fd37324452dd95',raw_clients=len(raw),
        unique_clients=int(raw.SYNTHETIC_U.nunique()),duplicate_extra_rows=int(raw.SYNTHETIC_U.duplicated().sum()),
        conflicting_duplicate_groups=len(conflicts),ambiguous_clients_excluded_from_llm_sampling=True,
        client_type_missing_before=before,client_type_missing_after=int(features.F10_cl_type.isna().sum()),
        dictionary_adapter={'CLIENT':'existing','PROSPECT':'potential','CLOSE_CLIENT':'unknown_pending_business_confirmation'},
        units_revenue_confirmed=False,raw_identifiers_exported_to_llm=False,demo_quality_disabled=True,artifacts={})
    keys=set(features.SYNTHETIC_U)
    if keys!=set(raw.SYNTHETIC_U):raise ValueError('raw/DA client key mismatch')
    for name in ('client_features','payment_features','exposures'):
        if name!='client_features':shutil.copy2(base/'features'/f'{name}.csv',a.out/f'{name}.csv')
        path=a.out/f'{name}.csv';df=pd.read_csv(path,dtype={'SYNTHETIC_U':'string'})
        if df.SYNTHETIC_U.duplicated().any() or set(df.SYNTHETIC_U)!=keys:raise ValueError('DA aggregate key alignment failed')
        report['artifacts'][name]=dict(rows=len(df),columns=len(df.columns),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    exp=pd.read_csv(a.out/'exposures.csv');cols=[c for c in exp if c.startswith('eps_')]
    report['invalid_exposure_cells']=int(((exp[cols]<0)|(exp[cols]>1)).sum().sum())
    report['missing_exposure_cells']=int(exp[cols].isna().sum().sum())
    report['input_hashes']=source_hashes
    (a.out/'validation_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
