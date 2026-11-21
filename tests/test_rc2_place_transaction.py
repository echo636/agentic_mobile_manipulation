"""rc2 MAS_PLACE_TRANSACTIONAL (2026-11-22): simulator-free tests of the decision logic and the _place_state wiring."""
import math

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import placement_transaction as TX
from manipulation_agent.observations.boundary import factual_execution_error


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_TRANSACTIONAL', raising=False)
    assert TX.enabled() is False
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on')
    assert TX.enabled() is True


def test_before_release_off_is_none(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_TRANSACTIONAL', raising=False)
    assert TX.before_release(object(), object(), object(), []) is None
    assert TX.evaluate(90., [90.], [False], None) == {}            # off: no extra checks at all


def test_tilt():
    s = 2 ** -.5
    assert TX.tilt_deg([0, 0, 0, 1]) == pytest.approx(0)
    assert TX.tilt_deg([s, 0, 0, s]) == pytest.approx(90)


def test_standing_item_that_falls_fails():
    before = {'held_standing': True, 'neighbours': [], 'contents': []}
    assert TX.evaluate(80., [], [], before) == {'upright_kept': False}
    assert TX.evaluate(10., [], [], before) == {'upright_kept': True}


def test_item_lying_when_picked_is_not_checked():
    before = {'held_standing': False, 'neighbours': [], 'contents': []}   # hotdog, log, fork lie by design
    assert TX.evaluate(90., [], [], before) == {'upright_kept': True}


def test_knocking_over_a_neighbour_fails():
    before = {'held_standing': False, 'neighbours': ['cup'], 'contents': []}
    assert TX.evaluate(0., [70.], [], before) == {'upright_kept': False}
    assert TX.evaluate(0., [5.], [], before) == {'upright_kept': True}


def test_carrier_contents_must_stay_on_it():
    before = {'held_standing': False, 'neighbours': [], 'contents': ['mug', 'ketchup']}
    assert TX.evaluate(0., [], [True, False], before)['carried_contents_kept'] is False
    assert TX.evaluate(0., [], [True, True], before)['carried_contents_kept'] is True


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
