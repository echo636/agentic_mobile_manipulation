import numpy as np

from manipulation_agent.executors.initial_reachability import metric_reachability_maps


def test_native_state_factory_aliases_use_metric_maps_and_restore_on_error():
    import sys
    from types import ModuleType, SimpleNamespace
    from unittest.mock import patch
    import pytest
    import torch
    import cv2  # Load the extension before patch.dict restores sys.modules.
    from manipulation_agent.executors.initial_reachability import metric_initial_reachability

    floor = torch.zeros((101, 101), dtype=torch.uint8)
    floor[:, :51] = 255
    trav = SimpleNamespace(floor_map=[floor], map_resolution=.05, default_erosion_radius=.4,
        world_to_map=lambda pos: torch.tensor([50, 66]))
    target = SimpleNamespace(name='table', scene=SimpleNamespace(robots=[]))
    def original_factory(*args, **kwargs):
        return dict(trav_map=trav, reachability_map=floor, eroded_trav_map=floor)
    def original_check(pos, obj, predicate, context):
        return bool(context['reachability_map'][50, 66] == 255)
    state_utils = SimpleNamespace(get_reachability_sampling_context=original_factory,
        is_pose_reachable_for_predicate=original_check, m=SimpleNamespace(ARM_LENGTH_XY=.8))
    aliases = [SimpleNamespace(get_reachability_sampling_context=original_factory) for _ in range(3)]
    modules = {'omnigibson': ModuleType('omnigibson'),
        'omnigibson.utils': SimpleNamespace(object_state_utils=state_utils),
        'omnigibson.object_states': SimpleNamespace(inside=aliases[0], on_top=aliases[1], under=aliases[2])}
    records = []
    with patch.dict(sys.modules, modules):
        with pytest.raises(RuntimeError, match='end scope'), metric_initial_reachability(records.append):
            for module in aliases:
                context = module.get_reachability_sampling_context(target, 'onTop')
                assert state_utils.is_pose_reachable_for_predicate(torch.tensor([0.,0.,.8]), target, 'onTop', context)
            raise RuntimeError('end scope')
    assert len(records) == 3
    assert state_utils.get_reachability_sampling_context is original_factory
    assert state_utils.is_pose_reachable_for_predicate is original_check
    assert all(module.get_reachability_sampling_context is original_factory for module in aliases)


def test_wall_reach_matches_metres_and_standing_clearance():
    floor = np.zeros((101, 101), dtype=np.uint8)
    floor[:, :51] = 255
    standing, reachable = metric_reachability_maps(floor, .05, .4, .8)
    assert standing[50, 42] == 255
    assert standing[50, 43] == 0
    # A wall at column 50: arm reach extends 16 cells, not eight.
    assert reachable[50, 66] == 255
    assert reachable[50, 67] == 0


def test_metric_windows_are_symmetric_and_do_not_open_narrow_corridor():
    floor = np.zeros((101, 101), dtype=np.uint8)
    floor[30:71, 30:71] = 255
    standing, reachable = metric_reachability_maps(floor, .05, .4, .8)
    for result in (standing, reachable):
        np.testing.assert_array_equal(result, result[::-1, ::-1])
    floor[:] = 0
    floor[:, 47:54] = 255  # 35 cm corridor cannot fit the 80 cm map footprint.
    standing, reachable = metric_reachability_maps(floor, .05, .4, .8)
    assert not standing.any()
    assert not reachable.any()
