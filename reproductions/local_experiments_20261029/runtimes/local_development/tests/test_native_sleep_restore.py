from types import SimpleNamespace
import hashlib
import json

import pytest

from manipulation_agent.simulator_barriers import restore_native_sleep_flags, compare_native_state


class Body:
    def __init__(self, name, asleep):
        self.name, self.is_asleep = name, asleep
        self.calls = []

    def sleep(self):
        self.calls.append('sleep')
        self.is_asleep = True

    def wake(self):
        self.calls.append('wake')
        self.is_asleep = False


def test_restore_recorded_sleep_instead_of_ignoring_state_difference():
    chair = Body('chair', False)
    table = Body('table', True)
    untouched = Body('unchanged', False)
    sim = SimpleNamespace(scenes=[SimpleNamespace(objects=[chair, table, untouched])])
    expected = {0: {'registry': {'object_registry': {
        'chair': {'is_asleep': True}, 'table': {'is_asleep': False},
        'unchanged': {'is_asleep': False}}}}}
    def current():
        return {0: {'registry': {'object_registry': {
            b.name: {'is_asleep': b.is_asleep} for b in [chair, table, untouched]}}}}
    assert len(compare_native_state(expected, current())[0]) == 2
    changes = restore_native_sleep_flags(sim, expected)
    assert len(changes) == 2
    assert chair.calls == ['sleep']
    assert table.calls == ['wake']
    assert untouched.calls == []
    assert compare_native_state(expected, current())[0] == []


def test_do_not_change_objects_without_recorded_sleep_state():
    body = Body('unrecorded', False)
    sim = SimpleNamespace(scenes=[SimpleNamespace(objects=[body])])
    assert restore_native_sleep_flags(sim, {0: {'registry': {'object_registry': {}}}}) == []
    assert body.calls == []


@pytest.mark.parametrize('field,protocol', [
    ('camera_initialization_barrier', 'native_state_checked_post_rgb_startup_v1'),
    ('articulation_edit_barrier', 'native_state_checked_before_articulation_edit_v1'),
])
def test_checked_lifecycle_barriers_accept_sleep_restoring_state_protocol(tmp_path, field, protocol):
    from manipulation_agent.research_native import load_instance
    from manipulation_agent.simulator_barriers import SLEEP_PROTOCOL
    spec = dict(schema_version=1, task='example', instance=0,
                scene_model='house_single_floor', scoring_protocol='original_native_predicates_research_instance_v1',
                robot_start_position=[0.,0.,0.], robot_start_orientation=[0.,0.,0.,1.],
                physics_edit_barrier=SLEEP_PROTOCOL, **{field: protocol})
    for key, body in [('scene_template', '{}'), ('task_state', '{}'),
                      ('bddl_definition', '(define (problem example-0) (:goal (and)))')]:
        path = tmp_path/key
        path.write_text(body)
        spec[key] = path.name
        spec[key+'_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path/'spec.json'
    manifest.write_text(json.dumps(spec))
    assert load_instance(manifest, task='example', instance=0)[field] == protocol
    spec['physics_edit_barrier'] = 'unchecked'
    manifest.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match='checked native state barrier'):
        load_instance(manifest, task='example', instance=0)
