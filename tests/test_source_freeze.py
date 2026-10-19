"""Archive provenance must identify the executed bytes without a .git directory."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.custom_readiness import _policy_source_identity
from manipulation_agent.records import release_source_digest, source_version


class FrozenSourceTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory) / 'archive'
        (self.root / 'src/manipulation_agent').mkdir(parents=True)
        (self.root / 'scripts').mkdir()
        self.module = self.root / 'src/manipulation_agent/example.py'
        self.script = self.root / 'scripts/validate_custom_task_physics.py'
        self.module.write_text('VALUE = 1\n')
        self.script.write_text('print("reference fixture")\n')
        self.manifest = {'source_commit': 'a' * 40,
                         'source_sha256': release_source_digest(self.root)}
        (self.root / 'source_freeze.json').write_text(json.dumps(self.manifest))

    def test_archive_uses_verified_identity_without_calling_git(self):
        with patch('manipulation_agent.records.subprocess.run', side_effect=AssertionError('No Git required')):
            source = source_version(self.root)
        _policy_source_identity(source, self.manifest)
        self.assertEqual(source['identity_method'], 'verified_source_freeze')
        self.assertFalse(source['dirty'])

    def test_changed_module_or_reference_entrypoint_invalidates_freeze(self):
        for path in (self.module, self.script):
            with self.subTest(path=path):
                before = path.read_text()
                path.write_text(before + '# changed after freeze\n')
                with self.assertRaisesRegex(ValueError, 'Frozen executable source'):
                    source_version(self.root)
                path.write_text(before)

    def test_unversioned_source_cannot_inherit_parent_repository_commit(self):
        (self.root / 'source_freeze.json').unlink()
        with patch('manipulation_agent.records.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=str(self.root.parent))):
            source = source_version(self.root)
        self.assertIsNone(source['commit'])
        self.assertIsNone(source['dirty'])
        self.assertEqual(source['identity_method'], 'unversioned')

    def test_matching_commit_does_not_hide_different_executable_bytes(self):
        source = source_version(self.root)
        source['release_source_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'unverified executable source'):
            _policy_source_identity(source, self.manifest)

    def test_policy_requires_clean_source_and_digest_evidence(self):
        for change in ({'dirty': True}, {'release_source_sha256': None}, {'commit': None}):
            with self.subTest(change=change):
                source = dict(source_version(self.root), **change)
                with self.assertRaisesRegex(ValueError, 'unverified executable source'):
                    _policy_source_identity(source, self.manifest)


if __name__ == '__main__':
    unittest.main()
