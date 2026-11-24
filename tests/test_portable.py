"""Cross-check source/bundle projections, identities and release immutability."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import releases as r
import visual_packages as vp
import visuals as v


def add_preview(root):
    original=root/'visuals/mosaic'
    work=root/'visuals/mosaic-preview'
    work.mkdir()
    for name in ('definition.json','CHANGELOG.md'):
        shutil.copyfile(original/name,work/name)
    old=v.read(original/'visual.json')
    meta={k:old[k] for k in ('publisher','tier','license','content_hash','provenance')}
    meta.update(schema_version=1,visual_id='vrch.mosaic-preview',title='蓝色 Mosaic',description='Blue variation',
                version='1.0.0', thumbnails=['预览.png'])
    r.save(work/'visual.json',meta)
    r.save(work/'look.json',{'schema_version':1,'params':{'amount':0.22}})
    r.save(work/'treatment.json',{'schema_version':1,'filters':{'contrast':1.15}})
    r.save(work/'controls.json',{'schema_version':1,'simple':['amount']})
    r.save(work/'media/index.json',{'schema_version':1,'items':[{'id':'grid','role':'source-image','path':'media/grid.png','slots':[0]}]})
    (work/'thumbnails').mkdir()
    Image.new('RGB',(4,4),'blue').save(work/'media/grid.png')
    shutil.copyfile(work/'media/grid.png',work/'thumbnails/预览.png')
    order=v.read(root/'catalog/order.json');order[meta['visual_id']]=len(order);r.save(root/'catalog/order.json',order)
    selection=v.read(root/'catalog/selection.json')
    selection['items'].append({'visual_id':meta['visual_id'],'collection':'demos','label':meta['title']})
    r.save(root/'catalog/selection.json',selection)
    return work


class PortableTests(unittest.TestCase):
    def test_canonical_fixture_and_invalid_file(self):
        data=vp.zip_bytes({'ascii':b'ABC','封面.txt':b'hello'})
        self.assertEqual(vp.sha(data),'5e2c727c929396cfd2808ebec699e0bb1cfe89dd3f001a05e74171322171b214')
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'fixture.zip';file.write_bytes(data)
            cmd=['node','--input-type=module','-e',
                 "import fs from 'node:fs';import {readCanonicalZip} from './tools/visual_package_reader.mjs';readCanonicalZip(fs.readFileSync(process.argv[1]))",str(file)]
            subprocess.run(cmd,cwd=ROOT,check=True)
            file.write_bytes(data[:-1])
            self.assertNotEqual(subprocess.run(cmd,cwd=ROOT,capture_output=True).returncode,0)

    def test_rich_work_and_immutable_download(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);root=base/'repo'
            shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('.git','.venv','__pycache__','node_modules','dist','.test-output'))
            work=add_preview(root)
            with self.assertRaisesRegex(ValueError,'consumer support'):
                v.check(root)
            bundle,release,record=r.build('1.1.0',root,base/'dist',r.empty_index(),'a'*40,preview=True)
            desc=vp.resource_map(release)[work.name]
            self.assertEqual(desc['program']['scene_id'],'vrch.mosaic')
            self.assertEqual(desc['id'],'vrch.mosaic-preview')
            self.assertEqual(desc['name'],'蓝色 Mosaic')
            source=vp.from_source(root,release,work.name,desc)
            self.assertIn('thumbnails/预览.png',source)
            self.assertEqual(set(desc['requires']['sections']),{'look','treatment','media'})
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(vp.from_archive(archive,release,work.name,desc),source)
                self.assertNotIn(desc['id'],json.loads(archive.read('catalog/order.json')))
                self.assertIn(desc['id'],json.loads(archive.read('catalog/works/order.json')))
            idx=r.update_index(r.empty_index(),release,record)
            changed=copy.deepcopy(release);changed['downloads'][desc['id']]['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'Immutable .visual'):
                r.update_index(idx,changed,record)
            r.save(work/'look.json',{'schema_version':1,'params':{'amount':10}})
            with self.assertRaises((ValueError,subprocess.CalledProcessError)):
                r.build('1.1.0',root,base/'bad',r.empty_index(),'a'*40,preview=True)
