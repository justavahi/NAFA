"""Export pinned DA sources locally without merging or touching another checkout."""
from pathlib import Path
import json
import subprocess

ROOT=Path(__file__).resolve().parent.parent
REV='a623ed3e3c73315d2d94d70f11fd37324452dd95'


def main():
    target=ROOT/'.local/da_snapshot'
    if target.exists():raise SystemExit('DA snapshot already exists; existing files preserved')
    names=subprocess.check_output(['git','ls-tree','-r','--name-only','-z',REV,'da_scripts/'],cwd=ROOT).decode().split('\0')
    for name in filter(None,names):
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.check_output(['git','show',REV+':'+name],cwd=ROOT))
    (target/'snapshot.json').write_text(json.dumps(dict(revision=REV,source='origin/data-analysis',files=len(list(filter(None,names)))),indent=2)+'\n')
    print(target/'da_scripts')


if __name__=='__main__':main()
