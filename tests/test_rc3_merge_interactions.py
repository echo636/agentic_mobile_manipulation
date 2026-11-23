"""CPU unit tests for cross-branch interactions in feat/rc3-dev (feat/rc2-cook + feat/rc2-dev)."""
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
import pytest
import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import articulation_hold as AH
from manipulation_agent.executors import placement_stable as ST
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.custom_task import CustomTaskBackend
from manipulation_agent.executors.inside_sampling import a8_level_quat, inward_margin_deficit, rests_on_resident
from manipulation_agent.executors.material_transfer import is_non_pourable_enclosure
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


@pytest.fixture(autouse=True)
def _clean_hold():
    AH._HELD.clear()
    AH._MODES.clear()
    AH._PAUSED.clear()
    yield
    AH._HELD.clear()
    AH._MODES.clear()
    AH._PAUSED.clear()


class _FakeJoint:
    def __init__(self, name, kind='RevoluteJoint', pos=0.0, low=0.0, high=1.5):
        self.joint_name = name
        self.name = name
        self.is_single_dof = True
        self.joint_type = kind
        self.pos = float(pos)
        self.vel = 0.0
        self.lower_limit = float(low)
        self.upper_limit = float(high)

    def get_state(self):
        return (self.pos, self.vel)

    def set_pos(self, v):
        self.pos = float(v)

    def set_vel(self, v):
        self.vel = float(v)


def _fake_og_modules():
    OpenCls = type('Open', (), {})
    InsideCls = type('Inside', (), {})
    OnTopCls = type('OnTop', (), {})

    og = ModuleType('omnigibson')
    states = ModuleType('omnigibson.object_states')
    states.Open = OpenCls
    states.Inside = InsideCls
    states.OnTop = OnTopCls

    open_state_mod = ModuleType('omnigibson.object_states.open_state')

    def _compute_joint_threshold(joint, direction):
        lo, hi = joint.lower_limit, joint.upper_limit
        thr = lo + 0.05 * (hi - lo) if direction > 0 else hi - 0.05 * (hi - lo)
        return thr, (hi if direction > 0 else lo), (lo if direction > 0 else hi)

    def _is_in_range(val, a, b):
        return min(a, b) <= val <= max(a, b)

    open_state_mod._compute_joint_threshold = _compute_joint_threshold
    open_state_mod._is_in_range = _is_in_range

    utils = ModuleType('omnigibson.utils')
    tu = ModuleType('omnigibson.utils.transform_utils')
    tu.relative_pose_transform = lambda p1, q1, p2, q2: (p1 - p2, q1)
    tu.pose_transform = lambda p1, q1, p2, q2: (p1 + p2, q1)
    utils.transform_utils = tu

    mods = {
        'omnigibson': og,
        'omnigibson.object_states': states,
        'omnigibson.object_states.open_state': open_state_mod,
        'omnigibson.utils': utils,
        'omnigibson.utils.transform_utils': tu,
    }
    return mods, OpenCls, InsideCls, OnTopCls


