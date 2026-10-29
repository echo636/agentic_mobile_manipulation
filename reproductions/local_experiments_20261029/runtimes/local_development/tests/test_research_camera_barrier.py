from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from manipulation_agent.executors.omnigibson_rgb import RGBBackend


def backend(protocol=None):
    obj = object.__new__(RGBBackend)
    obj.research_instance = {'camera_initialization_barrier': protocol}
    obj.og = SimpleNamespace(sim=SimpleNamespace(prepare_articulation_after_material_edit=Mock()))
    return obj


def test_camera_barrier_is_opt_in_and_never_restarts_later_observations():
    obj = backend('native_state_checked_post_rgb_startup_v1')
    obj._prepare_research_camera_physics()
    obj._prepare_research_camera_physics()
    obj.og.sim.prepare_articulation_after_material_edit.assert_called_once_with(
        force=True, reason='initial_rgb_capture_complete')


def test_normal_episodes_do_not_restart_physics_after_capture():
    obj = backend()
    obj._prepare_research_camera_physics()
    obj.og.sim.prepare_articulation_after_material_edit.assert_not_called()


def test_failed_barrier_does_not_mark_startup_as_prepared():
    obj = backend('native_state_checked_post_rgb_startup_v1')
    obj.og.sim.prepare_articulation_after_material_edit.side_effect = RuntimeError('state changed')
    with pytest.raises(RuntimeError, match='state changed'):
        obj._prepare_research_camera_physics()
    assert not getattr(obj, '_research_camera_physics_prepared', False)
