"""Custom definition registration preserves source identity and native compilation."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.tasks.native_definition import register_definition, resolve_definition, sampling_scene_config, with_saved_bindings

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / 'task_designs/drafts/planning_interactions_v1/table_service_turnover/problem0.bddl'


class DefinitionIdentity(unittest.TestCase):
    def test_online_sampling_keeps_unbound_native_furniture_and_leaves_base_untouched(self):
        base = {'scene_file': 'old.json', 'scene_instance': 'old',
                'load_task_relevant_only': True, 'load_room_types': ['kitchen'],
                'load_object_categories': ['floors'], 'include_robots': True}
        scene = sampling_scene_config(base, 'house_single_floor')
        self.assertFalse(scene['load_task_relevant_only'])
        self.assertIsNone(scene['load_object_categories'])
        self.assertIsNone(scene['load_room_types'])
        self.assertNotIn('scene_file', scene)
        self.assertNotIn('scene_instance', scene)
        self.assertFalse(scene['include_robots'])
        self.assertEqual(base['scene_file'], 'old.json')
        self.assertTrue(base['load_task_relevant_only'])

    def test_saved_bindings_preserve_physics_and_refuse_missing_or_conflicting_entities(self):
        scene = {'objects_info': {'init_info': {'robot': {}, 'cup': {}}},
                 'state': {'registry': {'system_registry': {'dust': {'particles': [1]}}}},
                 'metadata': {'task': {'task': {}}}}
        bindings = {'agent.n.01_1': 'robot', 'mug.n.04_1': 'cup', 'dust.n.01_1': 'dust'}
        fixed = with_saved_bindings(scene, bindings)
        self.assertEqual(fixed['state'], scene['state'])
        self.assertEqual(fixed['objects_info'], scene['objects_info'])
        self.assertNotIn('inst_to_name', scene['metadata']['task'])
        self.assertEqual(fixed['metadata']['task']['inst_to_name'], bindings)
        with self.assertRaisesRegex(ValueError, 'existing scene entities'):
            with_saved_bindings(scene, {'mug.n.04_1': 'absent'})
        with self.assertRaisesRegex(ValueError, 'conflict'):
            with_saved_bindings(fixed, {'mug.n.04_1': 'robot'})

    def test_custom_source_is_pinned_and_official_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'problem0.bddl'
            path.write_bytes(TASK.read_bytes())
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            spec = {'kind': 'custom_bddl_sampled_instance', 'bddl_sha256': sha,
                    'definition': {'path': path.name, 'sha256': sha}}
            self.assertEqual(resolve_definition(spec, root / 'task.json'), path)
            self.assertIsNone(resolve_definition({'kind': 'behavior1k_sampled_instance'}, root / 'task.json'))
            path.write_text(path.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                resolve_definition(spec, root / 'task.json')


@unittest.skipUnless(importlib.util.find_spec('bddl'), 'Requires pinned BDDL')
class Registration(unittest.TestCase):
    def setUp(self):
        from bddl.knowledge_base.models import Task
        class KnowledgeBase:
            def __init__(self):
                self.tasks = {}
                self.synsets = {}
            def get_task(self, name):
                return self.tasks.get(name)
            def get_synset(self, name):
                return self.synsets.setdefault(name, object())
            def add_task(self, name, definition):
                task = Task(name=name, definition=definition)
                task.knowledgebase = self
                self.tasks[name] = task
                return task
        self.kb = KnowledgeBase()

    def test_register_compile_and_identical_second_registration(self):
        text = TASK.read_text()
        task = register_definition(self.kb, 'table_service_turnover', text)
        self.assertIs(register_definition(self.kb, 'table_service_turnover', text), task)
        compiled = task.compile({})
        self.assertTrue(compiled.goal_conditions)
        self.assertEqual(len(task.synsets), 8)
        with self.assertRaisesRegex(ValueError, 'replace an existing'):
            register_definition(self.kb, 'table_service_turnover', text + '\n')

    def test_problem_name_cannot_silently_select_another_task(self):
        with self.assertRaisesRegex(ValueError, 'problem name'):
            register_definition(self.kb, 'another_task', TASK.read_text())
        self.assertFalse(self.kb.tasks)

    def test_future_types_are_registered_without_adding_initial_entities(self):
        path = TASK.parent.parent / 'two_zone_woodwork_and_cleanup/problem0.bddl'
        task = register_definition(self.kb, 'two_zone_woodwork_and_cleanup', path.read_text())
        self.assertEqual(task.future_synsets, {self.kb.synsets['half__log.n.01']})
        self.assertTrue(task.compile({}).goal_conditions)
