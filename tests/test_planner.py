import copy
import random
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from googlebook import gpt, planner


def part(number,start,size,name='Windows'):
    return dict(number=number,name=name,type='ebd0a0a2-b9e5-4433-87c0-68b6b72699c7',
                partuuid=str(uuid.uuid4()),offset=start,bytes=size,attributes=0x8000000000000000)


def payloads():
    names=['EFI-SYSTEM','super','metadata','boot_a','userdata']+[f'native-{i}' for i in range(25)]
    return [dict(name=name,type=gpt.ESP_TYPE if name=='EFI-SYSTEM' else '0fc63daf-8483-4772-8e79-3d69d8477de4',
                 bytes=planner.MIB,source_offset=i*planner.MIB,attributes=0) for i,name in enumerate(names)]


class PlannerTests(unittest.TestCase):
    def test_varied_disk_sizes_and_free_regions(self):
        random.seed(402)
        for capacity in (128,256,512,1024,2048):
            for left in (16,40,64):
                size=capacity*planner.GIB;end=(capacity-4)*planner.GIB
                original=gpt.assemble(size,[part(3,planner.MIB,left*planner.GIB-planner.MIB),part(120,end,planner.GIB)])
                choices=planner.eligible_extents(original)
                if not choices:continue
                extent=max(choices,key=lambda x:x[1]-x[0]);plan=planner.plan('alongside',size,payloads(),original,extent)
                self.assertEqual(plan.after['disk_guid'],original['disk_guid'])
                before=gpt.raw(original)[2];after=gpt.raw(plan.after)[2]
                for existing in original['partitions']:
                    at=(existing['number']-1)*128;self.assertEqual(before[at:at+128],after[at:at+128])
                self.assertEqual(gpt.raw(original)[0],gpt.raw(plan.after)[0])
                self.assertTrue(all(extent[0]<=p['offset']<p['offset']+p['bytes']<=extent[1] for p in plan.partitions))
                self.assertGreaterEqual(plan.partitions[-1]['bytes'],16*planner.GIB)

    def test_custom_size_leaves_unused_tail(self):
        original=gpt.assemble(256*planner.GIB,[part(1,planner.MIB,64*planner.GIB-planner.MIB)])
        extent=planner.eligible_extents(original)[0]
        plan=planner.plan('alongside',256*planner.GIB,payloads(),original,extent,48)
        self.assertEqual(plan.end-plan.start,48*planner.GIB)
        self.assertLess(plan.end,extent[1])
        for bad in (-1,0,31,9999):
            with self.assertRaises(ValueError):planner.plan('alongside',256*planner.GIB,payloads(),original,extent,bad)

    def test_fragmented_small_gaps_do_not_add_up(self):
        parts=[part(i+1,(1+i*16)*planner.GIB,8*planner.GIB) for i in range(7)]
        original=gpt.assemble(120*planner.GIB,parts)
        self.assertEqual(planner.eligible_extents(original),[])

    def test_no_room_for_thirty_new_entries(self):
        original=gpt.assemble(512*planner.GIB,[part(i+1,(i+1)*planner.MIB,planner.MIB) for i in range(99)])
        with self.assertRaises(ValueError):planner.eligible_extents(original)

    def test_existing_googlebook_is_protected(self):
        original=gpt.assemble(256*planner.GIB,[part(1,planner.MIB,planner.MIB,'desktop_security_storage')])
        with self.assertRaises(ValueError):planner.plan('erase',256*planner.GIB,payloads(),original)

    def test_colliding_names_cannot_redirect_android_partition_lookup(self):
        original=gpt.assemble(256*planner.GIB,[part(1,planner.MIB,planner.MIB,'metadata')])
        with self.assertRaises(ValueError):planner.eligible_extents(original,payloads())
        # Explicit erasure of an unrelated colliding layout is still allowed.
        self.assertEqual(planner.plan('erase',256*planner.GIB,payloads(),original).mode,'erase')

    def test_erase_uses_disk_capacity_and_fresh_identifiers(self):
        for size in (32*planner.GIB+2*planner.MIB,64*planner.GIB,512*planner.GIB,2048*planner.GIB):
            plan=planner.plan('erase',size,payloads())
            self.assertEqual(plan.partitions[0]['number'],1)
            self.assertEqual(plan.partitions[0]['type'],gpt.ESP_TYPE)
            self.assertEqual(len(plan.after['partitions']),30)
            self.assertEqual(plan.end,planner.align_down((size//512-33)*512))
            other=planner.plan('erase',size,payloads())
            self.assertNotEqual(plan.after['disk_guid'],other.after['disk_guid'])
            self.assertNotEqual(plan.partitions[0]['partuuid'],other.partitions[0]['partuuid'])
        with self.assertRaises(ValueError):planner.plan('erase',16*planner.GIB,payloads())

    def test_inconsistent_gpt_and_hybrid_mbr_refused(self):
        original=gpt.assemble(64*planner.GIB,[]);raw=gpt.raw(original)
        mapping=dict(zip(gpt.regions(original['disk_bytes']),raw))
        for index in (1,3):
            broken=copy.deepcopy(mapping);key=gpt.regions(original['disk_bytes'])[index]
            data=bytearray(broken[key]);data[30]^=1;broken[key]=data
            with self.assertRaises(ValueError):gpt.inspect(lambda at,n:broken[(at,n)],original['disk_bytes'])
        key=(0,512);data=bytearray(mapping[key]);data[466]=7;mapping[key]=data
        with self.assertRaises(ValueError):gpt.inspect(lambda at,n:mapping[(at,n)],original['disk_bytes'])


if __name__=='__main__':unittest.main()
