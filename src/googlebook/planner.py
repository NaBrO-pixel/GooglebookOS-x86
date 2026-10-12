"""Partition planning is pure and does not open devices or resize filesystems."""
from dataclasses import dataclass
import uuid

from . import gpt

MIB=1024**2
GIB=1024**3
MIN_ALLOCATION=32*GIB
MIN_USERDATA=16*GIB
PARTITION_COUNT=30


def align_up(value):return (value+MIB-1)//MIB*MIB
def align_down(value):return value//MIB*MIB


def free_extents(snapshot):
    cursor=34*512;end=(snapshot['disk_bytes']//512-33)*512;result=[]
    for p in sorted(snapshot['partitions'],key=lambda p:p['offset']):
        left,right=align_up(cursor),align_down(p['offset'])
        if right>left:result.append((left,right))
        cursor=p['offset']+p['bytes']
    left,right=align_up(cursor),align_down(end)
    if right>left:result.append((left,right))
    return result


def eligible_extents(snapshot,payloads=None):
    if payloads:
        names={p['name'].casefold() for p in payloads}
        if any(p['name'].casefold() in names for p in snapshot['partitions']):
            raise ValueError('Existing partition names collide with Googlebook; alongside is refused')
    if 128-len(snapshot['partitions'])<PARTITION_COUNT:
        raise ValueError('Alongside needs 30 unused GPT slots')
    return [(a,b) for a,b in free_extents(snapshot) if b-a>=MIN_ALLOCATION]


@dataclass
class Plan:
    mode: str
    start: int
    end: int
    partitions: list
    before: dict | None
    after: dict
    fat_id: str


def plan(mode, disk_bytes, payloads, snapshot=None, extent=None, requested_gib=None):
    if mode not in ('alongside','erase'):raise ValueError('Unknown installation mode')
    if len(payloads)!=PARTITION_COUNT or len({p['name'] for p in payloads})!=PARTITION_COUNT:
        raise ValueError('Expected all 30 unique Googlebook partitions')
    if not {'userdata','metadata','EFI-SYSTEM','boot_a','super'}<= {p['name'] for p in payloads}:
        raise ValueError('Missing required partitions')
    if snapshot and any(p['name'] in ('GBOS-INSTALL','desktop_security_storage') for p in snapshot['partitions']):
        raise ValueError('This disk already contains Googlebook; automatic reinstall is refused')
    if mode=='alongside':
        if snapshot is None or snapshot['disk_bytes']!=disk_bytes:raise ValueError('Alongside requires matching valid GPT')
        choices=eligible_extents(snapshot,payloads)
        if extent not in choices:raise ValueError('Choose one qualifying contiguous unallocated region')
        start,end=extent;used={p['number'] for p in snapshot['partitions']}
    else:
        start,end=MIB,align_down((disk_bytes//512-33)*512);used=set()
    if requested_gib is not None:
        if mode=='erase':raise ValueError('Erase mode uses the whole available disk')
        if not isinstance(requested_gib,int) or requested_gib<32 or start+requested_gib*GIB>end:
            raise ValueError('Requested allocation does not fit the selected free region')
        end=start+requested_gib*GIB
    if end-start<MIN_ALLOCATION:raise ValueError('At least 32 GiB of contiguous space is required')
    numbers=iter(n for n in range(1,129) if n not in used)
    ordered=sorted((p for p in payloads if p['name']!='userdata'),key=lambda p:(p['name']!='EFI-SYSTEM',p.get('source_offset',0)))
    cursor=start;parts=[]
    for item in ordered:
        cursor=align_up(cursor)
        size=256*MIB if item['name']=='EFI-SYSTEM' else item['bytes']
        if not isinstance(size,int) or size<=0 or size%512:raise ValueError('Invalid payload length')
        parts.append(dict(item,number=next(numbers),offset=cursor,bytes=size,partuuid=str(uuid.uuid4())))
        cursor+=size
    cursor=align_up(cursor);item=next(p for p in payloads if p['name']=='userdata')
    if end-cursor<MIN_USERDATA:raise ValueError('Allocation leaves less than 16 GiB for apps/data')
    parts.append(dict(item,number=next(numbers),offset=cursor,bytes=end-cursor,partuuid=str(uuid.uuid4())))
    original=snapshot if mode=='alongside' else None
    after=gpt.assemble(disk_bytes,parts,original)
    return Plan(mode,start,end,parts,snapshot,after,uuid.uuid4().hex[:8].upper())
