"""rc2 MAS_SUPPORT_JUDGE (2026-11-22): simulator-free tests of the placement criterion."""
import pytest

from manipulation_agent.executors import support_judge as SJ

GOOD = {'released': True, 'support_ray_hits_selected_object': True, 'upward_support': True, 'bottom_near_support': True,
        'selected_shelf_height': True, 'selected_surface_neighborhood': True, 'selected_contact_consistent': True,
        'settled': True, 'not_supported_by_robot': True}


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'on')


def test_switch_default_on_and_off(monkeypatch):
    monkeypatch.delenv('MAS_SUPPORT_JUDGE', raising=False)
    assert SJ.enabled() is True
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'off')
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


def test_native_gate(on):
    assert SJ.native_gate(True, True, False, None) == (True, None)
    assert SJ.native_gate(True, False, True, None) == (True, None)                 # interior shelf: Inside counts
    assert SJ.native_gate(True, False, False, None) == (False, 'native_predicate_false')   # open lower shelf, E1
    assert SJ.native_gate(True, False, False, 'on_support_under_clicked_item') == (True, None)
    assert SJ.native_gate(True, False, False, 'floor_touching_only_structure') == (True, None)
    assert SJ.native_gate(False, True, True, None) == (False, None)


def test_native_gate_off(monkeypatch):
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'off')
    assert SJ.native_gate(True, False, False, None) == (True, None)


@pytest.mark.parametrize('sj,tx,expected', [('off', 'off', 'passed'), ('on', 'off', 'result_unconfirmed'),
                                            ('off', 'on', 'passed'), ('on', 'on', 'not_placed')])
def test_four_switch_combinations_native_false(monkeypatch, sj, tx, expected):
    """Executor checks pass but the native predicate is false (open lower shelf / E1)."""
    from manipulation_agent.contracts import SkillError
    from manipulation_agent.executors import placement_transaction as TX
    from manipulation_agent.executors.carry import ControlledCarry
    from manipulation_agent.observations.boundary import factual_execution_error
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', sj); monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', tx)
    monkeypatch.setattr(TX, 'snapshot', lambda b: 'snap')
    monkeypatch.setattr(TX, 'restore', lambda b, s: (True, None))

    class Fake:
        records = []
        primitives = type('P', (), {'_empty_action': staticmethod(lambda: None)})()
        def _get_held(self): return 'book'
        _carry_contents = []
        def _placement_record(self, r): self.records.append(r)
        def _step(self, a): pass
        def _place_state_once(self, *a, **k):
            ok, _ = SJ.native_gate(True, False, False, None)      # what release_and_check decides
            if not ok:
                raise SkillError('postcondition_error', 'x', changed=True, stage='released_placement_unconfirmed')
            return 'placed'
    try:
        ControlledCarry._place_state(Fake(), 'place_on_top', 'desk', 10, None)
        got = 'passed'
    except SkillError as exc:
        got = factual_execution_error(exc, 'place_on_top', 'holding_item' if tx == 'on' else 'empty')['code']
    assert got == expected


def _fake_obj(name, *, category=None, fixed_base=False, aabb=None, links=None, states=None):
    return type('Obj', (), {
        'name': name,
        'category': category,
        'fixed_base': fixed_base,
        'aabb': aabb or ([0.0, 0.0, 0.8], [0.08, 0.08, 0.88]),
        'links': links if links is not None else {'base': type('L', (), {'prim_path': f'/World/scene_0/{name}/base',
                                                                         'is_meta_link': False})()},
        'states': states if states is not None else {},
    })()


