"""A sampled development instance must retain matching preparation evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.catalog_tasks import load_catalog_spec


class CatalogEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.scene = self.root / 'scene.json'
        self.scene.write_text('{"objects_info": {"init_info": {}}}')
        self.sample = self.root / 'sample.json'
        self.evidence = dict(status='passed', model_used=False, task='making_a_meal',
            scene='kitchen', seed=0, bddl_sha256='a' * 64, initial_conditions=[True],
            initial_goal_satisfied=False, saved_files={'scene.json': self.sha(self.scene)})
        self.spec = dict(schema_version=1, kind='behavior1k_sampled_instance',
            task='making_a_meal', scene='kitchen', seed=0, instance_id='sample0',
            bddl_sha256='a' * 64, public_instruction='Serve the cooked meal in a bowl.',
            frozen_instance=dict(path='scene.json', sha256=self.sha(self.scene)),
            environment_config=dict(task=dict(activity_name='making_a_meal'),
                                    scene=dict(scene_model='kitchen')))
        self.path = self.root / 'spec.json'
        self.save()

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save(self):
        self.sample.write_text(json.dumps(self.evidence))
        self.spec['sampling_record'] = dict(path='sample.json', sha256=self.sha(self.sample))
        self.path.write_text(json.dumps(self.spec))

    def test_valid_evidence_and_relocation_then_changed_scene(self):
        self.assertEqual(load_catalog_spec(self.path)['task'], 'making_a_meal')
        for key in ('frozen_instance', 'sampling_record'):
            self.spec[key]['path'] = str(self.root / self.spec[key]['path'])
        self.path.write_text(json.dumps(self.spec))
        self.assertEqual(load_catalog_spec(self.path)['seed'], 0)
        self.scene.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            load_catalog_spec(self.path)

    def test_reject_failed_or_different_preparation(self):
        for change in (dict(status='failed'), dict(seed=1), dict(initial_conditions=[False]),
                       dict(initial_goal_satisfied=True), dict(saved_files={})):
            with self.subTest(change=change):
                original = dict(self.evidence)
                self.evidence.update(change)
                self.save()
                with self.assertRaisesRegex(ValueError, 'sampling evidence'):
                    load_catalog_spec(self.path)
                self.evidence = original

    def test_reject_environment_for_another_task(self):
        self.spec['environment_config']['task']['activity_name'] = 'make_pizza'
        self.save()
        with self.assertRaisesRegex(ValueError, 'environment does not match'):
            load_catalog_spec(self.path)


if __name__ == '__main__':
    unittest.main()
