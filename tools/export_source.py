#!/usr/bin/env python3
"""Audit/export an explicit public allowlist; no GitHub or Git mutations."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES={'README.md','LICENSE','THIRD_PARTY.md','pyproject.toml','.gitignore','.gitattributes'}
FOLDERS={'src','tools','tests','profiles','docs','LICENSES','.github'}
SUFFIXES={'.py','.c','.md','.toml','.json','.yml','.txt','.png','.svg'}
SENSITIVE=[re.compile(pattern,re.I) for pattern in (
    r'gh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{30,}',
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',r'C:[\\/]+Users[\\/]+[^\s"<>]+',
    r'(?<![0-9a-f])[0-9]{20,}(?![0-9a-f])',
    r'"serial"\s*:\s*"(?!GBOS_PUBLIC_TEST|FILE TEST)[^"\n]+',
)]


def audit():
    selected=[]
    for item in ROOT.iterdir():
        if item.name in FILES:selected.append(item)
        elif item.name in FOLDERS:
            selected.extend(p for p in item.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for path in selected:
        if path.is_symlink() or ROOT.resolve() not in path.resolve().parents:raise ValueError('Public export cannot follow links outside the project')
        if path.name not in FILES and path.suffix not in SUFFIXES:raise ValueError('Unexpected public file type: '+str(path.relative_to(ROOT)))
        if path.stat().st_size>2*1024**2:raise ValueError('Large artifacts do not belong in source export')
        if path.suffix not in ('.png',):
            content=path.read_text(encoding='utf-8')
            if any(pattern.search(content) for pattern in SENSITIVE):raise ValueError('Potential private data in '+str(path.relative_to(ROOT)))
            if path.suffix=='.json':
                def check(value):
                    if isinstance(value,dict):
                        if {'serial','disk_guid','regions','encrypted_key','secdiscardable','keymaster_key_blob','accounts'} & set(value):
                            raise ValueError('Machine capture/key data cannot be exported as public JSON')
                        for child in value.values():check(child)
                    elif isinstance(value,list):
                        for child in value:check(child)
                check(json.loads(content))
    missing=FILES-{p.name for p in selected}
    if missing:raise ValueError('Missing public documents: '+','.join(sorted(missing)))
    return sorted(selected)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--out',type=Path)
    args=parser.parse_args();paths=audit()
    if not args.check and not args.out:parser.error('Use --check or provide --out for the source-only ZIP')
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(args.out,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:archive.write(path,path.relative_to(ROOT).as_posix())
        print('Source-only export:',args.out)
    print(json.dumps(dict(public_files=len(paths),audit_passed=True,uploaded=False),indent=2))
