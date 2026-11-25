"""Unit tests for rc4 generic inside placement fixes:
1. Resident preservation guard (protects flat dishes & all movable container residents during place_inside).
2. Tight-headroom (3 mm) support-clearance fallback when every standard 2.5 cm drop proposal overlaps.
"""
import math
from types import SimpleNamespace
import numpy as np
import torch
import pytest

from manipulation_agent.executors import placement_stable as ST
from manipulation_agent.executors.inside_sampling import select_inside_release


class _DummyObj:
    def __init__(self, name, pos, ori, aabb, *, inside=True, fixed_base=False):
        self.name = name
        self._pos = np.array(pos, dtype=float)
        self._ori = np.array(ori, dtype=float)
        self.aabb = (np.array(aabb[0], dtype=float), np.array(aabb[1], dtype=float))
        self.fixed_base = fixed_base
        self.keep_still_calls = 0
        self._inside = inside
        self.states = {}

    def get_position_orientation(self):
        return self._pos.copy(), self._ori.copy()

    def set_position_orientation(self, pos, ori):
        self._pos = np.array(pos, dtype=float)
        self._ori = np.array(ori, dtype=float)

    def keep_still(self):
        self.keep_still_calls += 1


class _DummyBackend:
    def __init__(self, inside_fn):
        self._inside_fn = inside_fn

    def released_inside(self, obj, target):
        return bool(self._inside_fn(obj, target))


def test_resident_snapshot_includes_flat_dish_and_respects_switch(monkeypatch):
    # Real c2_lasagna extent (25.83 x 19.05 x 7.47 cm): aspect = 0.289 < 0.30 so _upright_meaningful is False,
    # yet resident_snapshot MUST protect it!
    lasagna = _DummyObj("c2_lasagna", [8.46, -1.75, 1.10], [0, 0, 0, 1],
                        ([8.3308, -1.8452, 1.0626], [8.5891, -1.6547, 1.1373]))
    assert ST._upright_meaningful(lasagna) is False
    target = SimpleNamespace(name="oven_ffitak_0")
    backend = _DummyBackend(lambda o, t: o._inside)

    monkeypatch.setenv("MAS_PLACE_STABLE_CHECK", "off")
    assert ST.resident_snapshot(backend, target, [(lasagna, None, None)]) is None

    monkeypatch.setenv("MAS_PLACE_STABLE_CHECK", "on")
    snap = ST.resident_snapshot(backend, target, [(lasagna, None, None)])
    assert snap is not None
    assert len(snap["entries"]) == 1
    assert len(ST.restored_resident_aabbs(snap)) == 1


def test_check_residents_catches_ejection_displacement_and_tipping_and_restores(monkeypatch):
    monkeypatch.setenv("MAS_PLACE_STABLE_CHECK", "on")
    lasagna = _DummyObj("c2_lasagna", [8.46, -1.75, 1.10], [0, 0, 0, 1],
                        ([8.3308, -1.8452, 1.0626], [8.5891, -1.6547, 1.1373]), inside=True)
    target = SimpleNamespace(name="oven_ffitak_0")
    backend = _DummyBackend(lambda o, t: o._inside)
    snap = ST.resident_snapshot(backend, target, [(lasagna, None, None)])

    # 1. Undisturbed: passes
    ok, fails = ST.check_residents(backend, target, snap)
    assert ok is True and fails == []

    # 2. Small settle (3 cm, 5 deg): still passes
    lasagna.set_position_orientation([8.46, -1.72, 1.10], [0.04, 0, 0, math.sqrt(1 - 0.04**2)])
    ok, fails = ST.check_residents(backend, target, snap)
    assert ok is True and fails == []

    # 3. Knocked onto open oven door (displaced 25 cm, tilted 75 deg, outside cavity): FAILS
    lasagna._inside = False
    lasagna.set_position_orientation([8.46, -1.50, 0.90], [0.6, 0, 0, 0.8])
    ok, fails = ST.check_residents(backend, target, snap)
    assert ok is False and len(fails) == 1
    assert fails[0]["name"] == "c2_lasagna"
    assert fails[0]["still_inside"] is False
    assert fails[0]["disp_m"] > 0.12

    # 4. Still inside cavity but tipped 60 deg: FAILS
    lasagna._inside = True
    lasagna.set_position_orientation([8.46, -1.75, 1.10], [math.sin(math.radians(30)), 0, 0, math.cos(math.radians(30))])
    ok, fails = ST.check_residents(backend, target, snap)
    assert ok is False and fails[0]["tilt_delta_deg"] > 45.0

    # 5. Restore puts lasagna cleanly back at original pose with keep_still
    ST.restore_residents(snap)
    pos, ori = lasagna.get_position_orientation()
    assert np.allclose(pos, [8.46, -1.75, 1.10])
    assert np.allclose(ori, [0, 0, 0, 1])
    assert lasagna.keep_still_calls == 1


