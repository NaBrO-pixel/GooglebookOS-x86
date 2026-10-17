#!/usr/bin/env python3
"""Build a two-entry, fresh-data release from a locally prepared port image.

All modifications target newly created regular files. Never opens a device.
Upstream helpers are imported from an explicit external checkout, not vendored.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from googlebook import gpt

MIB=1024**2


def command(args,**kwargs):return subprocess.run([str(x) for x in args],check=True,**kwargs)
def hash_file(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def append_erofs_file(image,path,data,checksum):
    info=subprocess.check_output(['dump.erofs','--path='+path,str(image)],text=True)
    nid=int(re.search(r'NID: (\d+)',info)[1])
    with image.open('r+b') as f:
        f.seek(1024);sb=f.read(128);inode_at=struct.unpack_from('<I',sb,40)[0]*4096+nid*32
        f.seek(inode_at);inode=bytearray(f.read(64));fmt=struct.unpack_from('<H',inode)[0]
        struct.pack_into('<H',inode,0,fmt&~0xe);struct.pack_into('<Q' if fmt&1 else '<I',inode,8,len(data))
        at=image.stat().st_size
        if at%4096:raise ValueError('Unexpected EROFS append alignment')
        struct.pack_into('<I',inode,16,at//4096);f.seek(inode_at);f.write(inode[:64 if fmt&1 else 32])
        f.seek(at);f.write(data.ljust((len(data)+4095)//4096*4096,b'\0'));end=f.tell()
        f.seek(1060);f.write(struct.pack('<I',end//4096))
    checksum(image)
    actual=subprocess.check_output(['dump.erofs','--cat','--path='+path,str(image)])
    if actual!=data:raise IOError('Clean runtime EROFS readback failed')


def clean_vendor(image,snapshot,upstream,out):
    sys.path.insert(0,str(upstream));import lp_tools
    from make_lapis_security_port import update_sb_checksum
    super_part=next(p for p in snapshot['partitions'] if p['name']=='super')
    sup=lp_tools.Super(image,super_part['offset']);offset,size=sup.layout('vendor_a')[0]
    vendor=out/'vendor-clean.erofs'
    with image.open('rb') as f:f.seek(offset);vendor.write_bytes(f.read(size))
    def cat(name):return subprocess.check_output(['dump.erofs','--cat','--path='+name,str(vendor)])
    bringup=cat('/etc/init/zz-googlebook-bringup.rc').decode()
    bringup=re.sub(r'\nservice gbos_bringup_diagnostics .*?(?=\non |\Z)','\n',bringup,flags=re.S)
    bringup='\n'.join(line for line in bringup.splitlines() if 'start gbos_bringup_diagnostics' not in line)+'\n'
    # Read actual CPU topology through init's copy builtin. Remove recovery's
    # platform-specific affinity actions so the power HAL cannot reset masks.
    online='/sys/devices/system/cpu/online'
    cpu='\n# Use detected CPU topology, retaining scheduler weights and thermal policy.\n'
    for trigger in ('on post-fs-data','on property:sys.boot_completed=1'):
        cpu+=trigger+'\n'
        for group in ('','top-app','foreground','foreground_window','background','system-background','restricted'):
            cpu+=f'    copy {online} /dev/cpuset/{group+"/" if group else ""}cpus\n'
    audio=cat('/etc/init/zz-googlebook-display-audio.rc').decode().split('# Enabled only by')[0]
    power=json.loads(cat('/etc/powerhint.json'))
    removed={n['Name'] for n in power['Nodes'] if n['Name'].endswith('Cpuset') or n['Name'] in ('WorkQueueAffinity','XeIrqAffinity')}
    power['Nodes']=[n for n in power['Nodes'] if n['Name'] not in removed]
    power['Actions']=[a for a in power['Actions'] if a.get('Node') not in removed]
    replacements={
      '/etc/init/zz-googlebook-bringup.rc':(bringup+cpu).encode(),
      '/etc/init/zz-googlebook-display-audio.rc':audio.encode(),
      '/etc/googlebook-bringup.sh':b'#!/system/bin/sh\n# Automatic lab recording is removed from public builds.\nexit 0\n',
      '/etc/powerhint.json':(json.dumps(power,indent=2)+'\n').encode(),
    }
    if getattr(clean_vendor,'audio_helper',None):replacements['/etc/googlebook-audio-init']=clean_vendor.audio_helper.read_bytes()
    for name,data in replacements.items():append_erofs_file(vendor,name,data,update_sb_checksum)
    command(['fsck.erofs',vendor])
    length=vendor.stat().st_size
    if any(offset<b and a<offset+length for a,b in sup.used_ranges() if a!=offset):raise ValueError('Expanded vendor overlaps another logical partition')
    with image.open('r+b') as f:f.seek(offset);f.write(vendor.read_bytes())
    lp_tools.Super(image,super_part['offset']).set_single_extent('vendor_a',length//512,(offset-super_part['offset'])//512)
    return dict(automatic_lab_capture_removed=True,audio_clock_test_removed=True,
                postboot_audio_inspection_removed=True,cpu_masks_use_detected_topology=True,
                removed_cpu_affinity_nodes=sorted(removed))


def maintenance(stage,args,manifest):
    root=args.out/'maintenance-root'
    if root.exists():
        if root.is_symlink() or root.resolve().parent!=args.out.resolve():raise ValueError('Unsafe generated maintenance-root path')
        shutil.rmtree(root)
    shutil.copytree(args.maintenance_root,root,symlinks=True)
    shutil.copytree(ROOT/'src/googlebook',root/'usr/lib/googlebook',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    # mtools loads glibc's DOS-codepage converters dynamically; ldd alone does
    # not find them. Include their registry/cache and conversion modules.
    gconv=Path('/usr/lib/x86_64-linux-gnu/gconv')
    shutil.copytree(gconv,root/'usr/lib/x86_64-linux-gnu/gconv',dirs_exist_ok=True)
    shutil.copytree('/usr/lib/grub/x86_64-efi',root/'usr/lib/grub/x86_64-efi',dirs_exist_ok=True)
    for old in ('ssd-offline-install.py','ssd-gpt.py'):
        (root/old).unlink(missing_ok=True)
    binaries=['/usr/bin/mcopy','/usr/bin/mmd','/usr/sbin/mkfs.fat','/usr/sbin/fsck.fat','/usr/sbin/efibootmgr',
              '/usr/bin/grub-mkstandalone','/usr/bin/grub-mkimage']
    libraries=set()
    for name in binaries:
        p=Path(name)
        if not p.exists():p=Path(shutil.which(p.name) or name)
        dest=root/str(p).lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        for line in subprocess.check_output(['ldd',str(p)],text=True).splitlines():libraries.update(re.findall(r'(/[^\s()]+)',line))
    for name in libraries:
        if Path(name).is_file():
            dest=root/name.lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(name,dest)
    init='''#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PYTHONPATH=/usr/lib
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts /sys/firmware/efi/efivars
mount -t devpts devpts /dev/pts
mount -t efivarfs efivarfs /sys/firmware/efi/efivars 2>/dev/null
for m in nvme ahci xhci_pci ehci_pci ohci_pci usb_storage uas sd_mod ext4 vfat usbhid hid_generic; do modprobe "$m" 2>/dev/null; done
device=''
for attempt in $(seq 1 45); do
    for p in /dev/sd*[0-9] /dev/nvme*n*p[0-9]*; do
        [ -b "$p" ] || continue
        if [ "$(blkid -s UUID -o value "$p" 2>/dev/null)" = '@STAGE_UUID@' ]; then device="$p"; break; fi
    done
    [ -n "$device" ] && break
    echo "Waiting for installer USB: $attempt/45"
    sleep 1
done
if [ -n "$device" ] && mount -t ext4 -o ro "$device" /stage; then
    test_mode=''
    case " $(cat /proc/cmdline) " in
        *' gbos_release_test=alongside '*) test_mode=alongside ;;
        *' gbos_release_test=erase '*) test_mode=erase ;;
    esac
    if [ -n "$test_mode" ]; then
        python3.13 -m googlebook.cli --test-mode "$test_mode"
    else
        python3.13 -m googlebook.cli
    fi
    status=$?
    if [ -n "$test_mode" ]; then
        echo "GBOS_RELEASE_TEST_EXIT=$status"
        echo "GBOS_RELEASE_TEST_EXIT=$status" > /dev/ttyS0
    fi
    if [ "$status" -eq 0 ] && sync && umount /stage; then
        echo 'Installation complete. Shutting down automatically in 10 seconds.'
        [ -n "$test_mode" ] && echo 'GBOS_RELEASE_SHUTDOWN_READY' > /dev/ttyS0
        sleep 10
        poweroff -f
    fi
    if [ "$status" -eq 2 ]; then sync; umount /stage; poweroff -f; fi
else
    echo 'STOPPED: release USB could not be mounted. No target writes performed.'
fi
echo 'Installer stopped. Press Enter to shut down; keep the USB and its backups.'
read answer
sync
umount /stage 2>/dev/null
poweroff -f
'''.replace('@STAGE_UUID@',manifest['stage_fs_uuid'])
    (root/'init').write_text(init);(root/'init').chmod(0o755)
    command(['sh','-n',root/'init'])
    with (stage/'maintenance-initrd.gz').open('wb') as f:
        command(['sh','-c','find . -print0 | cpio --null -o -H newc 2>/dev/null | gzip -6'],cwd=root,stdout=f)
    shutil.copy2(args.maintenance_kernel,stage/'maintenance-kernel')


def build(args):
    clean_vendor.audio_helper=args.audio_helper
    for p in (args.base_image,args.kernel,args.native_initrd,args.maintenance_kernel,args.audio_helper):
        if not p.is_file() or not stat.S_ISREG(p.stat().st_mode):raise ValueError('Build inputs must be regular files')
    if args.out.exists():raise ValueError('Choose a new output directory; previous builds are preserved')
    args.out.mkdir(parents=True);stage=args.out/'stage';stage.mkdir()
    image=args.out/'googlebook-x86.raw'
    if getattr(args,'consume_generated_base',False):
        marker=args.base_image.parent/'native-build.json'
        if args.base_image.is_symlink() or args.base_image.parent.resolve().parent!=args.out.resolve().parent:
            raise ValueError('Generated base must be a sibling stage in the same build directory')
        receipt=json.loads(marker.read_text())
        if receipt.get('fresh_from_pinned_recovery') is not True or receipt.get('image_name')!=args.base_image.name:
            raise ValueError('Only a generated fresh base can be moved into the release stage')
        args.base_image.rename(image)
    else:
        command(['cp','--reflink=auto','--sparse=always',args.base_image,image])
    with image.open('rb') as f:
        snapshot=gpt.inspect(lambda at,n:(f.seek(at),f.read(n))[1],image.stat().st_size)
    parts=snapshot['partitions']
    if len(parts)!=30:raise ValueError('The prepared input must contain the 30 native partitions')
    import uuid
    parts=[dict(p,partuuid=str(uuid.uuid4())) for p in parts]
    clean=clean_vendor(image,snapshot,args.upstream,args.out)
    metadata=next(p for p in parts if p['name']=='metadata');userdata=next(p for p in parts if p['name']=='userdata')
    fresh=args.out/'metadata.ext4'
    with fresh.open('xb') as f:f.truncate(metadata['bytes'])
    command(['mkfs.ext4','-q','-F','-b','4096','-m','0','-E','lazy_itable_init=0,lazy_journal_init=0',fresh])
    with fresh.open('rb') as a,image.open('r+b') as b:b.seek(metadata['offset']);shutil.copyfileobj(a,b)
    command(['fallocate','--punch-hole','--keep-size','--offset',userdata['offset'],'--length',userdata['bytes'],image])
    profile=json.loads(args.profile.read_text());kernel_args=profile['kernel_args']
    if kernel_args.count('@BOOT_UUID@')!=1:raise ValueError('Profile must contain one boot UUID placeholder')
    (stage/'kernel-args.txt').write_text(kernel_args+'\n')
    shutil.copy2(args.kernel,stage/'native-kernel');shutil.copy2(args.native_initrd,stage/'native-initrd')
    factory={'metadata','misc','desktop_security_persist','desktop_security_storage','frp_persist'}
    payloads=[]
    with image.open('rb') as f:
        for p in parts:
            item={k:p[k] for k in ('name','type','bytes','attributes')};item['source_offset']=p['offset']
            if p['name'] in ('userdata','EFI-SYSTEM'):payloads.append(item);continue
            h=hashlib.sha256();item['chunks']=[]
            asset=p['name']+'.gz' if p['name'] in factory else None
            stream=gzip.open(stage/asset,'wb',compresslevel=6) if asset else None
            f.seek(p['offset'])
            try:
                for at in range(0,p['bytes'],4*MIB):
                    data=f.read(min(4*MIB,p['bytes']-at));h.update(data)
                    item['chunks'].append(dict(at=at,bytes=len(data),sha256=gpt.digest(data),zero=data==bytes(len(data))))
                    if stream:stream.write(data)
            finally:
                if stream:stream.close()
            item['payload_sha256']=h.hexdigest()
            if asset:item['asset']=asset
            payloads.append(item)
            print('Authenticated release payload:',p['name'],flush=True)
    # Append maintenance storage; move both GPT copies to the image's new end.
    stage_offset=(image.stat().st_size+MIB-1)//MIB*MIB
    stage_part=dict(number=32,name='GBOS-INSTALL',type='0fc63daf-8483-4772-8e79-3d69d8477de4',partuuid=str(uuid.uuid4()),offset=stage_offset,bytes=256*MIB,attributes=0)
    new_size=stage_offset+stage_part['bytes']+MIB
    source_gpt=gpt.assemble(new_size,parts+[stage_part])
    manifest=dict(format=1,version='0.1.0-rc1',profile=profile['name'],image_bytes=new_size,
                  required_cpu_flags=profile['required_cpu_flags'],
                  install_transports=profile.get('install_transports',['nvme']),
                  source_disk_guid=source_gpt['disk_guid'],source_table_sha256=gpt.digest(gpt.raw(source_gpt)[2]),
                  stage_fs_uuid=str(uuid.uuid4()),payloads=payloads,assets={})
    maintenance(stage,args,manifest)
    manifest['assets']={p.name:dict(bytes=p.stat().st_size,sha256=hash_file(p)) for p in stage.iterdir() if p.is_file()}
    (stage/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    live_boot=next(p for p in parts if p['name']=='boot_a');live_args=kernel_args.replace('@BOOT_UUID@',live_boot['partuuid'])
    live_fat_id=uuid.uuid4().hex[:8].upper();live_fat_uuid=live_fat_id[:4]+'-'+live_fat_id[4:]
    config=f'''set timeout=8
set default=0
insmod all_video
terminal_output console
search --fs-uuid --set=root {live_fat_uuid}
menuentry 'Live' {{
    linux /EFI/Googlebook/kernel {live_args}
    initrd /EFI/Googlebook/initrd
}}
menuentry 'Install' {{
    echo 'Loading the offline installer...'
    search --fs-uuid --set=stage {manifest['stage_fs_uuid']}
    set gfxpayload=1024x768x32,1024x768,auto
    linux ($stage)/maintenance-kernel console=tty0 nomodeset quiet
    initrd ($stage)/maintenance-initrd.gz
}}
'''
    cfg=args.out/'grub.cfg';cfg.write_text(config)
    loader=args.out/'BOOTX64.EFI'
    command(['grub-mkstandalone','-O','x86_64-efi','--modules=part_gpt fat ext2 normal linux search search_fs_uuid regexp all_video','--locales=','--fonts=','-o',loader,'boot/grub/grub.cfg='+str(cfg)])
    esp=next(p for p in parts if p['name']=='EFI-SYSTEM');esp_file=args.out/'live-esp.img'
    with esp_file.open('xb') as f:f.truncate(esp['bytes'])
    command(['mkfs.fat','-F','32','-n','GBOSLIVE','-i',live_fat_id,esp_file],capture_output=True)
    command(['mmd','-i',esp_file,'::/EFI','::/EFI/BOOT','::/EFI/Googlebook'])
    for source,target in [(loader,'::/EFI/BOOT/BOOTX64.EFI'),(args.kernel,'::/EFI/Googlebook/kernel'),(args.native_initrd,'::/EFI/Googlebook/initrd'),(cfg,'::/EFI/Googlebook/grub.cfg')]:
        command(['mcopy','-o','-i',esp_file,source,target])
    command(['fsck.fat','-n',esp_file],capture_output=True)
    stage_file=args.out/'stage.ext4'
    with stage_file.open('xb') as f:f.truncate(stage_part['bytes'])
    command(['mkfs.ext4','-q','-F','-b','4096','-m','0','-U',manifest['stage_fs_uuid'],'-L','GBOS-INSTALL','-E','lazy_itable_init=0,lazy_journal_init=0','-d',stage,stage_file])
    command(['e2fsck','-fn',stage_file])
    with image.open('r+b') as f:
        f.truncate(new_size)
        for source,at in [(esp_file,esp['offset']),(stage_file,stage_offset)]:
            f.seek(at)
            with source.open('rb') as a:shutil.copyfileobj(a,f,4*MIB)
        for r in source_gpt['regions']:f.seek(r['offset']);f.write(base64.b64decode(r['data']))
        f.flush();os.fsync(f.fileno())
    with image.open('rb') as f:
        actual=gpt.inspect(lambda at,n:(f.seek(at),f.read(n))[1],new_size)
    if actual['regions']!=source_gpt['regions']:raise IOError('Release GPT readback failed')
    validation=dict(version=manifest['version'],physical_disk_access=False,boot_entries=['Live','Install'],
                    runtime_cleanup=clean,source_gpt_valid=True,fresh_live_userdata=True,
                    fresh_factory_install_assets=True,installer_stage_sha256=hash_file(stage_file),
                    public_manifest_contains_personal_identity=False,
                    native_compatibility=profile.get('native_compatibility','Experimental Intel Xe profile; ASUS S5602ZA baseline'))
    (args.out/'build.json').write_text(json.dumps(validation,indent=2)+'\n')
    print(json.dumps(validation,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base-image','upstream','kernel','native-initrd','maintenance-root','maintenance-kernel','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--profile',type=Path,default=ROOT/'profiles/intel-xe.json')
    p.add_argument('--audio-helper',type=Path,required=True,help='Minimal output helper from tools/build_audio.py')
    build(p.parse_args())
