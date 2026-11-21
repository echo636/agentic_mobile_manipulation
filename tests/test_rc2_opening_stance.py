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


def test_drop_down_oven_door_is_front_and_lid_is_not():
    # oven (T3 kiln): hinge axis horizontal, closed door centre on the -y face at mid height, hinge at the bottom edge
    n, why = OS.opening_normal('revolute', [1, 0, 0], [-6.02, 6.162, 0.343], [-6.02, 6.181, 0.125],
                               [-6.02, 6.506, 0.364], -math.pi / 2, [0.35, 0.361], half_z=0.36)
    assert n == pytest.approx([0.0, -1.0], abs=1e-6) and why.startswith('drop_down_door')
    # toolbox lid: closed panel centre on top
    n2, why2 = OS.opening_normal('revolute', [0, -1, 0], [19.883, 6.898, 1.054], [19.757, 6.898, 0.984],
                                 [19.885, 6.898, 0.883], 0.0, [0.134, 0.309], half_z=0.17)
    assert n2 is None and why2 == 'lid_horizontal_hinge'


def test_sliding_room_door_is_not_a_drawer():
    # sliding door: slide axis along the long side of a thin frame (T2 study_door)
    n, why = OS.opening_normal('prismatic', [0, -1, 0], [20.203, 14.664, 1.162], [20.203, 13.848, 1.163],
                               [20.203, 14.243, 2.31], 0.0, [0.05, 0.893])
    assert n is None and why.startswith('thin_body_no_interior')            # caught by the thin-body rule first
    # a thicker body with a panel sliding along its long side (wardrobe sliding door) is still not a drawer
    n3, why3 = OS.opening_normal('prismatic', [0, -1, 0], [0.0, 0.3, 1.0], [0.0, -0.3, 1.0], [0.0, 0.0, 1.0],
                                 0.0, [0.3, 0.9])
    assert n3 is None and why3 == 'sliding_panel_along_long_side'
    # T2 drawer cabinet: slide along the short side -> outward
    n2, _ = OS.opening_normal('prismatic', [-1, 0, 0], [19.854, 7.491, 0.289], [19.841, 7.497, 0.241],
                              [19.89, 7.75, 0.277], math.pi, [0.202, 0.561])
    assert n2 == pytest.approx([-1.0, 0.0], abs=1e-6)


def test_thin_room_door_falls_back():
    # T3 garage door: leaf 0.1 m deep, revolute with a vertical hinge -- no interior, stays on the rc1 rule
    n, why = OS.opening_normal('revolute', [0, 0, 1], [-3.887, 2.329, 1.0], [-3.887, 2.85, 1.0],
                               [-3.887, 2.379, 1.0], -math.pi / 2, [0.05, 0.47])
    assert n is None and why.startswith('thin_body_no_interior')
    # the microwave (0.40 m deep) is still a container
    n2, _ = OS.opening_normal('revolute', [0, 0, -1], [4.41, 0.747, 1.044], [4.394, 0.555, 1.043],
                              [4.231, 0.798, 1.038], math.radians(-1.6), [0.199, 0.24])
    assert n2 is not None


def test_open_drop_down_door_closed_centre_uses_body_box():
    # oven open 90 deg: panel lies flat in front of the bottom hinge; the wrong sense would hang it below the hinge
    pivot = [0.0, -0.36, 0.125]; center = [0.0, -0.36 - 0.22, 0.125]           # panel 0.44 tall lying outward (-y)
    box = ([-0.35, -0.36, 0.0], [0.35, 0.36, 0.73])
    c = OS.closed_center('revolute', center, pivot, [1, 0, 0], math.pi / 2, [0, 0, 0.364], body_box=box)
    assert c == pytest.approx([0.0, -0.36, 0.345], abs=1e-6)                  # upright on the front face
    n, why = OS.opening_normal('revolute', [1, 0, 0], c, pivot, [0, 0, 0.364], 0.0, [0.35, 0.36], half_z=0.365)
    assert n == pytest.approx([0.0, -1.0], abs=1e-6) and why.startswith('drop_down_door')


def test_body_box_ignores_open_door_sweep():
    from types import SimpleNamespace as NS
    base = NS(aabb=([0.0, 0.0, 0.0], [0.6, 0.6, 0.9]))
    door = NS(aabb=([0.6, -0.5, 0.0], [1.2, 0.6, 0.9]))                 # swung open in front of the body
    washer = NS(aabb=([0.0, -0.5, 0.0], [1.2, 0.6, 0.9]), root_link_name='base_link',
                links={'base_link': base, 'door': door})
    lo, hi = OS._body_box(washer)
    assert (lo, hi) == ([0.0, 0.0, 0.0], [0.6, 0.6, 0.9])
    floor_under_door = [0.9, 0.0, 0.05]
    assert OS.point_in_box(floor_under_door, *OS._body_box(washer)) is False   # stays a floor click
    assert OS.point_in_box(floor_under_door, *OS._box(washer)) is True         # the whole-object box would swallow it
    assert OS.point_in_box([0.3, 0.3, 0.05], *OS._body_box(washer)) is True    # the machine's own foot
    assert OS.is_thin(washer) is False