def test_open_state_joints_and_articulation_hold_lock_open_and_snapshot_coexist():
    """open_state_joints locks non-door racks via upper_limit while articulation_hold + _snapshot_closed_doors
    manage only the declared door joint(s) without interfering with locked racks."""
    mods, OpenCls, _, _ = _fake_og_modules()
    door = _FakeJoint('j_door', 'RevoluteJoint', pos=0.0, low=0.0, high=1.57)
    rack1 = _FakeJoint('j_rack1', 'PrismaticJoint', pos=0.0, low=0.0, high=0.30)
    rack2 = _FakeJoint('j_rack2', 'PrismaticJoint', pos=0.0, low=0.0, high=0.30)
    open_state = NS(relevant_joints_info=(False, [door, rack1, rack2], [1, 1, 1]),
                    clear_cache=lambda: None, get_value=lambda: door.pos > 0.1)
    oven = NS(name='oven_0', n_joints=3, states={OpenCls: open_state},
              joints={'j_door': door, 'j_rack1': rack1, 'j_rack2': rack2})

    backend = CustomTaskBackend.__new__(CustomTaskBackend)
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[oven, backend.robot]))
    backend.output = None

    with patch.dict('sys.modules', mods):
        backend._apply_open_state_joints({'roles': {'oven': 'oven_0'}, 'open_state_joints': {'oven': ['j_door']}})
        # Racks are locked in at lower limit; only door remains in Open relevant_joints_info.
        assert open_state.relevant_joints_info[1] == [door]
        assert rack1.upper_limit == pytest.approx(1e-4)
        assert rack2.upper_limit == pytest.approx(1e-4)

        # Open door and lock_open: only door enters AH._HELD; racks stay untouched.
        door.pos = 1.50
        held = AH.lock_open(backend, oven, [door])
        assert [h['joint'] for h in held] == ['j_door']
        assert AH.is_held_open(oven, door) is True
        assert AH.is_held(oven, rack1) is False
        assert AH.is_held(oven, rack2) is False

        # _snapshot_closed_doors skips the held-open door and does not latch racks.
        snapped = RGBBackend._snapshot_closed_doors(backend)
        assert snapped == []

        # Door bumped shut -> enforce restores door to 1.50, leaves racks alone.
        door.pos = 0.02
        assert AH.enforce(modes=('open',)) == 1
        assert door.pos == pytest.approx(1.50)

        # Explicit close unlocks and locks_closed ONLY door.
        AH.unlock(backend, oven)
        door.pos = 0.0
        closed = AH.lock_closed(backend, oven)
        assert [c['joint'] for c in closed] == ['j_door']
        assert AH.is_held_closed(oven, door) is True
        assert AH.is_held(oven, rack1) is False


def test_container_payload_uses_nearest_volume_for_native_and_released_inside():
    """_container_payload keeps nearest-volume fallback from feat/rc2-cook AND released_inside inclusion
    from f7d8d09."""
    mods, _, InsideCls, _ = _fake_og_modules()
    far_vol = NS(
        prim_path='/oven/vol_far', is_meta_link=True, meta_link_type='fillable',
        check_points_in_volume=lambda pts: torch.tensor([False]),
        get_position_orientation=lambda: (torch.tensor([0.0, 0.0, 2.0]), torch.tensor([0., 0., 0., 1.])),
    )
    near_vol = NS(
        prim_path='/oven/vol_near', is_meta_link=True, meta_link_type='fillable',
        check_points_in_volume=lambda pts: torch.tensor([False]),
        get_position_orientation=lambda: (torch.tensor([0.5, 0.5, 0.25]), torch.tensor([0., 0., 0., 1.])),
    )
    oven = NS(
        name='oven_0', n_joints=0,
        aabb=(torch.tensor([0.0, 0.0, 0.0]), torch.tensor([1.0, 1.0, 2.5])),
        links={'far': far_vol, 'near': near_vol},
    )
    dish = NS(
        name='dish_0', fixed_base=False,
        aabb=(torch.tensor([0.4, 0.4, 0.1]), torch.tensor([0.6, 0.6, 0.2])),
        states={InsideCls: NS(get_value=lambda target: False)},
        get_position_orientation=lambda: (torch.tensor([0.5, 0.5, 0.15]), torch.tensor([0., 0., 0., 1.])),
    )

    backend = ControlledCarry()
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[oven, dish, backend.robot]))
    backend.released_inside = lambda item, target: item is dish and target is oven

    with patch.dict('sys.modules', mods):
        payload = backend._container_payload(oven)
    assert len(payload) == 1
    assert payload[0][0] is dish
    assert payload[0][1] is near_vol


