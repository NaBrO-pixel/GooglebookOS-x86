"""Register only our own loader, appending to the existing UEFI boot order."""
from pathlib import Path
import os
import re
import subprocess


def available():
    path=Path('/sys/firmware/efi/efivars')
    return path.is_dir() and any(path.glob('BootOrder-*')) and not os.statvfs(path).f_flag & os.ST_RDONLY


def register(disk,esp,runner=subprocess.run):
    def call(args):return runner(['efibootmgr',*args],check=True,text=True,capture_output=True).stdout
    before=call(['--verbose'])
    order=re.search(r'^BootOrder:\s*([0-9A-Fa-f,]*)',before,re.M)
    original=order[1].split(',') if order and order[1] else []
    entries={m[1].upper():m[0] for m in re.finditer(r'^Boot([0-9A-Fa-f]{4})\*?\s+.*$',before,re.M)}
    label='Googlebook '+esp['partuuid'][:8]
    # create-only never changes BootOrder as a side effect. Existing entries
    # are never deleted or reused, including any stale prior Googlebook entry.
    call(['--create-only','--disk',str(disk.path),'--part',str(esp['number']),
          '--label',label,'--loader',r'\EFI\BOOT\BOOTX64.EFI'])
    current=call(['--verbose'])
    after={m[1].upper():m[0] for m in re.finditer(r'^Boot([0-9A-Fa-f]{4})\*?\s+.*$',current,re.M)}
    added=set(after)-set(entries)
    if len(added)!=1 or any(after.get(k)!=v for k,v in entries.items()):raise IOError('Unexpected firmware-entry change')
    number=added.pop()
    if label not in after[number] or esp['partuuid'].lower() not in after[number].lower():raise IOError('Firmware entry does not reference the installed ESP')
    desired=original+[number]
    try:
        call(['--bootorder',','.join(desired)])
        result=call(['--verbose'])
        got=re.search(r'^BootOrder:\s*([0-9A-Fa-f,]*)',result,re.M)
        if not got or got[1].upper()!=','.join(desired).upper():raise IOError('Firmware did not retain the requested boot order')
    except Exception:
        if original:call(['--bootorder',','.join(original)])
        raise
    return dict(label=label,entry=number,original_boot_order=original,boot_order=desired)
