"""rc1 (2026-11-20) benchmark-side changes: articulation_hold and fine room lookup. Simulator-free unit tests;
the physical checks (T2 dryer door with garments, open after hold, nav replay) run as probes on the workstation."""
from types import SimpleNamespace

import numpy as np
import pytest

from manipulation_agent.executors import articulation_hold as AH


class FakeJoint:
    def __init__(self, name, q):
        self.joint_name, self.q, self.v, self.sets = name, q, 0.0, 0

    def get_state(self):
        return ([self.q], [self.v], [0.0])

    def set_pos(self, q):
        self.q = q; self.sets += 1

    def set_vel(self, v):
        self.v = v


class FakeToggle:
    def __init__(self, requires_closed, value=True, steps=0):
        self.requires_closed, self.value, self.robot_can_toggle_steps = requires_closed, value, steps
        self.obj = SimpleNamespace(updates=0)
        self.obj.state_updated = lambda: setattr(self.obj, 'updates', self.obj.updates + 1)

    def set_value(self, v):
        self.value = v


@pytest.fixture
def door(monkeypatch):
    j = FakeJoint('j_link_0', 0.0)
    monkeypatch.setattr(AH, '_joints', lambda obj: [j])
    monkeypatch.delenv('MAS_ARTICULATION_HOLD', raising=False)
    AH._HELD.clear()
    yield SimpleNamespace(name='clothes_dryer_xsuyua_0'), j
    AH._HELD.clear()


def test_held_door_pushed_open_is_put_back(door):
    obj, j = door
    rec = AH.lock_closed(SimpleNamespace(), obj)
    assert rec[0]['closed_position'] == 0.0 and AH.HOLD_TOLERANCE < 0.0785   # T2 dryer Open threshold
    j.q, j.v = 0.0669, 3.3            # the T2 habit failure: garments push the door open after close
    assert AH.enforce() == 1 and (j.q, j.v) == (0.0, 0.0)
    j.q = 0.003                        # within tolerance: left alone (no jitter)
    assert AH.enforce() == 0 and j.q == 0.003


def test_explicit_open_releases_the_hold(door):
    obj, j = door
    AH.lock_closed(SimpleNamespace(), obj)
    assert AH.unlock(SimpleNamespace(), obj) == [{'joint': 'j_link_0', 'released': True}]
    j.q = 1.5708
    assert AH.enforce() == 0 and j.q == 1.5708
    assert AH.unlock(SimpleNamespace(), obj) == []


def test_switch_off_restores_v12b_behaviour(door, monkeypatch):
    obj, j = door
    monkeypatch.setenv('MAS_ARTICULATION_HOLD', 'off')
    assert AH.lock_closed(SimpleNamespace(), obj) == [] and AH._HELD == {}
    assert AH.install_toggle_guard() is False


def test_finger_count_no_longer_toggles():
    s = FakeToggle(requires_closed=False, value=True, steps=4)
    for _ in range(10):
        assert AH.toggle_step(s, lambda: False) == 'held'
    assert s.value is True and s.robot_can_toggle_steps == 0


def test_opening_the_door_still_switches_a_running_appliance_off():
    s = FakeToggle(requires_closed=True, value=True)
    assert AH.toggle_step(s, lambda: True) == 'switched_off_door_open'
    assert s.value is False


def test_closed_door_keeps_running_appliance_on():
    s = FakeToggle(requires_closed=True, value=True)
    assert AH.toggle_step(s, lambda: False) == 'held' and s.value is True


def test_explicit_toggle_off_and_process_end_set_value_directly():
    # toggle_off and task processes call ToggledOn.set_value; the guard only replaces _update, so a direct set wins
    s = FakeToggle(requires_closed=True, value=True)
    s.set_value(False)
    assert AH.toggle_step(s, lambda: False) == 'held' and s.value is False


def test_fine_room_lookup_uses_native_resolution(tmp_path):
    import cv2
    from manipulation_agent.executors import omnigibson_rgb as R
    img = np.zeros((400, 400), dtype=np.uint8)
    img[:, :200] = 3            # x < 0 -> room 3
    img[:, 200:] = 4            # x >= 0 -> room 4
    (tmp_path / 'layout').mkdir()
    cv2.imwrite(str(tmp_path / 'layout' / 'floor_insseg_0.png'), img)
    scene = SimpleNamespace(seg_map=SimpleNamespace(map_default_resolution=0.01, scene_dir=str(tmp_path)))
    R._FINE_ROOM_MAPS.clear()
    out = R.fine_room_of_points(scene, [[-0.005, 0.0], [0.004, 0.0], [5.0, 0.0]])
    assert out.tolist() == [3, 4, 0]          # 1 cm either side of the boundary is resolved; outside the map = 0
    assert R.fine_room_of_points(scene, [[0.0, 0.0]], floor=1) is None
    assert R.fine_room_of_points(SimpleNamespace(), [[0.0, 0.0]]) is None


def test_detour_thresholds():
    from manipulation_agent.executors import omnigibson_rgb as R
    assert (R.DETOUR_RATIO, R.DETOUR_MIN_M, R.SAME_ROOM_WIDE_M) == (3.0, 30.0, 3.0)


