"""A wrong state-action pixel must not be reported as a distance problem."""
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.observations.boundary import public_execution_error
from manipulation_agent.high_level_hot_drink import HighLevelHotDrink


@pytest.fixture
def visual_state_backend(monkeypatch, tmp_path):
    torch = pytest.importorskip('torch')
    states = ModuleType('omnigibson.object_states')
    states.Open, states.Inside, states.ToggledOn = (
        type(name, (), {}) for name in ('Open', 'Inside', 'ToggledOn'))
    parent = ModuleType('omnigibson')
    parent.object_states = states
    monkeypatch.setitem(sys.modules, 'omnigibson', parent)
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', states)
    backend = RGBBackend.__new__(RGBBackend)
    backend.output = tmp_path
    backend.torch = torch
    backend.demo_motion, backend.ideal_carry = False, True
    backend.high_level_harness = False
    backend.steps = 0
    backend.robot = SimpleNamespace(get_position_orientation=lambda: (torch.zeros(3), None))
    backend.env = SimpleNamespace(scene=SimpleNamespace(objects=[]))
    target = SimpleNamespace(states={}, fixed_base=True, category='floors')
    backend._ground = Mock(return_value=(target, torch.tensor([3., 0., .005]), {}))
    backend._get_held = Mock(return_value=None)
    backend._navigate = Mock(side_effect=AssertionError('Unexpected base motion'))
    backend._ideal_state_action = Mock(return_value={'primitive': 'open'})
    return backend, target, states


@pytest.mark.parametrize('primitive', ['open', 'close', 'toggle_on', 'toggle_off'])
def test_unsupported_surface_is_rejected_before_distance(visual_state_backend, primitive):
    backend, _, _ = visual_state_backend
    with pytest.raises(SkillError) as error:
        backend.execute_visual(primitive, {}, 100)
    feedback = public_execution_error(error.value)
    assert feedback['code'] == 'invalid_visual_target'
    assert feedback['world_may_have_changed'] is False
    assert 'different visible point' in feedback['message']
    backend._navigate.assert_not_called()
    backend._ideal_state_action.assert_not_called()
    assert backend.steps == 0


@pytest.mark.parametrize('primitive', ['open', 'close', 'toggle_on', 'toggle_off'])
def test_supported_distant_target_still_requires_approach(visual_state_backend, primitive):
    backend, target, states = visual_state_backend
    target.states = {states.Open: object(), states.ToggledOn: object()}
    with pytest.raises(SkillError) as error:
        backend.execute_visual(primitive, {}, 100)
    assert error.value.code == 'out_of_reach'
    backend._navigate.assert_not_called()
    backend._ideal_state_action.assert_not_called()


def test_supported_near_target_uses_existing_state_action(visual_state_backend):
    backend, target, states = visual_state_backend
    target.states = {states.Open: object()}
    backend._ground.return_value = target, backend.torch.tensor([.8, 0., 1.]), {}
    backend.execute_visual('open', {}, 100)
    backend._ideal_state_action.assert_called_once_with('open', target, 100)
    backend._navigate.assert_not_called()


def test_wrong_pixel_does_not_require_staging_a_held_item(visual_state_backend):
    backend, _, _ = visual_state_backend
    backend._get_held.return_value = object()
    with pytest.raises(SkillError) as error:
        backend.execute_visual('open', {}, 100)
    assert error.value.code == 'invalid_visual_target'
    backend._ideal_state_action.assert_not_called()


def prepare_unbound_cabinet(fixture):
    backend, target, states = fixture
    backend.high_level_harness = True
    backend.semantic_magic = False
    backend.high_level_state = HighLevelHotDrink()
    backend._semantic_targets = Mock(return_value={})
    target.category, target.name = 'bottom_cabinet', 'bottom_cabinet_fancyy_0'
    target.prim_path = '/World/scene_0/' + target.name
    target.states = {states.Open: object()}
    pixel = {'image_ref': 'rgb-00006-right', 'point': [.47, .404]}
    backend._ground.return_value = (target, backend.torch.tensor([.36, 0., 1.]),
        {'selected_pixel': pixel['point'], 'image_ref': pixel['image_ref'],
         'selected_object_prim': target.prim_path})
    return backend, target, states, pixel


@pytest.mark.parametrize('primitive', ['open', 'close'])
def test_unbound_openable_uses_native_motor_without_changing_task_state(visual_state_backend, primitive):
    backend, cabinet, _, pixel = prepare_unbound_cabinet(visual_state_backend)
    before = backend.high_level_state.snapshot()
    result = backend.execute_visual(primitive, pixel, 100)
    backend._ideal_state_action.assert_called_once_with(primitive, cabinet, 100)
    backend._navigate.assert_not_called()
    assert backend.high_level_state.snapshot() == before
    assert result['environment_only_action'] == 'non_task_open_close_v1'
    assert result['environment_target_prim'] == cabinet.prim_path
    assert result.get('semantic_target') is None


def test_unbound_openable_still_enforces_native_reach_and_empty_hand(visual_state_backend):
    backend, _, _, pixel = prepare_unbound_cabinet(visual_state_backend)
    obj, _, ground = backend._ground.return_value
    backend._ground.return_value = obj, backend.torch.tensor([1.41, 0., 1.]), ground
    with pytest.raises(SkillError, match='approach') as error:
        backend.execute_visual('open', pixel, 100)
    assert error.value.code == 'out_of_reach'
    backend._get_held.return_value = object()
    with pytest.raises(SkillError) as error:
        backend.execute_visual('open', pixel, 100)
    assert error.value.code == 'hand_occupied'
    backend._ideal_state_action.assert_not_called()


def test_high_level_wrong_surface_is_not_promoted_to_openable(visual_state_backend):
    backend, cabinet, _, pixel = prepare_unbound_cabinet(visual_state_backend)
    cabinet.states = {}
    with pytest.raises(SkillError) as error:
        backend.execute_visual('open', pixel, 100)
    assert error.value.code == 'invalid_visual_target'
    backend._ideal_state_action.assert_not_called()


def test_environment_motor_failure_does_not_report_success(visual_state_backend):
    backend, _, _, pixel = prepare_unbound_cabinet(visual_state_backend)
    before = backend.high_level_state.snapshot()
    backend._ideal_state_action.side_effect = SkillError('postcondition_error', 'Door did not remain open', changed=True)
    with pytest.raises(SkillError) as error:
        backend.execute_visual('open', pixel, 100)
    assert error.value.code == 'postcondition_error'
    assert backend.high_level_state.snapshot() == before


def test_environment_route_replays_independently_and_keeps_identity_private(visual_state_backend):
    from scripts.audit_demo_trial import replay_high_level_actions
    from manipulation_agent.observations.boundary import public_action_effect
    backend, cabinet, _, pixel = prepare_unbound_cabinet(visual_state_backend)
    details = backend.execute_visual('open', pixel, 100)
    events = [
        {'kind': 'tool_call', 'name': 'act', 'arguments': {'primitive': 'open', 'target': pixel}},
        {'kind': 'private_executor_result', 'details': details},
        {'kind': 'tool_result', 'name': 'act', 'result': {'ok': True}},
    ]
    report = replay_high_level_actions(events, [], task_object_names={'kettle_276'})
    assert report['environment_only_actions'] == 1
    assert report['committed_actions'] == 0
    assert report['private_state_log_matches']
    assert report['evaluation']['task_success'] is False
    assert cabinet.name not in str(public_action_effect('open', details))
