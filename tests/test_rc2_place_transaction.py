"""rc2 MAS_PLACE_TRANSACTIONAL (2026-11-22): simulator-free tests of the decision logic and the _place_state wiring."""
import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import placement_transaction as TX
from manipulation_agent.observations.boundary import factual_execution_error


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_TRANSACTIONAL', raising=False)
    assert TX.enabled() is False
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')
    assert TX.enabled() is True


def test_transaction_module_no_longer_owns_checks():
    """10:59 split: the release checks live in placement_stable (MAS_PLACE_STABLE_CHECK)."""
    assert not hasattr(TX, 'evaluate') and not hasattr(TX, 'before_release') and not hasattr(TX, 'after_settle')
    s = 2 ** -.5
    assert TX.tilt_deg([s, 0, 0, s]) == pytest.approx(90)          # re-exported for the probes


def test_should_roll_back_only_after_release():
    assert TX.should_roll_back(SkillError('postcondition_error', 'x', changed=True, stage='released_placement_unconfirmed'))
    assert not TX.should_roll_back(SkillError('postcondition_error', 'x', changed=True, stage=None))      # not released
    assert not TX.should_roll_back(SkillError('placement_surface_unavailable', 'x', changed=False))     # refused
    assert not TX.should_roll_back(SkillError('executor_wall_guard', 'x', changed=True,
                                              stage='released_placement_unconfirmed'))


def test_rolled_back_error_is_the_existing_not_placed_receipt():
    exc = SkillError('pre_condition_error', 'private detail', changed=False, stage='released_rolled_back')
    receipt = factual_execution_error(exc, 'place_on_top', 'holding_item')
    assert receipt['code'] == 'not_placed'
    assert receipt['message'] == 'The item was not placed. The item is still in your hand. Inspect the returned RGB.'
    assert receipt['world_may_have_changed'] is False
    assert 'private' not in receipt['message']
    old = factual_execution_error(SkillError('postcondition_error', 'x', changed=True,
                                             stage='released_placement_unconfirmed'), 'place_on_top', 'empty')
    assert old['code'] == 'result_unconfirmed'                                  # what the model saw before


class _Fake:
    """Just enough of the backend for _place_state's transaction wiring."""
    def __init__(self, fail):
        self.fail, self.records, self.steps_taken, self.restored = fail, [], 0, 0
        self.primitives = type('P', (), {'_empty_action': staticmethod(lambda: None)})()

    def _get_held(self):
        return 'cup'

    @property
    def _carry_contents(self):
        return []

    def _place_state_once(self, *a, **k):
        if self.fail:
            raise self.fail
        return 'placed'

    def _placement_record(self, row):
        self.records.append(row)

    def _step(self, action):
        self.steps_taken += 1


def _wire(monkeypatch, ok=True):
    from manipulation_agent.executors.carry import ControlledCarry
    fake_snap = object()
    monkeypatch.setattr(TX, 'snapshot', lambda b: fake_snap)
    def restore(b, snap):
        assert snap is fake_snap
        b.restored += 1
        return (ok, None if ok else 'grasp_not_restored')
    monkeypatch.setattr(TX, 'restore', restore)
    return ControlledCarry._place_state


