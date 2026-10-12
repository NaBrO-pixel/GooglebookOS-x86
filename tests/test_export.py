import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('exporter',ROOT/'tools/export_source.py')
exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.original=exporter.ROOT;exporter.ROOT=self.root
        for name in exporter.FILES:(self.root/name).write_text('Public source\n')
        (self.root/'docs').mkdir()

    def tearDown(self):exporter.ROOT=self.original;self.temp.cleanup()

    def test_ignored_lab_data_is_never_selected(self):
        (self.root/'work').mkdir();(self.root/'work/capture.json').write_text(json.dumps({'serial':'4'*24}))
        self.assertEqual(len(exporter.audit()),len(exporter.FILES))

    def test_tokens_and_captured_keys_are_rejected(self):
        p=self.root/'docs/notes.md';p.write_text('ghp_'+'Z'*40)
        with self.assertRaises(ValueError):exporter.audit()
        p.unlink();p=self.root/'docs/capture.json';p.write_text(json.dumps({'regions':[{'offset':512,'data':'private'}]}))
        with self.assertRaises(ValueError):exporter.audit()

    @unittest.skipUnless(sys.platform=='linux','Symlink fixtures require local permissions')
    def test_public_export_does_not_follow_outside_links(self):
        with tempfile.TemporaryDirectory() as outside:
            target=Path(outside)/'private.md';target.write_text('private')
            (self.root/'docs/private.md').symlink_to(target)
            with self.assertRaises(ValueError):exporter.audit()