def test_select_inside_release_tight_headroom_fallback_avoids_upper_rack_overlap(monkeypatch):
    """When normal +2.53 cm drop clearance collides with an upper rack (z_top > 1.064),
    tight-headroom fallback lowers the support proposal to +3 mm clearance and achieves overlap=0."""
    import sys
    import types

    # Provide lightweight stub for omnigibson.utils.transform_utils if omnigibson is unavailable in CPU pytest
    if "omnigibson.utils.transform_utils" not in sys.modules:
        og = sys.modules.setdefault("omnigibson", types.ModuleType("omnigibson"))
        og_utils = sys.modules.setdefault("omnigibson.utils", types.ModuleType("omnigibson.utils"))
        tu = types.ModuleType("omnigibson.utils.transform_utils")
        tu.quat2mat = lambda q: torch.eye(3, dtype=q.dtype, device=q.device)
        tu.euler2quat = lambda e: torch.tensor([0.0, 0.0, 0.0, 1.0], dtype=e.dtype, device=e.device)
        tu.quat_multiply = lambda a, b: b
        og.utils = og_utils
        og_utils.transform_utils = tu
        sys.modules["omnigibson.utils.transform_utils"] = tu

    monkeypatch.setattr("manipulation_agent.executors.closure_clearance.closed_geometry_proposals", lambda b, t: None)
    monkeypatch.setattr("manipulation_agent.executors.closure_clearance.closed_geometry_overlap", lambda *a, **k: 0.0)
    monkeypatch.setattr("manipulation_agent.executors.particle_payload.particle_bounds", lambda *a, **k: [])

    class _FakeVolume:
        is_meta_link = True
        meta_link_type = "fillable"
        prim_path = "/World/scene_0/oven_ffitak_0/fillable"
        visual_aabb = (torch.tensor([8.20, -1.95, 0.91]), torch.tensor([8.66, -1.55, 1.40]))
        visual_boundary_points_world = torch.tensor([
            [8.20, -1.95, 0.91], [8.66, -1.95, 0.91], [8.20, -1.55, 0.91], [8.66, -1.55, 0.91],
            [8.20, -1.95, 1.40], [8.66, -1.95, 1.40], [8.20, -1.55, 1.40], [8.66, -1.55, 1.40],
        ])

        def get_position_orientation(self):
            return torch.tensor([8.43, -1.75, 1.15]), torch.tensor([0.0, 0.0, 0.0, 1.0])

        def check_points_in_volume(self, pts):
            return torch.ones(len(pts), dtype=torch.bool)

    # Dish height 0.098 m (half = 0.049 m), sitting on lower rack at z = 0.959 m:
    # - normal clearance (+0.0253 m): center z = 1.0333 -> top = 1.0823 > rack1 bottom (1.064) -> overlap = 1!
    # - tight clearance (+0.003 m): center z = 1.011 -> top = 1.060 < rack1 bottom (1.064) -> overlap = 0!
    class _FakeHeld:
        name = "c2_fish"
        orientations = {"rest": {"rotation": [0.0, 0.0, 0.0, 1.0], "prob": 1.0}}
        links = {}

        def get_position_orientation(self):
            return torch.tensor([0.0, 0.0, 0.0]), torch.tensor([0.0, 0.0, 0.0, 1.0])

    class _FakeTarget:
        name = "oven_ffitak_0"
        links = {"fillable": _FakeVolume()}

        def get_position_orientation(self):
            return torch.tensor([8.43, -1.75, 1.15]), torch.tensor([0.0, 0.0, 0.0, 1.0])

    class _FakeBackend:
        torch = torch
        og = SimpleNamespace(sim=SimpleNamespace(get_physics_dt=lambda: 1.0 / 120.0))
        env = SimpleNamespace(scene=None)
        deadline = SimpleNamespace(check=lambda **k: None)
        robot = None

        def _collision_vertices(self, obj):
            return np.array([
                [-0.12, -0.12, -0.049], [0.12, -0.12, -0.049], [-0.12, 0.12, -0.049], [0.12, 0.12, -0.049],
                [-0.12, -0.12,  0.049], [0.12, -0.12,  0.049], [-0.12, 0.12,  0.049], [0.12, 0.12,  0.049],
            ], dtype=float)

        def _inside_support_centers(self, held, target, volume, centers, offset, payload, check, **kw):
            # Support ray hits lower rack at z=0.959 -> normal clearance (+0.02534) gives z=1.03334
            return [[8.43, -1.75, 0.959 + 0.049 + 0.02534]]

        def _inside_pose_overlap_ranker(self, held, assembly):
            def score(pos, quat):
                # Real oven two-rack geometry: lower rack top at z=0.959, upper rack bottom at z=1.064.
                z_bot = float(pos[2]) - 0.049
                z_top = float(pos[2]) + 0.049
                return 0 if (z_bot >= 0.959 and z_top <= 1.064) else 1
            return score

        def _inside_overlap_ranker(self, assembly):
            return lambda c, e: 0

    res = select_inside_release(_FakeBackend(), _FakeHeld(), _FakeTarget(), [])
    assert res["proposal_overlap_score"] == 0
    assert res["tight_headroom_clearance_m"] == pytest.approx(0.003)
    assert float(res["pose"][0][2]) + 0.049 < 1.064
