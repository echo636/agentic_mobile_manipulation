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
    held = R.from_euler('xyz', [0.3, 0., 10.], degrees=True).as_quat()
    q, rot, tilt = level_to_reference(held, [0, 0, 0, 1])
    assert tilt < 0.5 and np.allclose(rot, np.eye(3)) and np.allclose(q, held)


def test_one_and_a_half_degrees_is_levelled():
    # 2026-11-21: thepatio tray tilted 1.6-2.8 deg fell / missed its surface with the old 3 deg floor
    held = R.from_euler('xyz', [1.5, 0., 10.], degrees=True).as_quat()
    q, rot, tilt = level_to_reference(held, [0, 0, 0, 1])
    assert 1.4 < tilt < 1.6 and not np.allclose(rot, np.eye(3))
    roll, pitch, yaw = R.from_quat(q).as_euler('xyz', degrees=True)
    assert abs(roll) < 1e-6 and abs(pitch) < 1e-6 and yaw == pytest.approx(10., abs=0.1)


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


class _T:
    """Minimal xyzw transform shim (torch in, torch out) for assembly_orientations."""
    @staticmethod
    def quat2mat(q):
        import torch
        return torch.as_tensor(R.from_quat(np.asarray(q, dtype=float)).as_matrix(), dtype=torch.float32)

    @staticmethod
    def euler2quat(e):
        import torch
        return torch.as_tensor(R.from_euler('xyz', np.asarray(e, dtype=float)).as_quat(), dtype=torch.float32)

    @staticmethod
    def quat_multiply(a, b):
        import torch
        return torch.as_tensor((R.from_quat(np.asarray(a, dtype=float)) * R.from_quat(np.asarray(b, dtype=float))).as_quat(),
                               dtype=torch.float32)


class _Held:
    orientations = None
    links = {}


class _Backend:
    def __init__(self, pregrasp):
        self._pregrasp_orientations = pregrasp


def test_place_inside_tilted_half_log_gets_level_proposal_first(monkeypatch):
    import torch
    from manipulation_agent.executors.inside_sampling import a8_level_quat, assembly_orientations
    monkeypatch.delenv('MAS_PLACE_LEVEL', raising=False)
    held = _Held()
    lying = R.from_euler('xyz', [90., 0., 0.], degrees=True)                       # a half log resting on its side
    in_hand = torch.as_tensor((R.from_euler('xyz', [0., 12., 40.], degrees=True) * lying).as_quat(), dtype=torch.float32)
    backend = _Backend({id(held): lying.as_quat().tolist()})
    level = a8_level_quat(backend, held, in_hand, torch)
    assert level is not None
    rows = assembly_orientations(held, in_hand, torch, _T, level_quat=level)
    labels = [label for label, _, _ in rows]
    assert labels[0] == 'a8_level_pose' and 'carried_pose' in labels                 # level first, in-hand kept
    assert labels.index('carried_pose') > max(i for i, l in enumerate(labels) if l == 'a8_level_pose')
    monkeypatch.setenv('MAS_PLACE_LEVEL', 'off')
    assert a8_level_quat(backend, held, in_hand, torch) is None


def test_empty_orientation_table_counts_as_unauthored(monkeypatch):
    # imported assets (trays) carry an empty or all-zero orientation table; A8 must still level them
    import torch
    from manipulation_agent.executors.inside_sampling import a8_level_quat
    monkeypatch.delenv('MAS_PLACE_LEVEL', raising=False)
    tilted = torch.as_tensor(R.from_euler('xyz', [7., 0., 20.], degrees=True).as_quat(), dtype=torch.float32)
    for table in ({}, {'a': {'rotation': [0, 0, 0, 1], 'prob': 0.}}):
        held = _Held(); held.orientations = table
        assert a8_level_quat(_Backend({}), held, tilted, torch) is not None
    held = _Held(); held.orientations = {'a': {'rotation': [0, 0, 0, 1], 'prob': 1.}}
    assert a8_level_quat(_Backend({}), held, tilted, torch) is None              # authored poses: native path


