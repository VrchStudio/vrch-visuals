import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import releases as r
import visuals as v


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.output=Path(cls.temp.name)
        cls.package,cls.release,cls.record=r.build('1.0.0',output=cls.output/'first',index=r.empty_index(),commit='a'*40)
        cls.index=r.update_index(r.empty_index(),cls.release,cls.record)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_deterministic_archive_and_metadata(self):
        package,release,record=r.build('1.0.0',output=self.output/'second',index=r.empty_index(),commit='a'*40)
        self.assertEqual(package.read_bytes(),self.package.read_bytes())
        self.assertEqual(release,self.release)
        with zipfile.ZipFile(package) as z:
            self.assertEqual(z.namelist(),sorted(z.namelist()))
            self.assertFalse(any(n.startswith(('engine/','tools/','index/','tests/')) for n in z.namelist()))
            for info in z.infolist():
                self.assertEqual(info.date_time,(1980,1,1,0,0,0))
                self.assertEqual(info.compress_type,zipfile.ZIP_STORED)
                self.assertEqual((info.external_attr>>16)&0o777,0o644)
        self.assertEqual(len(release['resources']),57)

    def test_parameter_compatibility_version_policy(self):
        before={'parameters':[{'key':'speed','type':'float','default':1}]}
        after=copy.deepcopy(before);after['parameters'][0]['default']=2
        self.assertEqual(r.required_bump(before,after),1)
        after['parameters'][0]['key']='new_speed'
        self.assertEqual(r.required_bump(before,after),0)
        self.assertEqual(r.required_bump(before,before),2)

    def test_engine_sync_uses_committed_whitelist(self):
        import shutil
        pages=self.output/'engine-source';scripts=pages/'static/scripts';scripts.mkdir(parents=True)
        destination=self.output/'engine-target';shutil.copytree(v.ROOT/'engine',destination/'engine')
        for name in v.ENGINE_FILES:shutil.copyfile(v.ROOT/'engine'/name,scripts/name)
        r.command('git','init','-q',cwd=pages)
        r.command('git','add','static',cwd=pages)
        r.command('git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture',cwd=pages)
        (scripts/'vrch_pages_scene_contract.js').write_text('uncommitted text')
        (scripts/'private.py').write_text('not a public engine file')
        result=r.engine_sync(pages,'HEAD',True,destination)
        self.assertEqual(result['changed_files'],[])
        self.assertEqual((destination/'engine/vrch_pages_scene_contract.js').read_bytes(),(v.ROOT/'engine/vrch_pages_scene_contract.js').read_bytes())
        self.assertFalse((destination/'engine/private.py').exists())

    def test_single_resource_is_complete(self):
        destination=self.output/'one'
        result=r.extract(self.package,'pulse-ridges',destination)
        self.assertEqual(result['id'],'user.vp-pulse-ridges')
        self.assertEqual(len(list((destination/'visuals').iterdir())),1)
        descriptor=json.loads((destination/'visuals/pulse-ridges/resource.json').read_text())
        for item in descriptor['files']:
            self.assertEqual(v.sha(destination/item['path']),item['sha256'])
        self.assertTrue((destination/'LICENSE').exists())
        self.assertFalse((destination/'catalog').exists())

    def test_retry_preserves_first_source_and_descriptors(self):
        _,release,record=r.build('1.0.0',output=self.output/'retry',index=self.index,commit='b'*40)
        self.assertEqual(release,self.release)
        self.assertEqual(record,self.record)
        self.assertEqual(r.update_index(self.index,release,record),self.index)

    def test_immutable_versions_and_history(self):
        release=copy.deepcopy(self.release);release['resources'][0]['fingerprint']='0'*64
        with self.assertRaisesRegex(ValueError,'Immutable visual'):
            r.update_index(self.index,release,self.record)
        record=copy.deepcopy(self.record);record['archive']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'Immutable bundle'):
            r.update_index(self.index,self.release,record)
        index=copy.deepcopy(self.index);index['versions']['1.1.0']={**self.record,'source_commit':'b'*40};index['latest']='1.1.0'
        self.assertEqual(r.update_index(index,self.release,self.record)['latest'],'1.1.0')
        self.assertEqual(len(index['visuals']),57)

    def test_assets_reused_missing_published_refused(self):
        asset={'digest':'sha256:'+v.sha(self.package),'size':self.package.stat().st_size}
        self.assertEqual(r.asset_action(asset,self.package,False),'reuse')
        self.assertEqual(r.asset_action(None,self.package,True),'upload')
        with self.assertRaisesRegex(ValueError,'missing'):
            r.asset_action(None,self.package,False)
        with self.assertRaisesRegex(ValueError,'differs'):
            r.asset_action({**asset,'digest':'sha256:bad'},self.package,True)

    def test_metadata_and_thumbnail_version_guard(self):
        # The guard hashes all resource bytes and metadata, not only the definition hash.
        index=copy.deepcopy(self.index)
        identifier=next(iter(index['visuals']))
        version=next(iter(index['visuals'][identifier]['versions']))
        index['visuals'][identifier]['versions'][version]['fingerprint']='0'*64
        with self.assertRaisesRegex(ValueError,'without a version bump'):
            r.descriptors(v.ROOT,index,'b'*40)


if __name__=='__main__':unittest.main()
