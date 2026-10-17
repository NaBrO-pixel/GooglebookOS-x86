"""User-facing offline Install flow. No action is selected by default."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import devices, firmware, gpt, planner
from .installer import Installer, check_manifest


def check_cpu(required,cpuinfo=Path('/proc/cpuinfo')):
    lines=cpuinfo.read_text().splitlines()
    sets=[set(line.split(':',1)[1].split()) for line in lines if line.startswith('flags')]
    available=set.intersection(*sets) if sets else set()
    missing=set(required)-available
    if missing:raise ValueError('CPU outside this release profile; missing features: '+', '.join(sorted(missing)))


def choose(prompt,values):
    print('\n'+prompt,flush=True)
    for number,value in enumerate(values,1):print(f'  {number}. {value}',flush=True)
    text=input('Choose a number, or press Enter to cancel: ').strip()
    if not text:return None
    if not text.isdigit() or not 1<=int(text)<=len(values):raise ValueError('Invalid choice; no installation started')
    return int(text)-1


def run():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',default='/stage')
    parser.add_argument('--test-mode',choices=['alongside','erase'],help=argparse.SUPPRESS)
    args=parser.parse_args();stage=Path(args.stage);manifest=json.loads((stage/'release.json').read_text())
    if sys.stdout.isatty():print('\x1b[2J\x1b[H',end='',flush=True)
    print('Googlebook OS - Install\nChecking release files...',flush=True)
    check_manifest(stage,manifest);check_cpu(manifest.get('required_cpu_flags',[]));source=devices.find_source(stage,manifest);targets=[]
    try:
        transports=tuple(manifest.get('install_transports',['nvme']))
        targets=devices.targets(source,transports)
        if not targets:raise ValueError('No idle, supported '+'/'.join(t.upper() for t in transports)+' disk found. The source USB is protected.')
        descriptions=[f'{d.path} - {d.identity["model"]} - {d.size/1024**3:.1f} GiB - ID {d.token}' for d in targets]
        if args.test_mode:
            vm=Path('/sys/class/dmi/id/product_name').read_text()
            targets_test=[d for d in targets if d.identity['serial']=='GBOS_PUBLIC_TEST']
            if ('QEMU' not in vm and 'Standard PC' not in vm) or len(targets_test)!=1:raise ValueError('Automatic testing requires the dedicated QEMU disk')
            target=targets_test[0];mode=args.test_mode
        else:
            index=choose('Select the internal installation disk. Source USB cannot be selected.',descriptions)
            if index is None:return 2
            target=targets[index];mode=None
        snapshot=None;error=None
        try:snapshot=target.snapshot();extents=planner.eligible_extents(snapshot,manifest['payloads'])
        except ValueError as exc:extents=[];error=str(exc)
        alongside=bool(extents) and firmware.available()
        modes=['erase'];labels=['Erase this disk and install - removes ALL existing partitions and data']
        if alongside:modes.insert(0,'alongside');labels.insert(0,'Install alongside - use existing unallocated space, preserve other partitions')
        else:print('Alongside unavailable: '+(error or 'no qualifying 32 GiB gap or writable UEFI boot variables'),flush=True)
        if mode is None:
            index=choose('Choose installation mode',labels)
            if index is None:return 2
            mode=modes[index]
        elif mode not in modes:raise ValueError('Requested test mode is not eligible')
        extent=None;requested=None
        if mode=='alongside':
            if args.test_mode:extent=max(extents,key=lambda r:r[1]-r[0])
            else:
                index=choose('Choose unallocated region',[f'{(b-a)/planner.GIB:.1f} GiB at {a/planner.GIB:.2f} GiB' for a,b in extents])
                if index is None:return 2
                extent=extents[index]
                answer=input('Allocation in whole GiB (minimum 32), or Enter to use this entire free region: ').strip()
                if answer:
                    if not answer.isdigit():raise ValueError('Enter a whole number of GiB')
                    requested=int(answer)
        plan=planner.plan(mode,target.size,manifest['payloads'],snapshot,extent,requested)
        print(f'\nREVIEW\nDisk: {target.path} / {target.identity["model"]} / {target.size/planner.GIB:.1f} GiB / ID {target.token}',flush=True)
        print(f'Mode: {mode}\nGooglebook space: {(plan.end-plan.start)/planner.GIB:.1f} GiB\nFresh apps/data: {plan.partitions[-1]["bytes"]/planner.GIB:.1f} GiB',flush=True)
        print('Existing partitions will be preserved.' if mode=='alongside' else 'ALL existing disk partitions and files will be removed. This is not a secure erase.',flush=True)
        print('The source USB is preserved. GPT backups and the installation result stay on it.\nAfter success, the computer shuts down automatically.',flush=True)
        verb='INSTALL' if mode=='alongside' else 'ERASE';expected=verb+' '+target.token
        if args.test_mode:confirmation=expected
        else:
            print('Type '+expected+' to proceed, or Enter to cancel:',flush=True);confirmation=input().strip()
            if not confirmation:return 2
            if confirmation!=expected:raise ValueError('Confirmation did not match the selected disk')
        subprocess.run(['mount','-o','remount,rw',str(stage)],check=True)
        Installer(stage,source,target,manifest,plan).install(confirmation)
        return 0
    finally:
        for target in targets:target.close()
        source.close()


if __name__=='__main__':
    try:raise SystemExit(run())
    except Exception as exc:print('\nSTOPPED: '+str(exc)+'\nKeep the USB and its installation backups.',flush=True);raise SystemExit(1)
