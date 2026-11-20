"""Offline staging safeguards; these fixtures are not robot-success evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stage_rax_skill',REPO/'scripts/stage_rax_skill.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.library=root/'library';self.session=root/'session'
        self.library.mkdir();self.session.mkdir()
        (self.session/'workspace/code').mkdir(parents=True)
        (self.session/'workspace/memory').mkdir()
        (self.session/'workspace/memory/INDEX.md').write_text('Existing memory\n')
        cfg={'data_root':str(root)}
        (self.session/'config.json').write_text(json.dumps(cfg))
        self.state={'phase':'exploring','active':None,'exploration_used':0,'bundles':{},
                    'config_sha256':hashlib.sha256((self.session/'config.json').read_bytes()).hexdigest()}
        self.save()
        self.skill=self.library/'oracle_waypoints/rax_skills/bottle'
        self.skill.mkdir(parents=True)
        code='def main(ctx):\n return {"ok": False}\n'
        (self.skill/'controller.py').write_text(code)
        (self.skill/'SKILL.md').write_text('Fixture, not demonstrated robotics.\n')
        (self.skill/'spec.json').write_text(json.dumps({'interface':'rax_main_ctx','protocol':'oracle_waypoints',
            'status':'draft','version':1,'bundle':'fixture','runner_sha256':'fixture',
            'controller_sha256':hashlib.sha256(code.encode()).hexdigest()}))
        self.manifest()
        clean=patch.object(module.SkillGit,'require_clean');clean.start();self.addCleanup(clean.stop)
        head=patch.object(module.SkillGit,'head',return_value='fixture-commit');head.start();self.addCleanup(head.stop)

    def save(self):
        (self.session/'state.json').write_text(json.dumps(self.state))

    def manifest(self):
        files={name:hashlib.sha256((self.skill/name).read_bytes()).hexdigest()
               for name in ('controller.py','SKILL.md','spec.json')}
        (self.skill/'source_manifest.json').write_text(json.dumps(files))

    def stage(self,**kwargs):
        return module.stage(self.library,self.session,'bottle','bottle',**kwargs)

    def test_explicit_draft_staging_preserves_code_and_keeps_host_evidence_private(self):
        with self.assertRaisesRegex(ValueError,'allow-draft'):self.stage()
        origin=self.stage(allow_draft=True)
        target=self.session/'workspace/code/bottle'
        self.assertEqual((target/'controller.py').read_bytes(),(self.skill/'controller.py').read_bytes())
        self.assertEqual({p.name for p in target.iterdir()},{'controller.py','SKILL.md'})
        self.assertTrue(origin['requires_new_registration_and_rehearsal'])
        self.assertTrue((self.session/'workspace/memory/INDEX.md').read_text().startswith('Existing memory'))
        self.assertTrue((self.session/'seeded_skills/bottle.json').exists())
        self.assertFalse((target/'evidence.json').exists())

    def test_active_or_previously_used_session_cannot_be_seeded(self):
        for extra in ({'active':{'phase':'exploration'}},{'exploration_used':1},{'phase':'submitted'}):
            state=dict(self.state);self.state.update(extra);self.save()
            with self.assertRaisesRegex(ValueError,'fresh prepared session'):self.stage(allow_draft=True)
            self.state=state
        self.assertFalse((self.session/'workspace/code/bottle').exists())

    def test_tampered_source_and_existing_project_are_preserved(self):
        original=(self.skill/'controller.py').read_bytes()
        (self.skill/'controller.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'source hash mismatch'):self.stage(allow_draft=True)
        (self.skill/'controller.py').write_bytes(original)
        target=self.session/'workspace/code/bottle';target.mkdir();(target/'controller.py').write_text('human work')
        with self.assertRaisesRegex(ValueError,'overwrite'):self.stage(allow_draft=True)
        self.assertEqual((target/'controller.py').read_text(),'human work')

    def test_project_symlink_cannot_escape_session(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir()
        (self.session/'workspace/code/bottle').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'escapes'):self.stage(allow_draft=True)
        self.assertEqual(list(outside.iterdir()),[])


if __name__=='__main__':unittest.main()
