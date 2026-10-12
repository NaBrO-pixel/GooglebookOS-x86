#!/usr/bin/env python3
"""Smoke-test Live using only a USB disk file; no internal disk is attached."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from test_release_vm import run,qmp


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists() or not (a.build/'googlebook-x86.raw').is_file():raise ValueError('New output directory and regular source image required')
    a.out.mkdir(parents=True);usb=a.out/'usb.qcow2'
    run(['qemu-img','create','-f','qcow2','-F','raw','-b',a.build/'googlebook-x86.raw',usb])
    config=(a.build/'grub.cfg').read_text().replace('set timeout=8','set timeout=1')
    config=config.replace('loglevel=3 quiet','loglevel=7 console=ttyS0,115200',1)
    cfg=a.out/'live-serial.cfg';cfg.write_text(config);loader=a.out/'live-serial.efi'
    run(['grub-mkstandalone','-O','x86_64-efi','--modules=part_gpt fat ext2 normal linux search search_fs_uuid all_video','--locales=','--fonts=','-o',loader,'boot/grub/grub.cfg='+str(cfg)])
    esp=a.out/'esp.img';shutil.copyfile(a.build/'live-esp.img',esp)
    run(['mcopy','-o','-i',esp,loader,'::/EFI/BOOT/BOOTX64.EFI'])
    from googlebook import gpt
    image=a.build/'googlebook-x86.raw'
    with image.open('rb') as f:snapshot=gpt.inspect(lambda at,n:(f.seek(at),f.read(n))[1],image.stat().st_size)
    esp_part=next(p for p in snapshot['partitions'] if p['name']=='EFI-SYSTEM')
    boot=next(p for p in snapshot['partitions'] if p['name']=='boot_a')
    run(['qemu-io','-f','qcow2','-c',f'write -s {esp} {esp_part["offset"]} {esp_part["bytes"]}',usb])
    shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd',a.out/'vars.fd');sock=a.out/'qmp.sock'
    command=['qemu-system-x86_64','-machine','q35','-enable-kvm','-cpu','host','-smp','8','-m','3G','-nodefaults','-no-reboot',
      '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd','-drive','if=pflash,format=raw,file='+str(a.out/'vars.fd'),
      '-drive','if=none,id=usb,format=qcow2,discard=unmap,detect-zeroes=unmap,file='+str(usb),
      '-device','qemu-xhci,id=xhci,addr=0x2','-device','usb-storage,drive=usb,bus=xhci.0,bootindex=1',
      '-device','virtio-gpu-pci,addr=0x1','-display','none','-monitor','none',
      '-audiodev','wav,id=audio,path='+str(a.out/'audio.wav'),'-device','ich9-intel-hda,id=hda,addr=0x3','-device','hda-duplex,bus=hda.0,audiodev=audio',
      '-serial','file:'+str(a.out/'serial.log'),'-qmp','unix:'+str(sock)+',server=on,wait=off']
    with (a.out/'qemu.log').open('wb') as log:
        vm=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+70
            while vm.poll() is None and time.monotonic()<deadline:time.sleep(1)
        finally:
            if vm.poll() is None:vm.terminate();vm.wait(timeout=20)
    text=(a.out/'serial.log').read_text(errors='replace')
    result=dict(physical_disk_access=False,no_internal_disk_attached=True,
                live_kernel_booted='Linux version 6.18.21' in text,
                unique_live_boot_uuid_selected=boot['partuuid'] in text,
                usb_native_partitions_detected='sda:' in text,
                custom_capture_services_not_started="starting service 'gbos_bringup_diagnostics'" not in text and "starting service 'gbos_audio_capability_inspect'" not in text,
                native_desktop_validated=False)
    (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    assert result['live_kernel_booted'] and result['unique_live_boot_uuid_selected'] and result['usb_native_partitions_detected'] and result['custom_capture_services_not_started']