def test_close_check_reads_state_after_clamp(door):
    # T2 door-hold probe trial 3 (hold on): one env step after close the garments had pushed the door to 0.0974 rad at
    # 9.4 rad/s (> Open threshold 0.0785) before the next pre-step clamp. clamp_now puts it back at once and reports the
    # pre-clamp angle for private diagnostics; the stability check then reads the held state.
    obj, j = door
    AH.lock_closed(SimpleNamespace(), obj)
    j.q, j.v = 0.0974, 9.37
    rec = AH.clamp_now(obj)
    assert rec == [{'joint': 'j_link_0', 'pre_clamp_position': 0.0974, 'closed_position': 0.0, 'clamped': True}]
    assert (j.q, j.v) == (0.0, 0.0)
    j.q = 0.003
    assert AH.clamp_now(obj)[0]['clamped'] is False and j.q == 0.003
    assert AH.clamp_now(SimpleNamespace(name='other_object')) == []


def test_clamp_now_without_hold_does_nothing(door, monkeypatch):
    obj, j = door
    monkeypatch.setenv('MAS_ARTICULATION_HOLD', 'off')
    AH.lock_closed(SimpleNamespace(), obj)          # switch off: nothing held (v12b behaviour)
    j.q = 0.2
    assert AH.clamp_now(obj) == [] and j.q == 0.2


# ---- rc1 A3 floor clearance (floor_clearance.py) ----
from manipulation_agent.executors import floor_clearance as FC


class Obj(SimpleNamespace):
    pass


def _obj(name, lo, hi, fixed=False, category='book'):
    return Obj(name=name, aabb=(np.array(lo, float), np.array(hi, float)), fixed_base=fixed, category=category)


def test_floor_item_detection():
    objs = [_obj('usbe_book', [5.7, 1.9, 0.0], [5.9, 2.05, 0.05]),                 # small, on the floor
            _obj('teddy', [1.0, 1.0, 0.02], [1.3, 1.3, 0.3]),                       # small, on the floor
            _obj('cup_on_table', [2.0, 2.0, 0.75], [2.1, 2.1, 0.85]),               # not on the floor
            _obj('chair', [3.0, 3.0, 0.0], [3.5, 3.5, 0.9], fixed=True, category='chair'),  # fixed
            _obj('rug_1', [0.0, 0.0, 0.0], [0.5, 0.5, 0.01], category='rug'),        # rug
            _obj('cart', [4.0, 4.0, 0.0], [4.9, 4.6, 0.9], category='cart'),        # larger than 0.6 m
            _obj('held_bottle', [6.0, 6.0, 0.0], [6.1, 6.1, 0.2])]
    names = [i[0] for i in FC.floor_items(objs, 0.0, exclude={'held_bottle'})]
    assert names == ['usbe_book', 'teddy']


def test_clearance_mask_and_target_band():
    items = [('usbe_book', (5.7, 1.9), (5.9, 2.05))]
    pts = np.array([[5.8, 2.0], [5.8, 2.4], [5.8, 2.7], [7.0, 2.0]])
    assert FC.blocked(pts, items, 0.45).tolist() == [True, True, False, False]
    m = FC.candidate_mask(pts, items, 0.45, target_xy=[5.8, 1.975], target_is_floor_item=True)
    assert m.tolist() == [False, False, True, True]          # 2.7 is 0.725 m away (>= 0.6) and outside the grown box
    assert FC.candidate_mask(pts, items, None).all()          # last tier = v12b selection


def test_no_floor_items_leaves_candidates_unchanged():
    # Same as v12b/A2 when no loose item is near: every tier admits every cell, so select() sees identical inputs.
    pts = np.random.default_rng(0).uniform(-5, 5, size=(200, 2))
    for _, grow in FC.tiers(0.4):
        assert FC.candidate_mask(pts, [], grow).all()


def test_tiers_and_switch(monkeypatch):
    assert [t for t, _ in FC.tiers(0.4)] == ['radius_plus_margin', 'radius', 'v12b_no_clearance']
    assert abs(FC.tiers(0.4)[0][1] - 0.5) < 1e-9 and FC.tiers(0.4)[1][1] == 0.4 and FC.tiers(0.4)[2][1] is None
    monkeypatch.setenv('MAS_FLOOR_CLEARANCE', 'off')
    assert FC.enabled() is False


class FakePhysx:
    def __init__(self):
        self.subs = []

    def subscribe_physics_on_step_events(self, fn, pre_step, order):
        self.subs.append((pre_step, fn)); return len(self.subs)

    def step(self, push):
        for pre, fn in self.subs:
            if pre: fn(0.0)
        push()                                   # contact physics inside the step
        for pre, fn in self.subs:
            if not pre: fn(0.0)


def test_door_never_reads_open_after_any_step_during_hold(door, monkeypatch):
    # coordinator 12:59: during a hold, every state read (ToggledOn requires_closed, evaluator) after an env step sees
    # the door closed. T2 probe: garments pushed the door to 0.0928-0.105 rad within one step (Open threshold 0.0785).
    obj, j = door
    monkeypatch.setattr(AH, '_SUBSCRIPTION', None)
    phys = FakePhysx()
    assert AH._subscribe(phys) is True and sorted(p for p, _ in phys.subs) == [False, True]
    AH.lock_closed(SimpleNamespace(), obj)
    for push in (0.0928, 0.105, 0.3, 0.004):
        phys.step(lambda: setattr(j, 'q', push))
        assert j.q <= AH.HOLD_TOLERANCE and not (j.q > 0.0785)     # Open would be read false
    AH.unlock(SimpleNamespace(), obj)
    phys.step(lambda: setattr(j, 'q', 1.5708))                    # explicit open: released, physics free
    assert j.q == 1.5708
    monkeypatch.setattr(AH, '_SUBSCRIPTION', None)
