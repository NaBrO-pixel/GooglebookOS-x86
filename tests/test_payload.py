import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from googlebook import gpt
from googlebook.installer import Installer
from googlebook.progress import Progress


@unittest.skipUnless(sys.platform=='linux','Exclusive disk-file tests require Linux')
class PayloadTests(unittest.TestCase):
    def disks(self,folder,size):
        from googlebook.devices import Disk
        paths=[folder/'target.raw',folder/'source.raw']
        for path in paths:
            with path.open('xb') as f:f.truncate(size)
        target,source=(Disk(path,file_test=True) for path in paths)
        target.open_write(source,gpt.capture(target.read,size))
        return target,source

    def test_old_data_under_zero_chunks_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            target,source=self.disks(Path(tmp),4*1024**2)
            try:
                data=b'A'*4096+bytes(4096)
                with source.path.open('r+b') as f:f.seek(1024**2);f.write(data)
                target.write(2*1024**2,b'D'*8192)
                p=dict(name='test',offset=2*1024**2,source_offset=1024**2,bytes=8192,
                       chunks=[dict(at=0,bytes=4096,zero=False,sha256=gpt.digest(data[:4096])),dict(at=4096,bytes=4096,zero=True,sha256=gpt.digest(data[4096:]))],
                       payload_sha256=gpt.digest(data))
                plan=Mock(start=1024**2,end=3*1024**2,mode='alongside')
                task=Installer(Path(tmp),source,target,{},plan)
                with Progress(16384) as progress:task.copy_partition(p,progress)
                self.assertEqual(target.read(p['offset'],p['bytes']),data)
                self.assertEqual(sum(r['bytes'] for r in task.audit),8192)
            finally:target.close();source.close()

    def test_corrupt_source_never_commits_a_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            target,source=self.disks(Path(tmp),4*1024**2)
            try:
                p=dict(name='test',offset=2*1024**2,source_offset=1024**2,bytes=4096,
                       chunks=[dict(at=0,bytes=4096,zero=False,sha256=gpt.digest(b'A'*4096))],payload_sha256=gpt.digest(b'A'*4096))
                task=Installer(Path(tmp),source,target,{},Mock(start=1024**2,end=3*1024**2,mode='alongside'))
                with Progress(8192) as progress:
                    with self.assertRaises(ValueError):task.copy_partition(p,progress)
                self.assertEqual(task.audit,[])
            finally:target.close();source.close()

    def test_caught_commit_failure_restores_original_gpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            target,source=self.disks(Path(tmp),64*1024**2)
            try:
                before=gpt.assemble(target.size,[])
                for r in before['regions']:target.write(r['offset'],base64.b64decode(r['data']))
                p=dict(number=50,name='New',type=gpt.ESP_TYPE,partuuid='12345678-1234-4000-8000-000000000001',offset=1024**2,bytes=1024**2,attributes=0)
                after=gpt.assemble(target.size,[p],before)
                task=Installer(Path(tmp),source,target,{},Mock(mode='alongside',after=after,start=1024**2,end=63*1024**2))
                real=target.write;injected=False
                def fail_once(at,data):
                    nonlocal injected
                    if at==512 and not injected:injected=True;raise IOError('Injected primary GPT failure')
                    return real(at,data)
                target.write=fail_once
                with self.assertRaises(IOError):task.commit()
                self.assertEqual(target.snapshot()['regions'],before['regions'])
            finally:target.close();source.close()
