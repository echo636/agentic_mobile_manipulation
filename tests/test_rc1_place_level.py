"""rc1 A8 (2026-11-20): level a held object to its pre-grasp roll/pitch before place_on_top (simulator-free)."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R

from manipulation_agent.executors.surface_geometry import bottom_anchor, level_to_reference


def box(size, center=(0., 0., 0.), n=6):
    axes = [np.linspace(-s / 2, s / 2, n) for s in size]
    pts = np.array(np.meshgrid(*axes)).reshape(3, -1).T
    return pts + np.asarray(center)


def placed(points, quat):
    return points @ R.from_quat(quat).as_matrix().T


def anchor_xy_after(points, current, reference):
    q, rot, tilt = level_to_reference(current, reference)
    world = placed(points, current) @ rot.T
    return bottom_anchor(world)[:2], q, tilt


TRAY = box((0.54, 0.54, 0.033))                                      # flat-bottomed tray, root at its centre


def test_flat_tray_tilted_8_degrees_is_levelled_and_anchor_returns_to_centre():
    held = R.from_euler('xyz', [6., 5., 30.], degrees=True).as_quat()   # ~8 deg tilt, heading 30 deg
    before = bottom_anchor(placed(TRAY, held))[:2]
    assert np.hypot(*before) > 0.2                                       # today's failure: lowest corner, ~0.22 m
    xy, q, tilt = anchor_xy_after(TRAY, held, [0, 0, 0, 1])
    assert 7 < tilt < 9
    assert np.hypot(*xy) < 0.01
    roll, pitch, yaw = R.from_quat(q).as_euler('xyz', degrees=True)
    assert abs(roll) < 1e-6 and abs(pitch) < 1e-6 and yaw == pytest.approx(30., abs=1.)


def test_small_tilt_unchanged():
    held = R.from_euler('xyz', [2., 0., 10.], degrees=True).as_quat()
    q, rot, tilt = level_to_reference(held, [0, 0, 0, 1])
    assert tilt < 3 and np.allclose(rot, np.eye(3)) and np.allclose(q, held)


def test_lying_object_stays_lying():
    # a book or half log resting on its side before the grasp: the reference keeps that roll, never stands it up
    lying = R.from_euler('xyz', [90., 0., 0.], degrees=True)
    held = (R.from_euler('xyz', [0., 7., 50.], degrees=True) * lying).as_quat()
    q, rot, tilt = level_to_reference(held, lying.as_quat())
    assert 6 < tilt < 8
    z_axis = R.from_quat(q).as_matrix()[:, 1]                          # the book's local y was vertical when lying
    assert abs(abs(z_axis[2]) - 1.) < 1e-6


def test_wrong_reference_never_turns_over():
    held = R.from_euler('xyz', [170., 0., 0.], degrees=True).as_quat()  # far from upright: refuse
    q, rot, tilt = level_to_reference(held, [0, 0, 0, 1])
    assert tilt > 45 and np.allclose(rot, np.eye(3))


def test_lamp_with_base_anchor_is_base_centre():
    base = box((0.20, 0.20, 0.02), (0., 0., 0.01))
    shade = box((0.35, 0.35, 0.25), (0.05, 0., 0.45))                  # off-centre shade, higher up
    lamp = np.vstack([base, shade])
    held = R.from_euler('xyz', [0., 10., 0.], degrees=True).as_quat()
    xy, _, _ = anchor_xy_after(lamp, held, [0, 0, 0, 1])
    assert np.hypot(*xy) < 0.01                                          # base centre, not the shade


def test_cylinder_and_offcentre_object():
    t = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    ring = np.stack([0.04 * np.cos(t), 0.04 * np.sin(t)], 1)
    cyl = np.vstack([np.c_[ring, np.full(len(t), z)] for z in (0., 0.05, 0.1)])
    held = R.from_euler('xyz', [5., 4., 0.], degrees=True).as_quat()
    xy, _, _ = anchor_xy_after(cyl, held, [0, 0, 0, 1])
    assert np.hypot(*xy) < 0.005
    # L-shaped object: foot at x in [0, 0.3], a raised arm towards -x; the anchor is the foot's centre
    foot = box((0.3, 0.1, 0.02), (0.15, 0., 0.01)); arm = box((0.3, 0.1, 0.02), (-0.15, 0., 0.2))
    xy, _, _ = anchor_xy_after(np.vstack([foot, arm]), R.from_euler('xyz', [0., 6., 0.], degrees=True).as_quat(),
                               [0, 0, 0, 1])
    assert abs(xy[0] - 0.15) < 0.01 and abs(xy[1]) < 0.01