def test_allows_under_support_rescue_blocks_carriers_containers_and_large_supports(monkeypatch):
    import sys
    from types import ModuleType

    og = ModuleType('omnigibson')
    og_states = ModuleType('omnigibson.object_states')
    og_states.Inside = 'Inside'
    og_states.Contains = 'Contains'
    og_states.Open = 'Open'
    monkeypatch.setitem(sys.modules, 'omnigibson', og)
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', og_states)

    # Carriers / plates / bowls / large flat surfaces / containers / fixed furniture -> False
    tray = _fake_obj('ggpp_tray', category='tray', aabb=([0.0, 0.0, 0.8], [0.76, 0.71, 0.83]))
    plate = _fake_obj('hl2_plate1', category='plate', aabb=([0.0, 0.0, 0.8], [0.24, 0.24, 0.82]))
    small_bowl = _fake_obj('small_bowl', category='bowl', aabb=([0.0, 0.0, 0.8], [0.15, 0.14, 0.88]))
    large_flat = _fake_obj('wooden_slab', category='slab', aabb=([0.0, 0.0, 0.8], [0.25, 0.19, 0.83]))
    fixed_small = _fake_obj('fixed_post', category='post', fixed_base=True, aabb=([0.0, 0.0, 0.8], [0.06, 0.06, 0.88]))
    openfillable_caddy = _fake_obj(
        'mini_caddy', category='caddy', aabb=([0.0, 0.0, 0.8], [0.12, 0.12, 0.88]),
        links={'vol': type('L', (), {'is_meta_link': True, 'meta_link_type': 'openfillable'})()},
    )
    openable_case = _fake_obj(
        'mini_case', category='case', aabb=([0.0, 0.0, 0.8], [0.12, 0.12, 0.88]),
        states={'Inside': object(), 'Open': object()},
    )
    contains_vessel = _fake_obj(
        'mini_vessel', category='vessel', aabb=([0.0, 0.0, 0.8], [0.12, 0.12, 0.88]),
        states={'Inside': object(), 'Contains': object()},
    )
    for blocked in (None, tray, plate, small_bowl, large_flat, fixed_small,
                    openfillable_caddy, openable_case, contains_vessel):
        assert SJ.allows_under_support_rescue(blocked) is False, getattr(blocked, 'name', None)

    # Small movable non-container items (even with default OmniGibson Inside state!) -> True
    half_apple = _fake_obj('half_apple_1', category='half_apple', aabb=([0.0, 0.0, 0.8], [0.08, 0.08, 0.84]),
                           states={'Inside': object()})
    small_cup = _fake_obj('small_cup', category='cup', aabb=([0.0, 0.0, 0.8], [0.08, 0.08, 0.88]),
                          states={'Inside': object()})
    fork = _fake_obj('hl2_fork1', category='fork', aabb=([0.0, 0.0, 0.8], [0.175, 0.08, 0.834]),
                     states={'Inside': object()})
    for allowed in (half_apple, small_cup, fork):
        assert SJ.allows_under_support_rescue(allowed) is True, allowed.name


def test_support_under_blocks_log_sliding_off_tray_onto_bench(monkeypatch, on):
    import sys
    from types import ModuleType
    import torch

    hits = []
    sampling = ModuleType('omnigibson.utils.sampling_utils')
    sampling.raytest = lambda start, end, ignore_bodies=None: (
        hits.append(ignore_bodies) or {'hit': True, 'rigidBody': '/World/scene_0/ggpp_garage_bench/base_link'}
    )
    monkeypatch.setitem(sys.modules, 'omnigibson', ModuleType('omnigibson'))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils', ModuleType('omnigibson.utils'))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.sampling_utils', sampling)

    backend = type('B', (), {'torch': torch, 'robot': _fake_obj('robot')})()
    tray = _fake_obj('ggpp_tray', category='tray', aabb=([-4.8, 6.2, 0.80], [-4.1, 6.9, 0.83]))
    # T3 #90 regression: tray must never get an under-support rescue target, so log sliding onto bench fails
    target_support = SJ.support_under(backend, tray)
    assert target_support is None
    assert hits == []
    checks = {**GOOD, 'support_ray_hits_selected_object': False, 'selected_contact_consistent': False}
    assert SJ.judge_on_top(checks, False, 'ggpp_garage_bench', True, target_support) == (False, None)

    # Small half_apple lying on a plate still gets rescued to the plate underneath
    sampling.raytest = lambda start, end, ignore_bodies=None: {
        'hit': True, 'rigidBody': '/World/scene_0/hl2_plate1/base_link'
    }
    half_apple = _fake_obj('half_apple_1', category='half_apple', aabb=([0.0, 0.0, 0.82], [0.08, 0.08, 0.86]))
    apple_support = SJ.support_under(backend, half_apple)
    assert apple_support == 'hl2_plate1'
    assert SJ.judge_on_top(checks, False, 'hl2_plate1', True, apple_support) == (True, 'on_support_under_clicked_item')
