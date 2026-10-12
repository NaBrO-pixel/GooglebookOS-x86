import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_from_sources as build
import native_port as native


class SourceBuildTests(unittest.TestCase):
    def test_zip_traversal_and_external_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('../escape','/absolute','C:/escape','a\\escape'):
                with self.subTest(name=name),self.assertRaises(ValueError):
                    build.member_destination(root,name)
            archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z:
                entry=zipfile.ZipInfo('project/link');entry.create_system=3
                entry.external_attr=(0o120777<<16)
                z.writestr(entry,'../../escape')
            with self.assertRaises(ValueError):build.unzip(archive,root/'out')
            self.assertFalse((root.parent/'escape').exists())

    def test_mismatched_cache_and_donor_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);file=root/'input';file.write_bytes(b'wrong')
            with self.assertRaises(ValueError):build.verify(file,{'sha256':'0'*64})
            with self.assertRaises(ValueError):build.verify_donor(root,{'input':'0'*64})

    def test_cleanup_is_limited_to_generated_direct_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);safe=root/'native';safe.mkdir();(safe/'artifact').write_text('generated')
            with self.assertRaises(ValueError):build.owned_cleanup(root/'cache',root)
            with self.assertRaises(ValueError):build.owned_cleanup(root.parent/'native',root)
            build.owned_cleanup(safe,root);self.assertFalse(safe.exists())

    def test_properties_preserve_intel_graphics_and_use_synchronous_audio(self):
        before=b'ro.hardware.egl=angle\nro.hardware.vulkan=intel\naaudio.mmap_policy=2\naudio.offload.disable=0\n'
        after=native.patch_properties(before).decode()
        self.assertIn('ro.hardware.vulkan=intel\n',after)
        self.assertIn('ro.hardware.egl=angle\n',after)
        self.assertIn('aaudio.mmap_policy=1\n',after)
        self.assertNotIn('aaudio.mmap_policy=2',after)

    def test_power_uses_dynamic_topology_but_retains_thermal_controls(self):
        power={'Nodes':[{'Name':'FGCpuset'},{'Name':'XeIrqAffinity'},
            {'Name':'SchedLoadBalance','Values':['0','1'],'DefaultIndex':0},
            {'Name':'Thermal','Values':['safe']}],
            'Actions':[{'Node':'FGCpuset','Value':'0-7'},
                {'Node':'SchedLoadBalance','Value':'0'},{'Node':'Thermal','Value':'safe'}]}
        result=json.loads(native.patch_power(json.dumps(power).encode()))
        self.assertEqual([n['Name'] for n in result['Nodes']],['SchedLoadBalance','Thermal'])
        self.assertEqual(result['Nodes'][0]['Values'],['1'])
        self.assertEqual(result['Actions'][-1],{'Node':'Thermal','Value':'safe'})

    def test_changed_upstream_anchor_stops_before_running_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'helper.py';path.write_text('raise AssertionError("must not execute")\n')
            previous=sys.argv
            with self.assertRaises(ValueError):native.execute_upstream(path,[],[('missing','replacement')],{})
            self.assertIs(sys.argv,previous)

    def test_public_manifest_has_portable_pinned_paths(self):
        lock=json.loads((ROOT/'profiles/sources.json').read_text())
        for name in lock['donor_files']:
            self.assertNotIn('\\',name)
            self.assertNotIn('..',Path(name).parts)
        for entry in [*lock['archives'].values(),*lock['packages'].values(),*lock['modules'].values()]:
            self.assertTrue(entry['url'].startswith('https://'))
            self.assertRegex(entry['sha256'],r'^[0-9a-f]{64}$')

    def test_flasher_image_alias_does_not_duplicate_the_image(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'googlebook-x86.raw';image.write_bytes(b'test image')
            alias=build.image_alias(image)
            self.assertEqual(alias.read_bytes(),image.read_bytes())
            self.assertEqual(image.stat().st_ino,alias.stat().st_ino)


if __name__=='__main__':unittest.main()
