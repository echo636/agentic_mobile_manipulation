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
