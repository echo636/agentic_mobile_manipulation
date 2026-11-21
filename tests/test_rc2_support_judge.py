"""rc2 MAS_SUPPORT_JUDGE (2026-11-22): simulator-free tests of the placement criterion."""
import pytest

from manipulation_agent.executors import support_judge as SJ

GOOD = {'released': True, 'support_ray_hits_selected_object': True, 'upward_support': True, 'bottom_near_support': True,
        'selected_shelf_height': True, 'selected_surface_neighborhood': True, 'selected_contact_consistent': True,
        'settled': True, 'not_supported_by_robot': True}


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'on')


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv('MAS_SUPPORT_JUDGE', raising=False)
    assert SJ.enabled() is False
    slid = {**GOOD, 'selected_surface_neighborhood': False}
    assert SJ.judge_on_top(slid, True, 'tray', False, None) == (False, None)      # off = 1d19707
    assert SJ.judge_inside(False, True, True, True) == (False, None)


def test_owner_name():
    assert SJ.owner_name('/World/scene_0/hl2_plate1/base_link/collisions') == 'hl2_plate1'
    assert SJ.owner_name(None) is None and SJ.owner_name('/World/ground') is None


def test_case1_slid_on_same_target(on):
    for failed in ('selected_surface_neighborhood', 'selected_shelf_height'):
        assert SJ.judge_on_top({**GOOD, failed: False}, True, 'hl2_tray', True, None) == (True, 'native_on_top_same_target')


def test_case3_on_the_support_under_a_clicked_item(on):
    checks = {**GOOD, 'support_ray_hits_selected_object': False, 'selected_contact_consistent': False}
    # hotdog clicked onto the half apple lying on plate 1, ends on plate 1
    assert SJ.judge_on_top(checks, False, 'hl2_plate1', True, 'hl2_plate1') == (True, 'on_support_under_clicked_item')
    # a fixed target (table) on the floor: ending on the floor is never success
    assert SJ.judge_on_top(checks, False, 'floors_x', False, 'floors_x') == (False, None)
    # ended somewhere else
    assert SJ.judge_on_top(checks, False, 'countertop', True, 'hl2_plate1') == (False, None)


def test_not_rescued_when_unsettled_held_by_robot_or_no_upward_support(on):
    for bad in ('settled', 'released', 'not_supported_by_robot', 'upward_support', 'bottom_near_support'):
        assert SJ.judge_on_top({**GOOD, bad: False}, True, 'hl2_tray', True, None) == (False, None), bad


def test_edge_hanging_item_stays_failed(on):
    # fork over a tray edge: native OnTop false, the support under it is the counter, not the tray's support plate
    checks = {**GOOD, 'support_ray_hits_selected_object': False}
    assert SJ.judge_on_top(checks, False, 'countertop_kelker_0', True, 'bar_udatjt_0') == (False, None)


def test_transaction_checks_veto(on):
    assert SJ.judge_on_top({**GOOD, 'selected_shelf_height': False}, True, 't', True, None,
                           {'upright_kept': False}) == (False, None)


def test_case2_inside_flat_target(on):
    assert SJ.judge_inside(False, True, True, True) == (True, 'flat_target_on_top')
    assert SJ.judge_inside(False, True, True, False) == (False, None)          # a real container: unchanged
    assert SJ.judge_inside(False, False, True, True) == (False, None)          # not on it
    assert SJ.judge_inside(False, True, False, True) == (False, None)          # not settled
    assert SJ.judge_inside(False, True, True, True, {'upright_kept': False}) == (False, None)


def test_flat_target_rule():
    assert SJ.is_flat_target(False, 0.03) is True                               # tray / plate
    assert SJ.is_flat_target(False, 0.30) is False                              # box, basket
    assert SJ.is_flat_target(True, 0.03) is False                               # fixed furniture


def test_case5_floor_touching_only_structure(on):
    checks = {**GOOD, 'selected_contact_consistent': False}
    assert SJ.judge_on_top(checks, False, 'floors_a', False, None, target_is_floor=True,
                           contact_categories=['floors', 'fixed_window']) == (True, 'floor_touching_only_structure')
    # touching a movable object or another piece of furniture still fails
    assert SJ.judge_on_top(checks, False, 'floors_a', False, None, target_is_floor=True,
                           contact_categories=['floors', 'chair']) == (False, None)
    assert SJ.judge_on_top(checks, False, 'floors_a', False, None, target_is_floor=True,
                           contact_categories=['floors', None]) == (False, None)
    # only for floor targets, and only when the contact check is the sole failure
    assert SJ.judge_on_top(checks, False, 'x', False, None, target_is_floor=False,
                           contact_categories=['floors']) == (False, None)
    assert SJ.judge_on_top({**checks, 'support_ray_hits_selected_object': False}, False, 'floors_b', False, None,
                           target_is_floor=True, contact_categories=['floors']) == (False, None)
