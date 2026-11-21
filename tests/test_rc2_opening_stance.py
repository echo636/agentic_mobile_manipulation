"""rc2 opening stance (2026-11-21): simulator-free tests of the generic geometry."""
import math

import pytest

from manipulation_agent.executors import opening_stance as OS


def test_switch_default_on(monkeypatch):
    monkeypatch.delenv('MAS_OPENING_STANCE', raising=False)
    assert OS.enabled() is True
    monkeypatch.setenv('MAS_OPENING_STANCE', 'off')
    assert OS.enabled() is False


def test_quat_rotate_matches_yaw():
    s = math.sin(math.pi / 4); c = math.cos(math.pi / 4)        # 90 deg about z
    v = OS._quat_rotate([0, 0, s, c], [1, 0, 0])
    assert v == pytest.approx([0, 1, 0], abs=1e-9)
    assert OS._quat_to_yaw([0, 0, s, c]) == pytest.approx(math.pi / 2)


def _fridge(yaw):
    """Body 0.6 (x) x 0.8 (y) centred at origin, front = +x in its own frame, rotated by yaw.
    Door hinge at the front-left corner, door panel 0.6 wide across the front, open 90 deg."""
    rot = lambda p: OS._rot_z(p, yaw) + [p[2]] if len(p) == 3 else OS._rot_z(p, yaw)
    hinge = rot([0.3, 0.4, 0.9])
    open_center = rot([0.3 + 0.3, 0.4, 0.9])                   # door swung out 90 deg: centre now in front of the hinge
    return hinge, open_center


@pytest.mark.parametrize('yaw', [0.0, math.pi / 2, 2.4, -1.1])
def test_door_normal_is_front_face_for_any_yaw(yaw):
    hinge, center = _fridge(yaw)
    closed = OS.closed_center('revolute', center, hinge, [0, 0, 1], math.pi / 2, [0, 0, 0.9])
    # closed panel centre should be on the front face (+x local) mid-width
    assert OS._rot_z([closed[0], closed[1]], -yaw) == pytest.approx([0.3, 0.1], abs=1e-6)
    n, why = OS.opening_normal('revolute', [0, 0, 1], closed, hinge, [0, 0, 0.9], yaw, [0.3, 0.4])
    assert n == pytest.approx(OS._rot_z([1.0, 0.0], yaw), abs=1e-6), why
    # a wide body (0.4 deep, 1.0 wide): the hinge corner alone would point at the side; the panel centre does not
    n2, _ = OS.opening_normal('revolute', [0, 0, 1], OS._rot_z([0.2, 0.0], yaw) + [0.9],
                              OS._rot_z([0.2, 0.5], yaw) + [0.9], [0, 0, 0.9], yaw, [0.2, 0.5])
    assert n2 == pytest.approx(OS._rot_z([1.0, 0.0], yaw), abs=1e-6)


def test_hinge_on_other_face_falls_back():
    n, why = OS.opening_normal('revolute', [0, 0, 1], [0.3, 0.0, 0.9], [-0.3, 0.4, 0.9], [0, 0, 0.9], 0.0, [0.3, 0.4])
    assert n is None and why.startswith('hinge_not_on_door_face')


def test_lid_and_vertical_slide_fall_back():
    assert OS.opening_normal('revolute', [0, 1, 0], [0, 0, 1], [0, 0.2, 1], [0, 0, .5], 0.0, [.3, .3])[0] is None
    assert OS.opening_normal('prismatic', [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, .5], 0.0, [.3, .3])[0] is None


def test_drawer_axis_signed_outward():
    closed = OS.closed_center('prismatic', [0.6, 0.0, 0.5], [0, 0, 0], [-1, 0, 0], 0.3, [0, 0, 0.5])
    assert closed == pytest.approx([0.3, 0.0, 0.5])
    n, why = OS.opening_normal('prismatic', [-1, 0, 0], closed, [0, 0, 0], [0, 0, 0.5], 0.0, [0.3, 0.4])
    assert n == pytest.approx([1.0, 0.0]) and why == 'drawer_slide_axis_outward'


def test_face_center_and_pick_front_cell():
    f = OS.face_center([0, 0], 0.0, [0.3, 0.4], [1.0, 0.0])
    assert f == pytest.approx([0.3, 0.0])
    pts = [(0.3, 1.3), (1.2, 0.05), (1.6, 0.0), (1.0, 0.5)]   # side, front 0.9, too far, off-axis
    d = [math.hypot(x, y) for x, y in pts]
    assert OS.pick_front_cell(pts, d, 2.0, f, [1.0, 0.0]) == 1
    assert OS.pick_front_cell(pts, d, 1.0, f, [1.0, 0.0]) is None   # front cell beyond reach of the click
