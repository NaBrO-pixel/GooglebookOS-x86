import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from googlebook import gpt,planner,firmware
from googlebook.installer import Installer
from googlebook.progress import Progress


class FakeDisk:
    def __init__(self,path,size):
        self.path=Path(path);self.size=size;self.token='TEST1234';self.identity={};self.writes=[]
    def read(self,at,n):return bytes(n)
    def write(self,at,data):self.writes.append((at,len(data)))


class SafetyTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Runtime CPU preflight uses the Linux device module')
    def test_cpu_preflight_rejects_missing_required_instruction_sets(self):
        from googlebook.cli import check_cpu
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cpuinfo';path.write_text('flags : gfni sha_ni\nflags : gfni sha_ni\n')
            with self.assertRaises(ValueError):check_cpu(['gfni','sha_ni','avx_vnni'],path)
            path.write_text('flags : gfni sha_ni avx_vnni\nflags : gfni sha_ni avx_vnni\n')
            check_cpu(['gfni','sha_ni','avx_vnni'],path)
    def task(self):
        size=64*planner.GIB;target=FakeDisk('/tmp/target',size)
        plan=Mock(start=planner.MIB,end=63*planner.GIB,mode='alongside')
        return Installer('/tmp/stage',FakeDisk('/tmp/source',size),target,{},plan)

    def test_allocation_and_gpt_write_boundaries(self):
        task=self.task()
        for at,data,table in [(0,b'x'*512,False),(63*planner.GIB,b'x',False),(0,b'x'*512,True),(512,b'x'*1024,True)]:
            with self.assertRaises(ValueError):task.write(at,data,table)
        self.assertEqual(task.target.writes,[])
        task.write(planner.MIB,b'x'*512);self.assertEqual(task.target.writes,[(planner.MIB,512)])

    def test_confirmation_required_before_any_write(self):
        task=self.task()
        for confirmation in ('yes','INSTALL SSD 120','ERASE TEST1234','INSTALL WRONG'):
            with self.assertRaises(ValueError):task.install(confirmation)
        self.assertEqual(task.target.writes,[])

    def test_progress_does_not_invent_completion_during_wait(self):
        stream=io.StringIO()
        with Progress(1024**2,stream,interval=0.02) as bar:
            bar.changed-=8;time.sleep(0.08)
        output=stream.getvalue()
        self.assertGreater(output.count('[------------------------]'),2)
        self.assertIn('waiting for I/O',output);self.assertNotIn('100.0%',output)

    def test_firmware_preserves_existing_order_and_entries(self):
        existing='BootOrder: 0002,0001\nBoot0001* Windows Boot Manager HD(1,GPT,old)/File(bootmgfw.efi)\nBoot0002* USB HD(1,GPT,usb)\n'
        identifier='12345678-0000-4000-8000-000000000001'
        extra='Boot0003* Googlebook 12345678 HD(5,GPT,'+identifier+')/File(\\EFI\\BOOT\\BOOTX64.EFI)\n'
        replies=iter([existing,'',existing+extra,'',existing.replace('0002,0001','0002,0001,0003')+extra])
        calls=[]
        def runner(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0,next(replies),'')
        result=firmware.register(Mock(path='/dev/nvme0n1'),dict(number=5,partuuid=identifier),runner)
        self.assertEqual(result['boot_order'],['0002','0001','0003'])
        self.assertIn('--create-only',calls[1]);self.assertEqual(calls[3][-1],'0002,0001,0003')
        self.assertFalse(any('--delete-bootnum' in c for c in calls))


    def test_usb_disks_are_never_sata_targets(self):
        from googlebook import devices
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,bus in [('sda','pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0'),
                             ('sdb','pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0'),
                             ('nvme0n1','pci0000:00/0000:00:06.0/0000:01:00.0/nvme/nvme0')]:
                (root/'devices'/bus/'block'/name).mkdir(parents=True)
                (root/name).symlink_to(root/'devices'/bus/'block'/name)
            self.assertEqual(devices.transport(root/'sda'),'sata')
            self.assertEqual(devices.transport(root/'sdb'),'usb')
            self.assertEqual(devices.transport(root/'nvme0n1'),'nvme')


if __name__=='__main__':unittest.main()
