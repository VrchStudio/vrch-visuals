import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('visuals', ROOT / 'tools/visuals.py')
visuals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visuals)


class VisualsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'library'
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__'))

    def tearDown(self):
        self.tmp.cleanup()

    def change(self, relative, fn):
        path = self.root / relative
        data = json.loads(path.read_text())
        fn(data)
        path.write_text(json.dumps(data))

    def test_baseline(self):
        self.assertEqual(visuals.check(self.root), {'definitions':57,'thumbnails':57,'order':63,'selected':35,'choreography':20,'export_files':117})
        baseline = visuals.read(self.root / 'tests/migration-baseline.json')['visuals']
        for path in (self.root / 'presets/definitions').glob('*.json'):
            sid = visuals.read(path)['scene_id']
            self.assertEqual(visuals.sha(path), baseline[sid]['sha256'])

    def test_hash_change_rejected(self):
        self.change('presets/definitions/bitmap.definition.json', lambda d: d.update(title='Changed'))
        with self.assertRaisesRegex(ValueError, 'JCS hash'):
            visuals.check(self.root)

    def test_disabled_and_unknown_signal_rejected(self):
        path = self.root / 'presets/definitions/bitmap.definition.json'
        original = path.read_text()
        for signal in ['audio.spectrum', 'audio.not-real']:
            path.write_text(original)
            self.change(str(path.relative_to(self.root)), lambda d: d['signals'].append({'id':signal,'type':'texture','optional':True,'default':0}))
            with self.assertRaisesRegex(ValueError, 'disabled or unknown signal'):
                visuals.check(self.root)

    def test_invalid_analysis_rejected(self):
        self.change('presets/definitions/bitmap.definition.json', lambda d: d.update(analysis_requirements=[{'kind':'unknown','source_input':0,'topology':'vrch.pose-landmarks@1','history_ms':0}]))
        with self.assertRaises(ValueError):
            visuals.check(self.root)

    def test_order_missing_unknown_and_duplicate(self):
        path = self.root / 'catalog/order.json'
        original = path.read_text()
        for mutate in [lambda d:d.pop(next(iter(d))), lambda d:d.update(unknown=63), lambda d:d.update({next(iter(d)):1})]:
            path.write_text(original)
            self.change('catalog/order.json', mutate)
            with self.assertRaisesRegex(ValueError, 'Order'):
                visuals.check(self.root)

    def test_invalid_collection_and_choreography(self):
        self.change('catalog/selection.json', lambda d: d['items'][0].update(collection='unknown'))
        with self.assertRaisesRegex(ValueError, 'collection'):
            visuals.check(self.root)
        shutil.copyfile(ROOT / 'catalog/selection.json', self.root / 'catalog/selection.json')
        self.change('catalog/audio-mapping-v2.json', lambda d: d['entries'][0]['bindings'][0].update(depth=0.123))
        with self.assertRaisesRegex(ValueError, 'choreography bindings'):
            visuals.check(self.root)

    def test_provenance_pin_survives_matching_metadata_edit(self):
        # ai-derived -> human-edit is namespace-valid, but migration trust provenance is pinned.
        self.change('presets/definitions/pulse-ridges.definition.json', lambda d:d['provenance'].update(kind='human-edit'))
        p = self.root / 'presets/definitions/pulse-ridges.definition.json'
        import subprocess
        out = subprocess.check_output(['node', str(self.root / 'tools/check_engine.cjs')], input=json.dumps([json.loads(p.read_text())]), text=True)
        digest = json.loads(out)['results'][0]['content_hash']
        self.change('visuals.json', lambda d:d['visuals']['pulse-ridges'].update(provenance={'kind':'human-edit'},content_hash=digest))
        with self.assertRaisesRegex(ValueError, 'pinned provenance'):
            visuals.check(self.root)

    def test_metadata_and_thumbnail_validation(self):
        self.change('visuals.json', lambda d:d['visuals']['bitmap'].update(license='unknown'))
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError):
            visuals.check(self.root)
        shutil.copyfile(ROOT / 'visuals.json', self.root / 'visuals.json')
        (self.root / 'thumbnails/extra.png').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'Unreferenced'):
            visuals.check(self.root)

    def test_engine_digest_and_whitelist(self):
        (self.root / 'engine/private.py').write_text('# must not be copied')
        with self.assertRaisesRegex(ValueError, 'Unexpected engine'):
            visuals.check(self.root)
        (self.root / 'engine/private.py').unlink()
        with (self.root / 'engine/vrch_pages_scene_contract.js').open('a') as stream:
            stream.write('\n// modified\n')
        with self.assertRaisesRegex(ValueError, 'Engine digest'):
            visuals.check(self.root)

    def test_export_exact_bytes_preserves_unmanaged_and_retries(self):
        destination = Path(self.tmp.name) / 'pages'
        fixture = destination / visuals.PAGES_PRESETS / 'definitions/passthrough.definition.json'
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(b'Pages-owned')
        for _ in range(2):
            visuals.export(destination, self.root)
            for source, target in visuals.owned_files(self.root).items():
                self.assertEqual((self.root/source).read_bytes(), (destination/target).read_bytes())
                self.assertEqual((destination/target).stat().st_mode & 0o777, 0o644)
            self.assertEqual(fixture.read_bytes(), b'Pages-owned')
        before = {p.relative_to(destination):p.read_bytes() for p in destination.rglob('*') if p.is_file()}
        self.change('presets/definitions/bitmap.definition.json', lambda d:d.update(title='Bad hash'))
        with self.assertRaises(ValueError):
            visuals.export(destination, self.root)
        self.assertEqual(before, {p.relative_to(destination):p.read_bytes() for p in destination.rglob('*') if p.is_file()})

    def test_duplicate_json_and_export_path(self):
        (self.root/'catalog/order.json').write_text('{"same":0,"same":1}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            visuals.check(self.root)


if __name__ == '__main__':
    unittest.main()