def test_level_to_reference_unrestricted_corrects_large_tilts_and_preserves_yaw():
    # rc2 B7: default cap remains 45 deg (loaded carriers), while maximum_tilt_degrees=None lifts the cap
    # for unladen upright single items even at 73 / 105 / 170 deg in-hand tilt, preserving world heading.
    for tilt_deg_in in (73., 105., 170.):
        held = (R.from_euler('z', 35., degrees=True) * R.from_euler('x', tilt_deg_in, degrees=True)).as_quat()
        q_capped, rot_capped, tilt_capped = level_to_reference(held, [0., 0., 0., 1.])
        assert tilt_capped > 45. and np.allclose(rot_capped, np.eye(3)) and np.allclose(q_capped, held)

        q_free, rot_free, tilt_free = level_to_reference(held, [0., 0., 0., 1.], maximum_tilt_degrees=None)
        assert tilt_free == pytest.approx(tilt_deg_in, abs=0.5)
        assert not np.allclose(rot_free, np.eye(3))
        roll, pitch, yaw = R.from_quat(q_free).as_euler('xyz', degrees=True)
        assert abs(roll) < 1e-4 and abs(pitch) < 1e-4
        assert yaw == pytest.approx(35., abs=1.0)


def test_surface_candidates_b7_unladen_standing_lifts_45_deg_cap_while_loaded_keeps_it(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    import torch
    from manipulation_agent.executors import placement_stable as ST
    from manipulation_agent.executors.placement import CheckedPlacement

    sampling_utils = ModuleType('omnigibson.utils.sampling_utils')
    sampling_utils.raytest = lambda start, end, ignore_bodies=(): {
        'hit': True, 'distance': 0.03, 'rigidBody': '/target/base_link',
        'normal': [0., 0., 1.], 'position': [float(start[0]), float(start[1]), 0.80],
    }
    transform_utils = ModuleType('omnigibson.utils.transform_utils')
    transform_utils.euler2quat = _T.euler2quat
    transform_utils.quat_multiply = _T.quat_multiply
    monkeypatch.setitem(sys.modules, 'omnigibson', ModuleType('omnigibson'))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils', ModuleType('omnigibson.utils'))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.sampling_utils', sampling_utils)
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.transform_utils', transform_utils)
    monkeypatch.delenv('MAS_PLACE_LEVEL', raising=False)

    bottle_verts = box((0.06, 0.06, 0.22), (0., 0., 0.))

    def make_obj(name, quat):
        link = SimpleNamespace(prim_path=f'/{name}/base_link', is_meta_link=False)
        return SimpleNamespace(
            name=name,
            orientations=None,
            links={'base_link': link},
            get_position_orientation=lambda q=quat: (
                torch.tensor([0., 0., 1.0], dtype=torch.float32),
                torch.tensor(q, dtype=torch.float32),
            ),
        )

    def make_backend(held_obj, pregrasp, *, particle_payload=()):
        records = []
        sim = SimpleNamespace(get_physics_dt=lambda: 1.0 / 60.0)
        b = SimpleNamespace(
            torch=torch,
            og=SimpleNamespace(sim=sim),
            robot=SimpleNamespace(links={}),
            _pregrasp_orientations={id(held_obj): pregrasp} if pregrasp is not None else {},
            _placement_particle_payload=(held_obj, list(particle_payload)),
            _collision_vertices=lambda obj: placed(bottle_verts, obj.get_position_orientation()[1].numpy()) + np.array([0., 0., 1.0]),
            _placement_record=lambda row: records.append(row),
            _open_rim_candidates=lambda *a, **k: [],
            records=records,
        )
        return b

    target = make_obj('target', [0., 0., 0., 1.])
    point = torch.tensor([1.0, 2.0, 0.80], dtype=torch.float32)

    # 1) Unladen upright bottle tilted 75 deg / 105 deg in hand -> levelled upright, upright_kept=True
    for bad_tilt in (75., 105.):
        in_hand = (R.from_euler('z', 25., degrees=True) * R.from_euler('x', bad_tilt, degrees=True)).as_quat()
        held = make_obj('cdkr_juice_2', in_hand)
        pregrasp = R.from_euler('x', 0.07, degrees=True).as_quat().tolist()
        b = make_backend(held, pregrasp)
        cands = CheckedPlacement._surface_candidates(b, held, target, point, payload=(), offsets=[(0., 0.)])
        assert cands
        cand_quat = cands[0]['pose'][1].numpy()
        assert ST.tilt_deg(cand_quat) < 1.0
        assert ST.evaluate(ST.tilt_deg(cand_quat), [], [], {'held_standing': True, 'neighbours': [], 'contents': []}) == {
            'upright_kept': True
        }
        a8 = [r for r in b.records if r['status'] == 'rc1_A8_level'][-1]
        assert a8['applied'] is True and a8['unrestricted_upright'] is True

    # 2) Loaded carrier (rigid payload) tilted 75 deg -> 45-deg cap stays in force
    tilted_75 = R.from_euler('x', 75., degrees=True).as_quat()
    tray = make_obj('cdkr_tray', tilted_75)
    item = make_obj('cdkr_juice_1', [0., 0., 0., 1.])
    b_loaded = make_backend(tray, [0., 0., 0., 1.])
    cands_loaded = CheckedPlacement._surface_candidates(b_loaded, tray, target, point, payload=[item], offsets=[(0., 0.)])
    assert cands_loaded
    assert ST.tilt_deg(cands_loaded[0]['pose'][1].numpy()) == pytest.approx(75., abs=0.5)
    a8_loaded = [r for r in b_loaded.records if r['status'] == 'rc1_A8_level'][-1]
    assert a8_loaded['applied'] is False and a8_loaded['unrestricted_upright'] is False

    # 3) Vessel carrying particle payload tilted 75 deg -> 45-deg cap stays in force
    cup = make_obj('cup', tilted_75)
    b_particles = make_backend(cup, [0., 0., 0., 1.], particle_payload=[object()])
    cands_particles = CheckedPlacement._surface_candidates(b_particles, cup, target, point, payload=(), offsets=[(0., 0.)])
    assert ST.tilt_deg(cands_particles[0]['pose'][1].numpy()) == pytest.approx(75., abs=0.5)
    a8_particles = [r for r in b_particles.records if r['status'] == 'rc1_A8_level'][-1]
    assert a8_particles['applied'] is False and a8_particles['unrestricted_upright'] is False

    # 4) Lying pre-grasp object (tilt 90 deg > 15 deg) -> 45-deg cap stays in force
    lying_ref = R.from_euler('x', 90., degrees=True).as_quat().tolist()
    held_lying = make_obj('log', R.from_euler('x', 15., degrees=True).as_quat())
    b_lying = make_backend(held_lying, lying_ref)
    cands_lying = CheckedPlacement._surface_candidates(b_lying, held_lying, target, point, payload=(), offsets=[(0., 0.)])
    assert ST.tilt_deg(cands_lying[0]['pose'][1].numpy()) == pytest.approx(15., abs=0.5)
    a8_lying = [r for r in b_lying.records if r['status'] == 'rc1_A8_level'][-1]
    assert a8_lying['applied'] is False and a8_lying['unrestricted_upright'] is False

    # 5) Explicit roll_degrees requested -> does not lift 45-deg cap
    held_roll = make_obj('bottle_roll', tilted_75)
    b_roll = make_backend(held_roll, [0., 0., 0., 1.])
    CheckedPlacement._surface_candidates(b_roll, held_roll, target, point, payload=(), roll_degrees=90., offsets=[(0., 0.)])
    a8_roll = [r for r in b_roll.records if r['status'] == 'rc1_A8_level'][-1]
    assert a8_roll['applied'] is False and a8_roll['unrestricted_upright'] is False

    # 6) MAS_PLACE_LEVEL=off -> unchanged, A8 does not run at all
    monkeypatch.setenv('MAS_PLACE_LEVEL', 'off')
    held_off = make_obj('bottle_off', tilted_75)
    b_off = make_backend(held_off, [0., 0., 0., 1.])
    cands_off = CheckedPlacement._surface_candidates(b_off, held_off, target, point, payload=(), offsets=[(0., 0.)])
    assert ST.tilt_deg(cands_off[0]['pose'][1].numpy()) == pytest.approx(75., abs=0.5)
    assert not any(r['status'] == 'rc1_A8_level' for r in b_off.records)


def test_a8_level_quat_b7_unladen_standing_vs_loaded(monkeypatch):
    import torch
    from manipulation_agent.executors import placement_stable as ST
    from manipulation_agent.executors.inside_sampling import a8_level_quat

    monkeypatch.delenv('MAS_PLACE_LEVEL', raising=False)
    held = _Held()
    in_hand = torch.as_tensor(R.from_euler('xyz', [105., 0., 20.], degrees=True).as_quat(), dtype=torch.float32)
    backend = _Backend({id(held): [0., 0., 0., 1.]})

    assert a8_level_quat(backend, held, in_hand, torch, allow_large_tilt=False) is None
    level = a8_level_quat(backend, held, in_hand, torch, allow_large_tilt=True)
    assert level is not None and ST.tilt_deg(level.numpy()) < 1.0
    monkeypatch.setenv('MAS_PLACE_LEVEL', 'off')
    assert a8_level_quat(backend, held, in_hand, torch, allow_large_tilt=True) is None
