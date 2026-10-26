"""Build a reviewable local source snapshot, excluding runtime/private data."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parent.parent
ROOT_FILES=['README.md','LICENSE','pyproject.toml','uv.lock','.gitignore','.env.example']
DIRECTORIES=['pe','prompts','schemas','scripts','tests','config','reference','patches','docs/pe','examples/demo']
SUFFIXES={'.py','.md','.txt','.json','.jsonl','.csv','.toml','.yaml','.yml','.patch'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'out/NAFA_review_2026-10-26.zip');args=parser.parse_args()
    files={ROOT/name for name in ROOT_FILES if (ROOT/name).exists()}
    for directory in DIRECTORIES:
        files.update(p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in SUFFIXES and '__pycache__' not in p.parts)
    secret=None
    if (ROOT/'.env').exists():
        for line in (ROOT/'.env').read_text().splitlines():
            if line.startswith('LLM_API_KEY='):secret=line.split('=',1)[1].strip().strip('\"\'')
    payload={}
    for path in sorted(files):
        if not path.resolve().is_relative_to(ROOT):raise ValueError('external source symlink refused')
        name=path.relative_to(ROOT).as_posix()
        if any(part in {'.local','out','.git','.venv'} for part in path.relative_to(ROOT).parts):raise ValueError('private/runtime file selected')
        content=path.read_bytes()
        if secret and secret.encode() in content:raise ValueError('API credential found in '+name)
        text=content.decode('utf-8')
        text=text.replace(str(ROOT),'<repo>').replace(str(Path.home()),'<user-home>')
        payload[name]=text.encode('utf-8')
    payload['FOR_REVIEW.md']=payload['docs/pe/09_architecture_review.md']
    payload['MESSAGE.txt']=payload['docs/pe/10_review_message.txt']
    manifest=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
        source_git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        includes_uncommitted_work=True,not_a_published_release=True,machine_paths_redacted=True,
        excluded=['.env','.local','out','raw participant CSVs','private_id_mapping','SQLite databases','.git','.venv'],
        files={name:hashlib.sha256(data).hexdigest() for name,data in payload.items()})
    payload['REVIEW_MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,content in payload.items():archive.writestr(name,content)
    with zipfile.ZipFile(args.out) as archive:
        if archive.testzip() is not None:raise ValueError('archive integrity check failed')
        for name in archive.namelist():
            if name=='.env' or name.endswith('.db') or '/.local/' in name or name.startswith('out/'):raise ValueError('private file in archive')
    print(args.out)
    print(len(payload),'files;',args.out.stat().st_size,'bytes; archive and credential checks passed')


if __name__=='__main__':main()
