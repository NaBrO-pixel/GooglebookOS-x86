"""Offline installation: authenticated payloads, bounded writes, GPT last."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from . import gpt, firmware
from .progress import Progress

MIB=1024**2
CHUNK=4*MIB


def durable_json(path,data):
    path=Path(path)
    with path.open('x') as f:
        json.dump(data,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def check_manifest(stage,manifest):
    if manifest.get('format')!=1:raise ValueError('Unsupported release manifest')
    if len(manifest['payloads'])!=30:raise ValueError('Incomplete release')
    for name,entry in manifest['assets'].items():
        path=stage/name
        if path.parent.resolve()!=stage.resolve() or not path.is_file():raise ValueError('Invalid release asset path')
        with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=entry['sha256'] or path.stat().st_size!=entry['bytes']:raise ValueError('Release asset authentication failed: '+name)
    for p in manifest['payloads']:
        if p['name'] in ('userdata','EFI-SYSTEM'):continue
        position=0
        for c in p['chunks']:
            if c['at']!=position or not 0<c['bytes']<=CHUNK:raise ValueError('Invalid payload chunk map')
            position+=c['bytes']
        if position!=p['bytes']:raise ValueError('Incomplete payload chunk map')
        if p.get('asset') and p['asset'] not in manifest['assets']:raise ValueError('Missing packaged factory asset')


def make_esp(stage,plan,manifest,destination):
    with destination.open('xb') as f:f.truncate(256*MIB)
    subprocess.run(['mkfs.fat','-F','32','-n','GOOGLEBOOK','-i',plan.fat_id,str(destination)],check=True,capture_output=True)
    subprocess.run(['mmd','-i',str(destination),'::/EFI','::/EFI/BOOT','::/EFI/Googlebook'],check=True)
    boot=next(p for p in plan.partitions if p['name']=='boot_a')
    fat_uuid=plan.fat_id[:4]+'-'+plan.fat_id[4:]
    args=(stage/'kernel-args.txt').read_text().strip().replace('@BOOT_UUID@',boot['partuuid'])
    config=f'''set timeout=0
search --fs-uuid --set=root {fat_uuid}
menuentry 'Googlebook OS' {{
    linux /EFI/Googlebook/kernel {args} androidboot.gbos_storage=nvme
    initrd /EFI/Googlebook/initrd
}}
'''
    cfg=destination.with_suffix('.cfg');cfg.write_text(config)
    loader=destination.with_suffix('.efi')
    subprocess.run(['grub-mkstandalone','-O','x86_64-efi',
                    '--modules=part_gpt fat normal linux search search_fs_uuid all_video',
                    '--locales=','--fonts=','-o',str(loader),'boot/grub/grub.cfg='+str(cfg)],check=True)
    for source,target in [(loader,'::/EFI/BOOT/BOOTX64.EFI'),
                           (stage/'native-kernel','::/EFI/Googlebook/kernel'),
                           (stage/'native-initrd','::/EFI/Googlebook/initrd'),
                           (cfg,'::/EFI/Googlebook/grub.cfg')]:
        subprocess.run(['mcopy','-o','-i',str(destination),str(source),target],check=True)
    subprocess.run(['fsck.fat','-n',str(destination)],check=True,capture_output=True)
    return config


class Installer:
    def __init__(self,stage,source,target,manifest,plan):
        self.stage=Path(stage);self.source=source;self.target=target
        self.manifest=manifest;self.plan=plan;self.audit=[]
        self.before=gpt.capture(target.read,target.size);self.backup=None

    def write(self,at,data,table=False):
        if table:
            if (at,len(data)) not in gpt.regions(self.target.size):raise ValueError('Unauthorized GPT write')
            if self.plan.mode=='alongside' and at==0:raise ValueError('Alongside does not change the existing MBR')
        elif not self.plan.start<=at<at+len(data)<=self.plan.end:
            raise ValueError('Payload write outside the reviewed allocation')
        self.target.write(at,data);self.audit.append(dict(offset=at,bytes=len(data),gpt=table))

    def copy_partition(self,p,progress,generated=None):
        expected=hashlib.sha256();start=progress.done
        stream=(generated.open('rb') if generated else gzip.open(self.stage/p['asset'],'rb') if p.get('asset') else None)
        chunks=p.get('chunks') if generated is None else [dict(at=at,bytes=min(CHUNK,p['bytes']-at)) for at in range(0,p['bytes'],CHUNK)]
        flushed=0
        try:
            for c in chunks:
                progress.advance(0,'Reading '+p['name'])
                if stream:data=stream.read(c['bytes'])
                elif c['zero']:data=bytes(c['bytes'])
                else:data=self.source.read(p['source_offset']+c['at'],c['bytes'])
                if len(data)!=c['bytes'] or (generated is None and gpt.digest(data)!=c['sha256']):
                    raise ValueError('Source data changed/corrupted: '+p['name'])
                expected.update(data);self.write(p['offset']+c['at'],data);flushed+=len(data)
                if flushed>=64*MIB:self.target.flush();flushed=0
                progress.advance(len(data),'Writing '+p['name'])
            if stream and stream.read(1):raise ValueError('Oversized packaged partition')
        finally:
            if stream:stream.close()
        self.target.flush()
        if hasattr(os,'posix_fadvise'):os.posix_fadvise(self.target.fd,p['offset'],p['bytes'],os.POSIX_FADV_DONTNEED)
        wanted=expected.hexdigest()
        if generated is None and wanted!=p['payload_sha256']:raise ValueError('Unexpected complete source hash')
        actual=hashlib.sha256()
        for at in range(0,p['bytes'],CHUNK):
            n=min(CHUNK,p['bytes']-at);actual.update(self.target.read(p['offset']+at,n));progress.advance(n,'Verifying '+p['name'])
        if actual.hexdigest()!=wanted:raise IOError('SSD readback failed: '+p['name'])
        if progress.done-start!=2*p['bytes']:raise AssertionError('Incorrect payload progress')

    def commit(self):
        # Backup table/header first, primary last. Existing MBR stays byte-exact
        # in alongside mode. Restoring GPT after erase cannot restore old files.
        entries=self.plan.after['regions'];indices=[3,4,2,1]+([0] if self.plan.mode=='erase' else [])
        try:
            for i in indices:
                r=entries[i];self.write(r['offset'],__import__('base64').b64decode(r['data']),True);self.target.flush()
            if self.target.snapshot()['regions']!=entries:raise IOError('GPT commit verification failed')
        except BaseException:
            for i in indices:
                r=self.before[i];self.write(r['offset'],__import__('base64').b64decode(r['data']),True);self.target.flush()
            if gpt.capture(self.target.read,self.target.size)!=self.before:raise IOError('GPT rollback failed; retain backup files')
            raise

    def install(self,confirmation,register_boot=True):
        verb='INSTALL' if self.plan.mode=='alongside' else 'ERASE'
        if confirmation!=verb+' '+self.target.token:raise ValueError('Disk-specific installation confirmation required')
        check_manifest(self.stage,self.manifest)
        if self.plan.mode=='alongside' and self.target.snapshot()['regions']!=self.plan.before['regions']:
            raise ValueError('GPT differs from the reviewed alongside plan')
        self.target.assert_idle()
        if self.target.path==self.source.path:raise ValueError('Cannot erase the source USB')
        self.backup=self.stage/('install-'+str(time.time_ns()));self.backup.mkdir()
        durable_json(self.backup/'before.json',dict(identity=self.target.identity,regions=self.before))
        durable_json(self.backup/'plan.json',dict(mode=self.plan.mode,start=self.plan.start,end=self.plan.end,after=self.plan.after))
        # Bounded originals are useful even for a non-GPT erase target.
        for label,at in [('first',0),('last',self.target.size-MIB)]:
            with (self.backup/(label+'.bin')).open('xb') as f:f.write(self.target.read(at,MIB));f.flush();os.fsync(f.fileno())
        self.target.open_write(self.source,self.before)
        try:
            with tempfile.TemporaryDirectory(prefix='googlebook-install-') as tmp:
                esp_file=Path(tmp)/'esp.img';make_esp(self.stage,self.plan,self.manifest,esp_file)
                payloads=[p for p in self.plan.partitions if p['name']!='userdata']
                total=2*MIB+sum(2*p['bytes'] for p in payloads)
                with Progress(total) as progress:
                    userdata=next(p for p in self.plan.partitions if p['name']=='userdata')
                    for at in (userdata['offset'],userdata['offset']+userdata['bytes']-MIB):
                        self.write(at,bytes(MIB));self.target.flush()
                        if self.target.read(at,MIB)!=bytes(MIB):raise IOError('Fresh-data header verification failed')
                        progress.advance(MIB,'Preparing fresh data headers')
                    for p in payloads:self.copy_partition(p,progress,esp_file if p['name']=='EFI-SYSTEM' else None)
                print('Committing verified partition tables...',flush=True);self.commit()
            # Tell the kernel about the verified table without modifying disk.
            if not self.target.file_test:
                import fcntl
                fcntl.ioctl(self.target.fd,0x125f)  # BLKRRPART
            result=dict(success=True,mode=self.plan.mode,write_ranges=self.audit,
                        firmware_registered=False,automatic_shutdown=False)
            if register_boot:
                if firmware.available():
                    esp=next(p for p in self.plan.partitions if p['name']=='EFI-SYSTEM')
                    result['firmware']=firmware.register(self.target,esp);result['firmware_registered']=True
                elif self.plan.mode=='alongside':raise IOError('OS is installed but firmware registration is unavailable; keep the USB and backups')
            result['automatic_shutdown']=True;durable_json(self.backup/'result.json',result)
            print('INSTALLATION COMPLETE. Writes will be flushed, then automatic shutdown follows.',flush=True)
            return result
        except BaseException as exc:
            durable_json(self.backup/'failed.json',dict(error=str(exc),write_ranges=self.audit,
                         gpt_committed=gpt.capture(self.target.read,self.target.size)==self.plan.after['regions']))
            raise