def test_standing_neighbours_recognizes_released_inside_extra():
    """placement_stable._standing_neighbours protects upright items accepted via released_inside
    (e.g. cavity floor of a timed-process oven) as well as native Inside / OnTop."""
    mods, _, InsideCls, OnTopCls = _fake_og_modules()
    oven = NS(
        name='oven_0', fixed_base=True,
        aabb=(torch.tensor([0.0, 0.0, 0.0]), torch.tensor([0.6, 0.6, 0.6])),
    )
    pos_a = torch.tensor([0.3, 0.3, 0.10])
    ori_a = torch.tensor([0.0, 0.0, 0.0, 1.0])
    mug_a = NS(
        name='mug_a', fixed_base=False, category='mug',
        aabb=(torch.tensor([0.25, 0.25, 0.02]), torch.tensor([0.35, 0.35, 0.14])),
        states={InsideCls: NS(get_value=lambda t: False), OnTopCls: NS(get_value=lambda t: False)},
        get_position_orientation=lambda: (pos_a, ori_a),
    )
    held_b = NS(
        name='mug_b', fixed_base=False, category='mug',
        aabb=(torch.tensor([0.0, 0.0, 1.0]), torch.tensor([0.1, 0.1, 1.12])),
        get_position_orientation=lambda: (torch.tensor([0.05, 0.05, 1.06]), torch.tensor([0., 0., 0., 1.])),
    )
    backend = NS(
        robot=NS(name='robot'),
        env=NS(scene=NS(objects=[oven, mug_a, held_b])),
        _pregrasp_orientations={id(held_b): [0., 0., 0., 1.]},
        released_inside=lambda obj, target: obj is mug_a and target is oven,
    )

    with patch.dict('sys.modules', mods):
        before = ST.before_release(backend, held_b, oven, [])
    assert before is not None
    assert before['neighbours'] == [mug_a]


def test_b7_unrestricted_level_and_cook_inside_ordering_coexist():
    """B7 unladen standing item leveling (>45 deg tilt) works alongside cook inward_margin_deficit
    and rests_on_resident ordering helpers."""
    # Held upright item tilted ~65 deg in hand; pregrasp was standing (0 deg).
    rad = np.radians(65.0)
    tilted_q = torch.tensor([np.sin(rad / 2), 0.0, 0.0, np.cos(rad / 2)], dtype=torch.float32)
    held = NS(name='mug_0', orientations={})
    backend = NS(_pregrasp_orientations={id(held): [0.0, 0.0, 0.0, 1.0]})

    # Restricted (loaded) refuses >45 deg; unrestricted (unladen) levels back to upright.
    assert a8_level_quat(backend, held, tilted_q, torch, allow_large_tilt=False) is None
    leveled = a8_level_quat(backend, held, tilted_q, torch, allow_large_tilt=True)
    assert leveled is not None
    assert ST.tilt_deg(leveled.tolist()) < 1.0

    # Cook ordering helpers remain intact.
    deficit_edge, _ = inward_margin_deficit([0.05, 0.5, 0.2], [0.1, 0.1, 0.1], [0., 0., 0.], [0.6, 1.0, 0.5], (1., 0.))
    deficit_deep, _ = inward_margin_deficit([0.30, 0.5, 0.2], [0.1, 0.1, 0.1], [0., 0., 0.], [0.6, 1.0, 0.5], (1., 0.))
    assert deficit_edge > deficit_deep
    assert rests_on_resident([0.3, 0.3, 0.25], [0.1, 0.1, 0.1], [([0.25, 0.25, 0.0], [0.35, 0.35, 0.20])]) is True


def test_b8_pour_guard_compatible_with_cook_open_low_door_opening_info():
    """is_non_pourable_enclosure (B8) remains True for side-opening / drop-down enclosures even when
    opening_info adds open_low_door_box."""
    mods, OpenCls, _, _ = _fake_og_modules()
    oven = NS(name='oven_0', category='oven', states={OpenCls: NS(get_value=lambda: True)})
    custom_box = NS(
        name='custom_appliance', category='custom_box',
        states={OpenCls: NS(get_value=lambda: True)},
        links={'base_link': NS()},
        aabb=([0., 0., 0.], [0.6, 0.6, 0.8]),
    )
    assert is_non_pourable_enclosure(oven) is True
    with patch.dict('sys.modules', mods), \
         patch('manipulation_agent.executors.opening_stance.is_thin', return_value=False), \
         patch('manipulation_agent.executors.opening_stance.opening_info',
               return_value={'normal': [0., 1.], 'face_center': [0.3, 0.6],
                             'open_low_door_box': [[0.0, 0.6], [0.6, 1.1]]}):
        assert is_non_pourable_enclosure(custom_box) is True