def test_place_state_rolls_back_and_raises_not_placed(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')
    place = _wire(monkeypatch)
    b = _Fake(SkillError('postcondition_error', 'x', changed=True, stage='released_placement_unconfirmed'))
    with pytest.raises(SkillError) as info:
        place(b, 'place_on_top', 'table', 10, None)
    assert info.value.code == 'pre_condition_error' and info.value.changed is False
    assert info.value.stage == 'released_rolled_back'
    assert b.restored == 1 and b.steps_taken == 1
    assert b.records[-1]['status'] == 'transaction_rolled_back'


def test_place_state_keeps_old_error_when_restore_fails(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')
    place = _wire(monkeypatch, ok=False)
    err = SkillError('postcondition_error', 'x', changed=True, stage='released_placement_unconfirmed')
    with pytest.raises(SkillError) as info:
        place(_Fake(err), 'place_on_top', 'table', 10, None)
    assert info.value is err


def test_place_state_off_is_unchanged(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_TRANSACTIONAL', raising=False)
    place = _wire(monkeypatch)
    err = SkillError('postcondition_error', 'x', changed=True, stage='released_placement_unconfirmed')
    b = _Fake(err)
    with pytest.raises(SkillError) as info:
        place(b, 'place_on_top', 'table', 10, None)
    assert info.value is err and b.restored == 0 and b.records == []
    assert place(_Fake(None), 'place_on_top', 'table', 10, None) == 'placed'


def test_refusal_before_release_is_not_rolled_back(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')
    place = _wire(monkeypatch)
    err = SkillError('placement_surface_unavailable', 'x', changed=False)
    b = _Fake(err)
    with pytest.raises(SkillError) as info:
        place(b, 'place_on_top', 'table', 10, None)
    assert info.value is err and b.restored == 0


def test_rolled_back_after_local_wrapper_still_not_placed():
    """The local-manipulation wrapper sets changed=True after any env step (found by the 2026-11-22 sim probe:
    the model saw result_unconfirmed although the item was back in the hand)."""
    exc = SkillError('pre_condition_error', 'x', changed=False, stage='released_rolled_back')
    exc.changed |= True
    receipt = factual_execution_error(exc, 'place_on_top', 'holding_item')
    assert receipt['code'] == 'not_placed'
    assert receipt['message'] == 'The item was not placed. The item is still in your hand. Inspect the returned RGB.'
    assert receipt['hand'] == 'holding_item'


def _through_local_wrapper(exc):
    """What local_manipulation.run does to every failure after env steps advanced."""
    exc.changed |= True
    return exc


def test_judge_only_native_false_reaches_model_as_result_unconfirmed():
    exc = _through_local_wrapper(SkillError('postcondition_error', 'x', changed=True,
                                            stage='released_placement_unconfirmed'))
    receipt = factual_execution_error(exc, 'place_on_top', 'empty')
    assert receipt['code'] == 'result_unconfirmed'
    assert receipt['message'] == ('The object was released; your hand is empty. Where it ended up could not be '
                                  'confirmed. Inspect the returned RGB before choosing the next action.')


def test_refusal_before_release_after_local_approach_is_unchanged():
    exc = _through_local_wrapper(SkillError('placement_surface_unavailable', 'x', changed=False))
    receipt = factual_execution_error(exc, 'place_on_top', 'holding_item')
    assert receipt['code'] == 'placement_surface_unavailable'


class _Vec:
    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return list(self._values)


class _ClothBackend:
    """Minimal CPU harness combining ControlledCarry._place_state, CheckedClothActions._place_cloth_surface,
    and LocalOperation.run without launching a simulator."""

    def __init__(self, *, distance=1.0, on_top=False, next_to=True):
        from manipulation_agent.executors.local_manipulation import LocalLimits
        self.local_limits = LocalLimits()
        self.steps = 0
        self.sampling_physics_steps = 0
        self.pose = (0., 0., 0.)
        self.point = (distance, 0., .7)
        self.deadline = type('D', (), {'check': staticmethod(lambda **kw: None)})()
        self.binding = type('B', (), {'grounding': {}, 'describe': staticmethod(lambda: {})})()
        self.primitives = type('P', (), {'_empty_action': staticmethod(lambda: None)})()
        self.env = type('E', (), {'scene': object()})()
        self.records = []
        self.events = []
        self.restored = 0
        self.frames_revision = 0
        self.target = type('T', (), {'name': 'table'})()
        self._on_top = on_top
        self._next_to = next_to
        self.held = self._make_cloth()

    def _make_cloth(self):
        backend = self
        class _Cloth:
            name = 'rag'
            prim_type = 'CLOTH'
            def __init__(self):
                self.states = {
                    'OnTop': type('S', (), {'get_value': staticmethod(lambda sup: backend._on_top)})(),
                    'NextTo': type('S', (), {'get_value': staticmethod(lambda tgt: backend._next_to)})(),
                }
            def set_position_orientation(self, *pose):
                pass
            def keep_still(self):
                pass
        return _Cloth()

    def _local_pose(self):
        return self.pose

    def _local_record(self, **kw):
        self.events.append(kw)

    def _resolve_local_target(self, binding):
        return self.target, self.point

    def _manipulation_preconditions(self, *args):
        pass

    def _local_ready(self, primitive, obj, point, op):
        import math
        return math.dist(self.pose[:2], point[:2]) <= self.local_limits.reach_m

    def _local_reposition(self, primitive, obj, point, op):
        self.pose = (point[0] - .8, point[1], 0.)
        op.admit_pose(self.pose)
        self.steps += 1

    def _get_held(self):
        return self.held

    @property
    def _carry_contents(self):
        return []

    def _carry_detach(self):
        self.held = None

    def _relocate_contents(self, held, contents):
        pass

    def _surface_candidates(self, held, target, point, yaw_degrees, payload, **kw):
        return [{'pose': (_Vec([1., 0., .7]), _Vec([0., 0., 0., 1.])), 'index': 0, 'support': target}]

    def _placement_record(self, row):
        self.records.append(row)

    def _step(self, action):
        self.steps += 1

    def _execute_bound_manipulation(self, primitive, obj, point, grounding, max_steps, **kw):
        from manipulation_agent.executors.carry import ControlledCarry
        return ControlledCarry._place_state(self, primitive, obj, max_steps, point)

    def _place_state_once(self, *a, **k):
        from manipulation_agent.executors.carry import ControlledCarry
        return ControlledCarry._place_state_once(self, *a, **k)

    def _place_cloth_surface(self, *a, **k):
        from manipulation_agent.executors.cloth_actions import CheckedClothActions
        return CheckedClothActions._place_cloth_surface(self, *a, **k)


def _patch_cloth_env(monkeypatch):
    import sys
    from types import ModuleType
    from manipulation_agent.executors import carry as carry_mod

    og = ModuleType('omnigibson')
    og_states = ModuleType('omnigibson.object_states')
    og_states.OnTop = 'OnTop'
    og_states.NextTo = 'NextTo'
    og_utils = ModuleType('omnigibson.utils')
    og_const = ModuleType('omnigibson.utils.constants')
    og_const.PrimType = type('PrimType', (), {'CLOTH': 'CLOTH'})
    monkeypatch.setitem(sys.modules, 'omnigibson', og)
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', og_states)
    monkeypatch.setitem(sys.modules, 'omnigibson.utils', og_utils)
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.constants', og_const)
    monkeypatch.setattr(carry_mod, 'snapshot_particles', lambda *a, **k: [])

    def fake_snap(b):
        return {'held': b.held}

    def fake_restore(b, snap):
        b.held = snap['held']
        b.restored += 1
        return True, None

    monkeypatch.setattr(TX, 'snapshot', fake_snap)
    monkeypatch.setattr(TX, 'restore', fake_restore)


def test_cloth_not_supported_respects_transactional_off_and_on(monkeypatch):
    from manipulation_agent.executors.local_manipulation import LocalOperation
    _patch_cloth_env(monkeypatch)

    # Off: cloth drop raises stage='released_placement_unconfirmed' -> result_unconfirmed (released)
    monkeypatch.delenv('MAS_PLACE_TRANSACTIONAL', raising=False)
    b_off = _ClothBackend(distance=1.0, on_top=False)
    with pytest.raises(SkillError) as info_off:
        LocalOperation(b_off, 'place_on_top', b_off.binding, 10, {}).run()
    assert info_off.value.stage == 'released_placement_unconfirmed'
    assert info_off.value.changed is True
    assert b_off.restored == 0 and b_off._get_held() is None
    receipt_off = factual_execution_error(info_off.value, 'place_on_top', 'empty')
    assert receipt_off['code'] == 'result_unconfirmed'
    assert receipt_off['stage'] == 'released'
    assert receipt_off['world_may_have_changed'] is True

    # On: cloth drop triggers transaction rollback -> not_placed, still holding item
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')
    b_on = _ClothBackend(distance=1.0, on_top=False)
    with pytest.raises(SkillError) as info_on:
        LocalOperation(b_on, 'place_on_top', b_on.binding, 10, {}).run()
    assert info_on.value.stage == 'released_rolled_back'
    assert info_on.value.changed is False
    assert b_on.restored == 1 and b_on._get_held() is not None
    receipt_on = factual_execution_error(info_on.value, 'place_on_top', 'holding_item')
    assert receipt_on['code'] == 'not_placed'
    assert receipt_on['hand'] == 'holding_item'
    assert receipt_on['world_may_have_changed'] is False


def test_local_wrapper_world_may_have_changed_on_rollback_depends_on_reposition(monkeypatch):
    from manipulation_agent.executors.local_manipulation import LocalOperation
    from manipulation_agent.runtime.robot_clock import RobotClock
    _patch_cloth_env(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')

    # Without prior local reposition: snapshot restored cleanly -> world_may_have_changed is False,
    # while robot_clock still charges the manipulation attempt (leaf stepped physics).
    b_inplace = _ClothBackend(distance=1.0, on_top=False)
    b_inplace.robot_clock = RobotClock()
    b_inplace.robot_clock.begin_action('place_on_top')
    with pytest.raises(SkillError) as info_inplace:
        LocalOperation(b_inplace, 'place_on_top', b_inplace.binding, 10, {}).run()
    b_inplace.robot_clock.settle_action('place_on_top', error=info_inplace.value)
    assert info_inplace.value.stage == 'released_rolled_back'
    assert info_inplace.value.changed is False
    assert b_inplace.robot_clock.manipulation_count == 1
    receipt_inplace = factual_execution_error(info_inplace.value, 'place_on_top', 'holding_item')
    assert receipt_inplace['code'] == 'not_placed'
    assert receipt_inplace['hand'] == 'holding_item'
    assert receipt_inplace['world_may_have_changed'] is False

    # With prior local reposition (distance > reach_m): base moved before snapshot -> world_may_have_changed is True.
    b_moved = _ClothBackend(distance=1.8, on_top=False)
    with pytest.raises(SkillError) as info_moved:
        LocalOperation(b_moved, 'place_on_top', b_moved.binding, 10, {}).run()
    assert info_moved.value.stage == 'released_rolled_back'
    assert info_moved.value.changed is True
    receipt_moved = factual_execution_error(info_moved.value, 'place_on_top', 'holding_item')
    assert receipt_moved['code'] == 'not_placed'
    assert receipt_moved['hand'] == 'holding_item'
    assert receipt_moved['world_may_have_changed'] is True
