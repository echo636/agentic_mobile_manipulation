"""Generated instances must not masquerade as public/human-scored episodes."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from manipulation_agent.research_native import PROTOCOL, load_instance
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class ResearchInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = dict(schema_version=1, task='native_example', instance=0,
                         scene_model='house_single_floor', scoring_protocol=PROTOCOL,
                         robot_start_position=[0, 0, .01], robot_start_orientation=[0, 0, 0, 1],
                         load_room_instances=['kitchen_0'])
        for key, text in (('scene_template', '{}'), ('task_state', '{}'),
                          ('bddl_definition', '(define (problem native_example-0) (:goal (and)))')):
            path = self.root / (key + '.txt')
            path.write_text(text)
            self.spec[key] = path.name
            self.spec[key + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.path = self.root / 'instance.json'

    def load(self, **changes):
        self.path.write_text(json.dumps(dict(self.spec, **changes)))
        return load_instance(self.path, task='native_example', instance=0)

    def test_frozen_generated_instance_has_research_identity(self):
        value = self.load()
        self.assertEqual(value['split'], 'research_development')
        self.assertFalse(value['human_reference_metrics_available'])
        self.assertEqual(Path(value['task_state']).parent, self.root)

    def test_modified_scene_cannot_reuse_frozen_spec(self):
        (self.root / self.spec['scene_template']).write_text('{"changed": true}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch: scene_template'):
            self.load()

    def test_wrong_task_cannot_use_another_instance(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.load(task='another_task')

    def test_nonfinite_robot_pose_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'robot_start_position'):
            self.load(robot_start_position=[float('nan'), 0, 0])

    def test_nonunit_orientation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unit quaternion'):
            self.load(robot_start_orientation=[0, 0, 0, 2])

    def test_cloth_state_cannot_silently_reload_with_a_changed_topology(self):
        from manipulation_agent.cloth_topology import PROTOCOL as CLOTH_PROTOCOL
        directory = self.root / 'cloth_topology'
        directory.mkdir()
        mesh = directory / 'mesh.json'
        mesh.write_text('{"faces": [0, 1, 2]}')
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps({'protocol': CLOTH_PROTOCOL, 'files': {
            mesh.name: hashlib.sha256(mesh.read_bytes()).hexdigest()}}))
        topology = dict(protocol=CLOTH_PROTOCOL, directory=directory.name,
                        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        value = self.load(cloth_topology=topology)
        self.assertEqual(value['cloth_topology']['directory'], str(directory))
        mesh.write_text('{"faces": [2, 1, 0]}')
        with self.assertRaisesRegex(ValueError, 'Cloth topology input hash mismatch'):
            self.load(cloth_topology=topology)


class ResearchScoringTests(unittest.TestCase):
    def test_native_scoring_does_not_invent_human_metrics_or_public_success(self):
        for achieved in (False, True):
            with self.subTest(achieved=achieved):
                backend = RGBBackend.__new__(RGBBackend)
                backend.research_instance = {'task': 'native_example'}
                backend.high_level_harness = False
                backend._goal_options = lambda: [[True, achieved]]
                predicate = SimpleNamespace(_check_goal_fn=lambda: (achieved, {}))
                backend.env = SimpleNamespace(task=SimpleNamespace(success=achieved,
                    _termination_conditions={'predicate': predicate}))
                backend.task_metric = SimpleNamespace(_compute_episode_metrics=lambda *a: self.fail(
                    'Human-reference metric requested for a generated research instance'))
                backend.initial_goals = [[False, False]]
                backend.steps = 2
                backend.navigation_distance = 1.2
                backend.inside_placement = 'official_volume'
                backend.sampling_physics_steps = 0
                backend.task_metadata = {'split': 'research_development'}
                result = backend.evaluate()
                self.assertIs(result['official_task_success'], None)
                self.assertIs(result['official_metrics'], None)
                self.assertEqual(result['native_research_task_success'], achieved)
                self.assertEqual(result['task_success'], achieved)
                self.assertEqual(result['protocol'], PROTOCOL)
                self.assertFalse(result['human_reference_metrics_available'])


if __name__ == '__main__':
    unittest.main()
