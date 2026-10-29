from types import SimpleNamespace
from manipulation_agent.executors.carry import ControlledCarry


def test_open_joint_snapshot_records_positions_without_setting_state():
    records = []
    joint = SimpleNamespace(name='lid', get_state=lambda: (0.75, 0),
                            lower_limit=0, upper_limit=1)
    state = SimpleNamespace(get_value=lambda: True,
                            relevant_joints_info=(False, [joint], [1]))
    executor = SimpleNamespace(_placement_record=records.append)
    ControlledCarry._record_open_joints(executor, SimpleNamespace(name='container'),
                                      state, 'after_settling', True)
    assert records[0]['observed_open'] is True
    assert records[0]['joints'] == [dict(name='lid', position=0.75,
                                       lower_limit=0.0, upper_limit=1.0)]
    assert records[0]['phase'] == 'after_settling'


def test_unavailable_joint_state_is_explicit_in_private_diagnostic():
    records = []
    state = SimpleNamespace(get_value=lambda: True)
    executor = SimpleNamespace(_placement_record=records.append)
    ControlledCarry._record_open_joints(executor, SimpleNamespace(name='container'),
                                      state, 'before', False)
    assert records[0]['snapshot_error'] == 'AttributeError'
    assert records[0]['joints'] == []


def test_real_open_action_preserves_settling_and_failure_rollback(monkeypatch):
    import sys
    from contextlib import nullcontext
    from types import ModuleType
    import pytest
    from manipulation_agent.contracts import SkillError
    Open, Toggle = type('Open', (), {}), type('Toggle', (), {})
    module = ModuleType('omnigibson.object_states')
    module.Open, module.ToggledOn = Open, Toggle
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', module)
    for fail in (False, True):
        records, trace, pos = [], [], [0.0]
        joint = SimpleNamespace(name='lid', lower_limit=0, upper_limit=1,
                                get_state=lambda: (pos[0], 0))
        def setter(wanted, **kwargs):
            trace.append(('set', wanted, kwargs))
            pos[0] = 1.0
            return True
        state = SimpleNamespace(set_value=setter, get_value=lambda: pos[0] > .5,
                                relevant_joints_info=(False, [joint], [1]))
        obj = type('Container', (), {})()
        obj.name, obj.states = 'container', {Open: state}
        class Executor(ControlledCarry):
            pass
        exe = Executor()
        exe._placement_record = records.append
        exe._container_payload = lambda obj: []
        exe._anchored_operation = lambda obj: nullcontext()
        exe._sync_diagnostic_articulation_visuals = lambda obj: trace.append('sync')
        exe._relocate_container_payload = lambda payload: trace.append('payload')
        exe._verify_container_payload = lambda obj, payload: trace.append('verify')
        def step(action):
            trace.append('step')
            if fail:
                pos[0] = 0
        exe._step = step
        exe.robot = SimpleNamespace(get_joint_positions=lambda: None, q_to_action=lambda x: x)
        def restore(saved, **kwargs):
            pos[0] = saved
            trace.append('rollback')
        exe.og = SimpleNamespace(sim=SimpleNamespace(
            dump_state=lambda **kwargs: pos[0], load_state=restore))
        if fail:
            with pytest.raises(SkillError, match='did not remain stable'):
                exe._ideal_state_action('open', obj, 2)
            assert trace[-1] == 'rollback'
            assert pos[0] == 0
        else:
            result = exe._ideal_state_action('open', obj, 2)
            assert result['postcondition'] == 'official_state_and_containment_after_settling'
            assert trace[-1] == 'verify'
        assert trace[:3] == [('set', True, {'fully': True}), 'sync', 'payload']
        assert trace.count('step') == 2
        snapshots = [r for r in records if r['status'] == 'open_joint_snapshot']
        assert [r['phase'] for r in snapshots] == ['before', 'after_setter', 'after_settling']
        assert snapshots[0]['joints'][0]['position'] == 0
        assert snapshots[1]['joints'][0]['position'] == 1
        assert snapshots[2]['observed_open'] is not fail
