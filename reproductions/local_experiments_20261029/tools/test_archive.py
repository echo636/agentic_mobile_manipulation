import gzip
import json
from pathlib import Path
import tempfile
import unittest
import archive

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'archive'
        self.root.mkdir()
    def fixture(self, files):
        mapping = {}
        for name, data in files.items():
            sha = archive.digest(data); p = archive.object_file(self.root, sha)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(data, mtime=0)); mapping[name] = sha
        sid = archive.digest(json.dumps(mapping, sort_keys=True, separators=(',', ':')).encode())
        p = self.root/'snapshots'/(sid+'.json'); p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps({'files':mapping, 'executable_files':['run.sh']}))
        (self.root/'catalog.json').write_text(json.dumps({'snapshots':[{'snapshot_id':sid}], 'environment_version_records':[]}))
        return sid, mapping
    def test_restores_exact_bytes_and_exec_mode(self):
        files={'src/a.py':b'print("hello")\n', 'run.sh':b'#!/bin/sh\nexit 0\n'}
        sid, _ = self.fixture(files); out = Path(self.tmp.name)/'restored'
        archive.restore(self.root, sid, out)
        self.assertEqual({n:(out/n).read_bytes() for n in files}, files)
        self.assertTrue((out/'run.sh').stat().st_mode & 0o100)
        self.assertTrue(archive.verify(self.root)['passed'])
    def test_never_overwrites_existing_directory(self):
        sid,_=self.fixture({'a.py':b'a'}); out=Path(self.tmp.name)/'old';out.mkdir();(out/'keep').write_text('original')
        with self.assertRaises(FileExistsError):archive.restore(self.root,sid,out)
        self.assertEqual((out/'keep').read_text(),'original')
    def test_corrupt_object_fails_before_creating_output(self):
        sid,m=self.fixture({'a.py':b'original'})
        archive.object_file(self.root,m['a.py']).write_bytes(gzip.compress(b'changed'))
        out=Path(self.tmp.name)/'out'
        with self.assertRaises(ValueError):archive.restore(self.root,sid,out)
        self.assertFalse(out.exists())
    def test_traversal_even_with_valid_manifest_hash_is_rejected(self):
        sid,_=self.fixture({'../outside.py':b'x'});out=Path(self.tmp.name)/'out'
        with self.assertRaises(ValueError):archive.restore(self.root,sid,out)
        self.assertFalse(out.exists());self.assertFalse((out.parent/'outside.py').exists())
    def test_manifest_tamper_is_rejected(self):
        sid,_=self.fixture({'a.py':b'a'});p=self.root/'snapshots'/(sid+'.json');d=json.loads(p.read_text());d['files']['b.py']=d['files'].pop('a.py');p.write_text(json.dumps(d))
        with self.assertRaises(ValueError):archive.verify(self.root)
    def test_publication_manifest_detects_changed_environment_metadata(self):
        self.fixture({'a.py':b'a'})
        name='environment/recorded.json';p=self.root/name;p.parent.mkdir();p.write_bytes(b'{"torch":"recorded"}')
        (self.root/'PACKAGE_MANIFEST.json').write_text(json.dumps({'files':{name:archive.digest(p.read_bytes())}}))
        self.assertTrue(archive.verify(self.root)['passed'])
        p.write_bytes(b'{"torch":"different"}')
        with self.assertRaises(ValueError):archive.verify(self.root)
    def test_snapshot_id_cannot_be_a_path(self):
        with self.assertRaises(ValueError):archive.read_snapshot(self.root,'../../outside')

if __name__=='__main__':unittest.main()
