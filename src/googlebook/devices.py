"""Whole-device discovery, stable identity and exclusive write admission."""
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import struct

from . import gpt


def transport(sys):
    """Classify a whole disk by its sysfs bus path; USB is never an install target."""
    if sys.name.startswith('nvme'):return 'nvme'
    path=str(sys.resolve())
    if '/usb' in path:return 'usb'
    if '/ata' in path and sys.name.startswith('sd'):return 'sata'
    return 'other'


class Disk:
    def __init__(self,path,file_test=False,transports=('nvme',)):
        self.path=Path(path).resolve();self.file_test=file_test;self.writable=False;self.transports=tuple(transports)
        mode=self.path.stat().st_mode
        if file_test:
            if not stat.S_ISREG(mode):raise ValueError('File tests cannot access block devices')
            self.identity=dict(path=str(self.path),size=self.path.stat().st_size,model='Disk file',serial='FILE TEST',sector=512)
        else:
            if not stat.S_ISBLK(mode):raise ValueError('Expected a whole block device')
            sys=Path('/sys/class/block')/self.path.name
            if (sys/'partition').exists():raise ValueError('Select a whole disk, not a partition')
            self.sys=sys
            serials=[p for p in [sys/'device/serial',*[parent/'serial' for parent in sys.resolve().parents]] if p.is_file()]
            self.identity=dict(path=str(self.path),size=int((sys/'size').read_text())*512,
                               model=(sys/'device/model').read_text().strip(),
                               serial=serials[0].read_text().strip() if serials else '',
                               sector=int((sys/'queue/logical_block_size').read_text()))
        if self.identity['sector']!=512:raise ValueError('4Kn disks are not supported by this release')
        self.size=self.identity['size'];self.fd=os.open(self.path,os.O_RDONLY)
        self.device_id=os.fstat(self.fd).st_rdev
        self.token=hashlib.sha256(repr(sorted(self.identity.items())).encode()).hexdigest()[:8].upper()

    def read(self,at,n):
        if not 0<=at<at+n<=self.size:raise ValueError('Read outside disk')
        data=os.pread(self.fd,n,at)
        if len(data)!=n:raise IOError('Short disk read')
        return data

    def assert_idle(self):
        if self.file_test:return
        kind=transport(self.sys)
        if kind not in self.transports:
            raise ValueError('This release installs only to whole '+'/'.join(t.upper() for t in self.transports)+' disks')
        if kind=='sata' and (self.sys/'removable').read_text().strip()!='0':raise ValueError('Removable disks are not install targets')
        mounted={line.split()[2] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
        swapped={str(Path(line.split()[0]).resolve()) for line in Path('/proc/swaps').read_text().splitlines()[1:]}
        for child in [self.sys,*self.sys.glob(self.path.name+('p*' if kind=='nvme' else '[0-9]*'))]:
            if (child/'dev').read_text().strip() in mounted or '/dev/'+child.name in swapped:
                raise ValueError('Target or one of its partitions is mounted/in swap')
            if list((child/'holders').iterdir()):raise ValueError('Target has active RAID/device-mapper holders')
        if (self.sys/'ro').read_text().strip()!='0':raise ValueError('Target is read-only')

    def snapshot(self):
        return gpt.inspect(self.read,self.size)

    def open_write(self,source,before):
        if self.path==source.path or (not self.file_test and self.device_id==source.device_id):raise ValueError('Cannot install over the source USB')
        self.assert_idle()
        if gpt.capture(self.read,self.size)!=before:raise ValueError('Disk layout changed after review')
        again=Disk(self.path,self.file_test,self.transports)
        try:
            if again.identity!=self.identity:raise ValueError('Selected disk identity changed')
        finally:again.close()
        fd=os.open(self.path,os.O_RDWR|os.O_EXCL)
        if os.fstat(fd).st_rdev!=self.device_id:os.close(fd);raise ValueError('Device changed when opened')
        os.close(self.fd);self.fd=fd;self.writable=True
        # Repeat mounted/holder and table checks on the exclusive descriptor.
        self.assert_idle()
        if gpt.capture(self.read,self.size)!=before:raise ValueError('Disk layout changed at write admission')

    def write(self,at,data):
        if not self.writable:raise ValueError('No exclusive write admission')
        if not 0<=at<at+len(data)<=self.size:raise ValueError('Write outside disk')
        if os.pwrite(self.fd,data,at)!=len(data):raise IOError('Short disk write')

    def flush(self):os.fsync(self.fd)
    def close(self):
        if getattr(self,'fd',None) is not None:os.close(self.fd);self.fd=None


def find_source(stage, manifest):
    matches=[]
    for item in Path('/sys/class/block').iterdir():
        if (item/'partition').exists() or item.name.startswith(('loop','ram','dm-','md')):continue
        disk=None
        try:
            disk=Disk('/dev/'+item.name)
            if disk.size<manifest['image_bytes']:continue
            header=disk.read(512,512)
            if header[:8]!=b'EFI PART':continue
            logical=(struct.unpack_from('<Q',header,32)[0]+1)*512
            if logical>disk.size:continue
            snapshot=gpt.inspect(disk.read,logical)
            if snapshot['disk_guid']==manifest['source_disk_guid'] and gpt.digest(gpt.raw(snapshot)[2])==manifest['source_table_sha256']:
                matches.append(disk);disk=None
        except (ValueError,OSError,UnicodeError):pass
        finally:
            if disk:disk.close()
    if len(matches)!=1:
        for disk in matches:disk.close()
        raise ValueError('Cannot identify one exact release USB; no target writes permitted')
    source=matches[0]
    stage_mm=os.stat(stage).st_dev
    stage_path=Path('/sys/dev/block')/f'{os.major(stage_mm)}:{os.minor(stage_mm)}'
    if not stage_path.exists() or not (stage_path/'partition').exists() or stage_path.resolve().parent!=source.sys.resolve():
        source.close();raise ValueError('Installer stage is not on the authenticated source disk')
    return source


def targets(source,transports=('nvme',)):
    found=[]
    patterns=[pattern for kind,pattern in (('nvme','nvme*n*'),('sata','sd*')) if kind in transports]
    for item in sorted(p for pattern in patterns for p in Path('/sys/class/block').glob(pattern)):
        if (item/'partition').exists():continue
        disk=None
        try:
            disk=Disk('/dev/'+item.name,transports=transports)
            if disk.device_id==source.device_id:continue
            disk.assert_idle();found.append(disk);disk=None
        except (ValueError,OSError):pass
        finally:
            if disk:disk.close()
    return found
