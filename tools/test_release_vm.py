#!/usr/bin/env python3
"""UEFI tests of release Install/Live against FILES with simulated Windows.

No physical device paths are accepted. Uses OVMF and native NVMe/USB emulation.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from googlebook import gpt,planner


def run(args,**kwargs):return subprocess.run([str(x) for x in args],check=True,**kwargs)


def qmp(socket_path,commands):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.connect(str(socket_path));stream=s.makefile('rwb');json.loads(stream.readline())
        for command in [{'execute':'qmp_capabilities'},*commands]:
            stream.write((json.dumps(command)+'\r\n').encode());stream.flush()
            while True:
                value=json.loads(stream.readline())
                if 'return' in value:break
                if 'error' in value:raise RuntimeError(value)


def prepare(args):
    args.out.mkdir(parents=True);manifest=json.loads((args.build/'stage/release.json').read_text())
    size=(256 if args.mode=='alongside' else 64)*planner.GIB
    original=args.out/'original.raw'
    with original.open('xb') as f:f.truncate(size)
    keep=[]
    if args.mode=='alongside':
        for number,start,n,name in [(1,planner.MIB,256*planner.MIB,'Windows EFI'),(7,2*planner.GIB,72*planner.GIB,'Windows system'),(120,220*planner.GIB,planner.GIB,'Windows recovery')]:
            keep.append(dict(number=number,name=name,type=gpt.ESP_TYPE if number==1 else 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7',partuuid=str(uuid.uuid4()),offset=start,bytes=n,attributes=0x8000000000000000))
    else:
        keep=[dict(number=9,name='Old files',type='ebd0a0a2-b9e5-4433-87c0-68b6b72699c7',partuuid=str(uuid.uuid4()),offset=planner.MIB,bytes=8*planner.GIB,attributes=0)]
    before=gpt.assemble(size,keep)
    with original.open('r+b') as f:
        for r in before['regions']:f.seek(r['offset']);f.write(base64.b64decode(r['data']))
        for p in keep:
            for at in (p['offset'],p['offset']+p['bytes']-4096):f.seek(at);f.write(b'KEEP ORIGINAL PARTITION'.ljust(4096,b'W'))
    (args.out/'original.json').write_text(json.dumps(before,indent=2))
    target=args.out/'target.qcow2';usb=args.out/'usb.qcow2'
    run(['qemu-img','create','-f','qcow2','-F','raw','-b',original,target])
    run(['qemu-img','create','-f','qcow2','-F','raw','-b',args.build/'googlebook-x86.raw',usb])
    cfg=(args.build/'grub.cfg').read_text().replace('set default=0','set default=1').replace('set timeout=8','set timeout=1')
    cfg=cfg.replace('console=tty0 nomodeset quiet',f'console=tty0 console=ttyS0,115200 nomodeset quiet gbos_release_test={args.mode}')
    (args.out/'test-menu.cfg').write_text(cfg)
    run(['grub-mkstandalone','-O','x86_64-efi','--modules=part_gpt fat ext2 normal linux search search_fs_uuid regexp all_video','--locales=','--fonts=','-o',args.out/'test-menu.efi','boot/grub/grub.cfg='+str(args.out/'test-menu.cfg')])
    esp=args.out/'usb-esp.img';shutil.copyfile(args.build/'live-esp.img',esp)
    run(['mcopy','-o','-i',esp,args.out/'test-menu.efi','::/EFI/BOOT/BOOTX64.EFI'])
    with (args.build/'googlebook-x86.raw').open('rb') as f:
        source=gpt.inspect(lambda at,n:(f.seek(at),f.read(n))[1],manifest['image_bytes'])
    esp_part=next(p for p in source['partitions'] if p['name']=='EFI-SYSTEM')
    run(['qemu-io','-f','qcow2','-c',f'write -s {esp} {esp_part["offset"]} {esp_part["bytes"]}',usb])
    shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd',args.out/'vars.fd')
    return manifest,size,before,target,usb


def launch(args,target,usb,boot=False):
    socket_path=args.out/'qmp.sock';socket_path.unlink(missing_ok=True)
    suffix='boot' if boot else 'install'
    cmd=['qemu-system-x86_64','-machine','q35','-enable-kvm','-cpu','host','-smp','8','-m','4G','-nodefaults','-no-reboot',
         '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
         '-drive','if=pflash,format=raw,file='+str(args.out/'vars.fd'),
         '-drive','if=none,id=ssd,format=qcow2,discard=unmap,detect-zeroes=unmap,file='+str(target),
         '-device','nvme,drive=ssd,serial=GBOS_PUBLIC_TEST,addr=0x4,bootindex='+('1' if boot else '2'),
         '-device','VGA,addr=0x1','-display','none','-monitor','none',
         '-serial','file:'+str(args.out/(suffix+'.log')),
         '-qmp','unix:'+str(socket_path)+',server=on,wait=off']
    if not boot:
        cmd+=['-drive','if=none,id=usb,format=qcow2,file='+str(usb),'-device','qemu-xhci,id=xhci,addr=0x2','-device','usb-storage,drive=usb,bus=xhci.0,bootindex=1']
    else:
        cmd+=['-audiodev','wav,id=audio,path='+str(args.out/'audio.wav'),'-device','ich9-intel-hda,id=hda,addr=0x3','-device','hda-duplex,bus=hda.0,audiodev=audio']
    with (args.out/(suffix+'-qemu.log')).open('wb') as f:
        (args.out/(suffix+'.log')).touch()
        proc=subprocess.Popen(cmd,stdout=f,stderr=subprocess.STDOUT)
        (args.out/(suffix+'.pid')).write_text(str(proc.pid))
        deadline=time.monotonic()+(100 if boot else 900);shot=time.monotonic()+25;saved=False
        try:
            while proc.poll() is None and time.monotonic()<deadline:
                if not boot and 'GBOS_RELEASE_TEST_EXIT=1' in (args.out/(suffix+'.log')).read_text(errors='replace'):break
                if not saved and time.monotonic()>shot:
                    qmp(socket_path,[{'execute':'human-monitor-command','arguments':{'command-line':'screendump '+str(args.out/(suffix+'.ppm'))}}]);saved=True
                time.sleep(1)
            natural=proc.poll() is not None
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=20)
    return natural,proc.returncode


def read_overlay(path,at,n,out):
    first=at//4096;end=(at+n+4095)//4096;dest=out/'readback.bin'
    run(['qemu-img','dd','-f','qcow2','if='+str(path),'of='+str(dest),'bs=4096','skip='+str(first),'count='+str(end)],capture_output=True)
    data=dest.read_bytes();return data[at-first*4096:at-first*4096+n]


def test(args):
    if args.out.exists() and not (args.boot_only or args.analyze_only):raise ValueError('Preserve previous VM output')
    if not (args.build/'googlebook-x86.raw').is_file():raise ValueError('The test source must be a regular image file')
    if args.boot_only or args.analyze_only:
        manifest=json.loads((args.build/'stage/release.json').read_text());before=json.loads((args.out/'original.json').read_text())
        size=before['disk_bytes'];target=args.out/'target.qcow2';usb=args.out/'usb.qcow2';natural=True;exitcode=0
    else:
        manifest,size,before,target,usb=prepare(args)
        natural,exitcode=launch(args,target,usb)
    text=(args.out/'install.log').read_text(errors='replace')
    if 'GBOS_RELEASE_TEST_EXIT=0' not in text:raise AssertionError('Installer failed; inspect install.log and install.ppm')
    read=lambda at,n:read_overlay(target,at,n,args.out)
    actual=gpt.inspect(read,size)
    result=dict(mode=args.mode,physical_disk_access=False,installer_completed=True,
                automatic_shutdown=natural and exitcode==0,both_gpt_copies_valid=True,
                new_partition_count=len(actual['partitions'])-len(before['partitions']) if args.mode=='alongside' else len(actual['partitions']))
    if args.mode=='alongside':
        after_table=gpt.raw(actual)[2];old_table=gpt.raw(before)[2]
        result['original_entries_byte_exact']=all(after_table[(p['number']-1)*128:p['number']*128]==old_table[(p['number']-1)*128:p['number']*128] for p in before['partitions'])
        result['original_disk_guid_preserved']=actual['disk_guid']==before['disk_guid']
        result['windows_content_markers_preserved']=all(read(at,4096)==b'KEEP ORIGINAL PARTITION'.ljust(4096,b'W') for p in before['partitions'] for at in (p['offset'],p['offset']+p['bytes']-4096))
        assert result['original_entries_byte_exact'] and result['original_disk_guid_preserved'] and result['windows_content_markers_preserved']
    else:
        result['old_partition_removed']=all(p['name']!='Old files' for p in actual['partitions'])
        result['fresh_disk_guid']=actual['disk_guid']!=before['disk_guid']
        assert result['old_partition_removed'] and result['fresh_disk_guid']
    # Add serial output ONLY to this test's installed config; keep the actual
    # installed bootstrap and dynamically generated boot UUIDs unchanged.
    esp=next(p for p in actual['partitions'] if p['name']=='EFI-SYSTEM')
    esp_file=args.out/'installed-esp.img'
    if not args.analyze_only:esp_file.write_bytes(read(esp['offset'],esp['bytes']))
    cfg=args.out/'installed.cfg'
    if not args.analyze_only:
        cfg.unlink(missing_ok=True)
        run(['mcopy','-o','-i',esp_file,'::/EFI/Googlebook/grub.cfg',cfg])
        text=cfg.read_text().replace(' quiet ',' ').replace('loglevel=3','loglevel=7')
        text=text.replace('console=ttyS0,115200 ','')
        cfg.write_text(text.replace('linux /EFI/Googlebook/kernel ','linux /EFI/Googlebook/kernel console=ttyS0,115200 '))
    loader=args.out/'installed-serial.efi'
    if not args.analyze_only:
        run(['grub-mkstandalone','-O','x86_64-efi',
             '--modules=part_gpt fat normal linux search search_fs_uuid all_video',
             '--locales=','--fonts=','-o',loader,'boot/grub/grub.cfg='+str(cfg)])
        run(['mcopy','-o','-i',esp_file,loader,'::/EFI/BOOT/BOOTX64.EFI'])
        run(['mcopy','-o','-i',esp_file,cfg,'::/EFI/Googlebook/grub.cfg'])
        run(['qemu-io','-f','qcow2','-c',f'write -s {esp_file} {esp["offset"]} {esp["bytes"]}',target])
    if args.reset_test_data:
        import gzip
        metadata=next(p for p in actual['partitions'] if p['name']=='metadata')
        factory=next(p for p in manifest['payloads'] if p['name']=='metadata')
        fresh=args.out/'test-metadata.ext4'
        with gzip.open(args.build/'stage'/factory['asset'],'rb') as a,fresh.open('wb') as b:shutil.copyfileobj(a,b)
        run(['qemu-io','-f','qcow2','-c',f'write -s {fresh} {metadata["offset"]} {metadata["bytes"]}',target])
        userdata=next(p for p in actual['partitions'] if p['name']=='userdata')
        for at in (userdata['offset'],userdata['offset']+userdata['bytes']-planner.MIB):
            run(['qemu-io','-f','qcow2','-c',f'write -z {at} {planner.MIB}',target])
    if not args.analyze_only:launch(args,target,usb,boot=True)
    boot=(args.out/'boot.log').read_text(errors='replace')
    result['installed_boots_without_usb']='Linux version 6.18.21' in boot and 'androidboot.gbos_storage=nvme' in boot
    metadata=next(p for p in actual['partitions'] if p['name']=='metadata')
    meta_file=args.out/'mounted-metadata.ext4';meta_file.write_bytes(read(metadata['offset'],metadata['bytes']))
    keys=subprocess.run(['debugfs','-R','ls -p /vold/metadata_encryption/key',str(meta_file)],capture_output=True,text=True).stdout
    userdata=next(p for p in actual['partitions'] if p['name']=='userdata');header=read(userdata['offset'],4096)
    import re
    result['metadata_encryption_key_created']='/encrypted_key/' in keys and '/secdiscardable/' in keys
    result['userdata_header_encrypted']=any(header) and header[1080:1082]!=bytes([0x53,0xef])
    result['kernel_used_userdata_dm_mount']=bool(re.search(r'path="/data[^"\n]*".*dev="dm-\d+"',boot)) or 'Encryption policy of /data/app-metadata set to' in boot
    result['data_encryption_policy_error']='Failed to set encryption policy of /data/' in boot
    result['fresh_encrypted_data_mounted']=result['metadata_encryption_key_created'] and result['userdata_header_encrypted'] and result['kernel_used_userdata_dm_mount'] and not result['data_encryption_policy_error']
    result['lab_recording_service_not_started']="starting service 'gbos_bringup_diagnostics'" not in boot and "starting service 'gbos_audio_capability_inspect'" not in boot
    # Read the actual installer receipt from the source USB overlay.
    source_read=lambda at,n:read_overlay(usb,at,n,args.out)
    source_gpt=gpt.inspect(source_read,manifest['image_bytes'])
    stage_part=next(p for p in source_gpt['partitions'] if p['name']=='GBOS-INSTALL')
    stage_file=args.out/'receipt-stage.ext4';stage_file.write_bytes(source_read(stage_part['offset'],stage_part['bytes']))
    listing=subprocess.check_output(['debugfs','-R','ls -p /',str(stage_file)],text=True)
    import re
    names=re.findall(r'^/\d+/040755/\d+/\d+/(install-\d+)/',listing,re.M)
    if len(names)!=1:raise AssertionError('Expected one durable installer receipt')
    receipt_file=args.out/'receipt.json'
    run(['debugfs','-R',f'dump /{names[0]}/result.json {receipt_file}',stage_file],capture_output=True)
    receipt=json.loads(receipt_file.read_text());fw=receipt.get('firmware',{})
    result['firmware_entry_created']=receipt.get('firmware_registered') is True
    result['original_firmware_order_preserved']=fw.get('boot_order')==fw.get('original_boot_order',[])+[fw.get('entry')]
    result['firmware_partition_uuid_matches']=fw.get('label')=='Googlebook '+esp['partuuid'][:8]
    kept={p['number'] for p in before['partitions']} if args.mode=='alongside' else set()
    native=[p for p in actual['partitions'] if p['number'] not in kept]
    start=min(p['offset'] for p in native);end=max(p['offset']+p['bytes'] for p in native)
    allowed=gpt.regions(size)[1:] if args.mode=='alongside' else gpt.regions(size)
    result['installer_writes_bounded']=all((r['offset'],r['bytes']) in allowed if r['gpt'] else start<=r['offset']<r['offset']+r['bytes']<=end for r in receipt['write_ranges'])
    result['original_mbr_preserved']=args.mode!='alongside' or gpt.raw(actual)[0]==gpt.raw(before)[0]
    (args.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    assert result['automatic_shutdown'] and result['new_partition_count']==30 and result['installed_boots_without_usb'] and result['fresh_encrypted_data_mounted'] and result['lab_recording_service_not_started'] and result['firmware_entry_created'] and result['original_firmware_order_preserved'] and result['installer_writes_bounded'] and result['original_mbr_preserved']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--mode',choices=['alongside','erase'],required=True)
    p.add_argument('--boot-only',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--reset-test-data',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--analyze-only',action='store_true',help=argparse.SUPPRESS)
    test(p.parse_args())
