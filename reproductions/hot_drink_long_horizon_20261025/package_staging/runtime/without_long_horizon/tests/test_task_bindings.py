import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from manipulation_agent.omnigibson_backend import select_compatible_scene
from manipulation_agent.task_bindings import audit_wildcard_bindings, validate_runtime_bindings


class WildcardBindingTests(unittest.TestCase):
    def fixture(self):
        objects = {'floor': {'args': {'category': 'floors', 'in_rooms': ['bedroom_0']}},
                   'stand': {'args': {'category': 'stand', 'in_rooms': ['bedroom_0']}}}
        objects.update({f'nightstand{i}': {'args': {'category': 'nightstand', 'in_rooms': ['bedroom_0']}}
                        for i in range(3)})
        data = {'objects_info': {'init_info': objects},
                'metadata': {'task': {'inst_to_name': {'floor.n.01_1': 'floor', 'stand.n.04_1': 'stand'}}}}
        required = {'floor.n.01_1', *(f'stand.n.04_{i}' for i in range(1, 5))}
        # The integration audit additionally exercises the installed official
        # compiler on actual task files. This fixture controls category counts.
        def compile_task(*, scene_layout):
            room = scene_layout['bedroom']
            count = room.get('stand', 0) + room.get('nightstand', 0)
            return SimpleNamespace(object_scope=['floor.n.01_1'] + [f'stand.n.04_{i}' for i in range(1, count + 1)])
        task = SimpleNamespace(parse_base_scope=lambda: (None, ['floor.n.01_1'], {'floor.n.01_1': 'bedroom'}),
                               compile=compile_task)
        leaf = lambda name: SimpleNamespace(name=name, is_leaf=True, categories=[SimpleNamespace(name=name)])
        synset = SimpleNamespace(is_leaf=False, descendants=[leaf('stand'), leaf('nightstand')])
        kb = SimpleNamespace(get_task=lambda name: task, get_synset=lambda name: synset)
        return data, required, kb

    def test_missing_cache_entries_can_use_real_descendant_objects_without_mutation(self):
        data, required, kb = self.fixture()
        before = copy.deepcopy(data)
        audit = audit_wildcard_bindings(data, required, 'fixture', knowledge_base=kb)
        self.assertEqual(audit['missing_static_bindings'], ['stand.n.04_2', 'stand.n.04_3', 'stand.n.04_4'])
        self.assertEqual(set(audit['candidate_assignments_offline_only'].values()),
                         {'nightstand0', 'nightstand1', 'nightstand2'})
        self.assertTrue(audit['runtime_validation_required'])
        self.assertEqual(data, before)
        with tempfile.TemporaryDirectory() as folder:
            scene = Path(folder) / 'scene.json'; instance = Path(folder) / 'instance.json'
            scene.write_text(json.dumps(data)); instance.write_text(json.dumps(dict.fromkeys(required, {})))
            original = scene.read_bytes(); deferred = []
            with patch('manipulation_agent.task_bindings._knowledge_base', return_value=kb):
                selected, actual, rejected = select_compatible_scene(
                    scene, instance, task_name='fixture', deferred_bindings=deferred)
            self.assertEqual(selected, scene)
            self.assertEqual(actual, before)
            self.assertEqual(rejected, [])
            self.assertEqual(len(deferred), 1)
            self.assertEqual(scene.read_bytes(), original)

    def test_missing_physical_object_or_wrong_room_is_not_ignored(self):
        for change in ('removed', 'wrong_room'):
            data, required, kb = self.fixture()
            if change == 'removed':
                del data['objects_info']['init_info']['nightstand2']
            else:
                data['objects_info']['init_info']['nightstand2']['args']['in_rooms'] = ['bathroom_0']
            with self.assertRaisesRegex(ValueError, 'not supplied by official wildcard expansion'):
                audit_wildcard_bindings(data, required, 'fixture', knowledge_base=kb)

    def test_missing_ordinary_binding_is_not_treated_as_a_wildcard(self):
        data, required, kb = self.fixture()
        del data['metadata']['task']['inst_to_name']['floor.n.01_1']
        with self.assertRaisesRegex(ValueError, 'non-wildcard'):
            audit_wildcard_bindings(data, required, 'fixture', knowledge_base=kb)

    def test_bad_cached_object_is_not_silently_replaced(self):
        data, required, kb = self.fixture()
        data['metadata']['task']['inst_to_name']['stand.n.04_1'] = 'nonexistent'
        with self.assertRaisesRegex(ValueError, 'absent entities'):
            audit_wildcard_bindings(data, required, 'fixture', knowledge_base=kb)

    def test_runtime_entities_are_required_even_after_static_admission(self):
        scope = {'stand.n.04_1': SimpleNamespace(name='stand'), 'stand.n.04_2': None}
        required = {'stand.n.04_1', 'stand.n.04_2', 'stand.n.04_3'}
        audit = validate_runtime_bindings(scope, required)
        self.assertEqual(audit['status'], 'failed')
        self.assertEqual(audit['missing_instances'], ['stand.n.04_2', 'stand.n.04_3'])
        scope.update({key: SimpleNamespace(name=key) for key in audit['missing_instances']})
        self.assertEqual(validate_runtime_bindings(scope, required)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
