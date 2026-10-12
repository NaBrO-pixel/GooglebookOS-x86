#!/usr/bin/env python3
"""Capture the two boot choices and installer review using disk files only.

Only navigates to review; never sends an installation confirmation.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from test_release_vm import qmp,run


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True);p.add_argument('--original',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists() or not a.original.is_file() or not (a.build/'googlebook-x86.raw').is_file():raise ValueError('New output directory and regular disk files required')
    a.out.mkdir(parents=True)
    target=a.out/'target.qcow2';usb=a.out/'usb.qcow2'
    run(['qemu-img','create','-f','qcow2','-F','raw','-b',a.original,target])
    run(['qemu-img','create','-f','qcow2','-F','raw','-b',a.build/'googlebook-x86.raw',usb])
    shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd',a.out/'vars.fd')
    sock=a.out/'qmp.sock'
    command=['qemu-system-x86_64','-machine','q35','-enable-kvm','-cpu','host','-smp','4','-m','2G','-nodefaults','-no-reboot',
      '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd','-drive','if=pflash,format=raw,file='+str(a.out/'vars.fd'),
      '-drive','if=none,id=ssd,format=qcow2,file='+str(target),'-device','nvme,drive=ssd,serial=GBOS_WALKTHROUGH,addr=0x4',
      '-drive','if=none,id=usb,format=qcow2,file='+str(usb),'-device','qemu-xhci,id=xhci,addr=0x2','-device','usb-storage,drive=usb,bus=xhci.0,bootindex=1',
      '-device','usb-kbd,bus=xhci.0','-device','VGA,addr=0x1','-display','none','-monitor','none',
      '-serial','file:'+str(a.out/'serial.log'),'-qmp','unix:'+str(sock)+',server=on,wait=off']
    with (a.out/'qemu.log').open('wb') as log:
        vm=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
        def keys(*values):
            for value in values:
                qmp(sock,[{'execute':'send-key','arguments':{'keys':[{'type':'qcode','data':value}],'hold-time':100}}]);time.sleep(0.25)
        def screenshot(name):qmp(sock,[{'execute':'screendump','arguments':{'filename':str(a.out/(name+'.ppm'))}}])
        try:
            time.sleep(4);screenshot('boot-menu');keys('down','ret')
            time.sleep(16);screenshot('select-disk');keys('1','ret');time.sleep(1)
            screenshot('select-mode');keys('1','ret');time.sleep(1)
            keys('1','ret');time.sleep(1);keys('ret');time.sleep(1);screenshot('install-review')
            keys('ret');time.sleep(2)
        finally:
            if vm.poll() is None:vm.terminate();vm.wait(timeout=20)
    mapping=json.loads(subprocess.check_output(['qemu-img','map','--output=json',str(target)]))
    if any(r.get('depth')==0 and r.get('present') and r.get('data') for r in mapping):raise AssertionError('Walkthrough unexpectedly changed the target')
    (a.out/'result.json').write_text(json.dumps(dict(physical_disk_access=False,installation_confirmation_sent=False,target_data_unchanged=True),indent=2))
