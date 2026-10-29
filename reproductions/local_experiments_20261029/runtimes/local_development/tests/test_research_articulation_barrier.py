from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from manipulation_agent.executors.carry import ControlledCarry


def backend(protocol=None):
    obj = object.__new__(ControlledCarry)
    obj.research_instance = {'articulation_edit_barrier': protocol}
    obj.og = SimpleNamespace(sim=SimpleNamespace(
        _native_particle_edit_barrier=True, prepare_articulation_after_material_edit=Mock()))
    return obj


def test_research_open_and_close_force_checked_state_rebuild_with_reason():
    obj = backend('native_state_checked_before_articulation_edit_v1')
    obj._prepare_articulation_edit('open')
    obj.og.sim.prepare_articulation_after_material_edit.assert_called_once_with(
        force=True, reason='before_native_open')
    obj._prepare_articulation_edit('close')
    assert obj.og.sim.prepare_articulation_after_material_edit.call_args.kwargs == {
        'force': True, 'reason': 'before_native_close'}


def test_other_actions_do_not_rebuild_and_old_protocol_keeps_generation_gate():
    obj = backend('native_state_checked_before_articulation_edit_v1')
    obj._prepare_articulation_edit('toggle_on')
    obj.og.sim.prepare_articulation_after_material_edit.assert_not_called()
    obj = backend()
    obj._prepare_articulation_edit('open')
    obj.og.sim.prepare_articulation_after_material_edit.assert_called_once_with()


def test_uninstalled_runtime_stays_unchanged_and_bad_state_propagates():
    obj = backend('native_state_checked_before_articulation_edit_v1')
    obj.og.sim._native_particle_edit_barrier = False
    obj._prepare_articulation_edit('open')
    obj.og.sim.prepare_articulation_after_material_edit.assert_not_called()
    obj.og.sim._native_particle_edit_barrier = True
    obj.og.sim.prepare_articulation_after_material_edit.side_effect = RuntimeError('state changed')
    with pytest.raises(RuntimeError, match='state changed'):
        obj._prepare_articulation_edit('open')
