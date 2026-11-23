"""rc2 N5b & N5a unit tests: bidirectional door hold (lock_open) and zero-displacement container payload guard."""
from contextlib import nullcontext
import json
import types
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest
import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import articulation_hold as AH
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.closure_clearance import check_closure_clearance
from manipulation_agent.executors.omnigibson_backend import OmniGibsonBackend
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.placement import CheckedPlacement


def _fake_transform_module():
    mod = types.ModuleType('omnigibson.utils.transform_utils')
    mod.relative_pose_transform = lambda cp, cq, pp, pq: (cp - pp, cq.clone())
    mod.pose_transform = lambda pp, pq, rp, rq: (pp + rp, rq.clone())
    mod.quat2mat = lambda q: torch.eye(3)
    mod.euler2quat = lambda e: torch.tensor([0., 0., 0., 1.])
    mod.quat_multiply = lambda a, b: b.clone()
    return mod


def _fake_og_modules():
    og_mod = types.ModuleType('omnigibson')
    states_mod = types.ModuleType('omnigibson.object_states')
    open_mod = types.ModuleType('omnigibson.object_states.open_state')

    class Open:
        pass

    class ToggledOn:
        pass

    class Inside:
        pass

    def _compute_joint_threshold(joint, direction):
        closed_end = joint.lower_limit if direction == 1 else joint.upper_limit
        open_end = joint.upper_limit if direction == 1 else joint.lower_limit
        threshold = 0.95 * closed_end + 0.05 * open_end
        return threshold, open_end, closed_end

    def _is_in_range(pos, threshold, range_end):
        return pos > threshold if range_end > threshold else pos < threshold

    states_mod.Open = Open
    states_mod.ToggledOn = ToggledOn
    states_mod.Inside = Inside
    open_mod._compute_joint_threshold = _compute_joint_threshold
    open_mod._is_in_range = _is_in_range
    utils_mod = types.ModuleType('omnigibson.utils')
    t_mod = _fake_transform_module()
    utils_mod.transform_utils = t_mod
    og_mod.object_states = states_mod
    og_mod.utils = utils_mod
    og_mod.__path__ = []
    states_mod.__path__ = []
    utils_mod.__path__ = []
    return {
        'omnigibson': og_mod,
        'omnigibson.object_states': states_mod,
        'omnigibson.object_states.open_state': open_mod,
        'omnigibson.utils': utils_mod,
        'omnigibson.utils.transform_utils': t_mod,
    }, Open, Inside


class FakeJoint:
    def __init__(self, name, pos=0.0, vel=0.0, lower=0.0, upper=1.5708):
        self.name = name
        self.joint_name = name
        self.pos = float(pos)
        self.vel = float(vel)
        self.lower_limit = float(lower)
        self.upper_limit = float(upper)
        self.dof_indices = [0]

    def get_state(self):
        return torch.tensor([self.pos]), torch.tensor([self.vel])

    def set_pos(self, value):
        self.pos = float(value)

    def set_vel(self, value):
        self.vel = float(value)


def _appliance(name, OpenCls, joints_dirs, *, both_sides=False):
    cache_clears = {'count': 0}

    def get_val():
        for j, d in joints_dirs:
            closed_end = j.lower_limit if d == 1 else j.upper_limit
            open_end = j.upper_limit if d == 1 else j.lower_limit
            thresh = 0.95 * closed_end + 0.05 * open_end
            if (j.pos > thresh if open_end > thresh else j.pos < thresh):
                return True
        return False

    def set_val(wanted, fully=False):
        for j, d in joints_dirs:
            target = (j.upper_limit if d == 1 else j.lower_limit) if wanted else (
                j.lower_limit if d == 1 else j.upper_limit)
            j.set_pos(target)
            j.set_vel(0.0)
        return True

    open_state = NS(
        _initialized=True,
        relevant_joints_info=(both_sides, [j for j, _ in joints_dirs], [d for _, d in joints_dirs]),
        get_value=get_val,
        set_value=set_val,
        clear_cache=lambda: cache_clears.__setitem__('count', cache_clears['count'] + 1),
    )
    return NS(
        name=name,
        n_joints=len(joints_dirs),
        joints={j.name: j for j, _ in joints_dirs},
        links={},
        states={OpenCls: open_state},
        get_joint_positions=lambda: torch.tensor([j.pos for j, _ in joints_dirs], dtype=torch.float32),
        set_joint_positions=lambda q: [j.set_pos(q[i]) for i, (j, _) in enumerate(joints_dirs)],
        keep_still=lambda: [j.set_vel(0.0) for j, _ in joints_dirs],
        state_updated=Mock(),
        _cache_clears=cache_clears,
    )


@pytest.fixture(autouse=True)
def _clean_hold(monkeypatch):
    monkeypatch.delenv('MAS_ARTICULATION_HOLD', raising=False)
    AH._HELD.clear()
    AH._MODES.clear()
    AH._PAUSED.clear()
    yield
    AH._HELD.clear()
    AH._MODES.clear()
    AH._PAUSED.clear()


# ---- N5b: bidirectional articulation_hold & open-door protection ----

def test_lock_open_holds_angle_against_chassis_push_and_respects_switch(monkeypatch):
    j = FakeJoint('j_link_0', pos=1.5426, vel=-2.881)
    dw = NS(name='c4_dishwasher')

    rec = AH.lock_open(NS(), dw, [j])
    assert len(rec) == 1
    assert rec[0]['open_position'] == pytest.approx(1.5426)
    assert rec[0]['mode'] == 'open'
    assert j.vel == 0.0
    assert AH.is_held_open(dw, j) is True
    assert AH.is_held_closed(dw, j) is False

    # Simulated chassis collision slams the drop-down door back below the 0.0785 rad Open threshold.
    j.pos, j.vel = 0.03, -2.88
    assert AH.apply() == 1
    assert j.pos == pytest.approx(1.5426)
    assert j.vel == 0.0

    # Small drift within HOLD_TOLERANCE is left untouched (no jitter).
    j.pos = 1.540
    assert AH.apply() == 0
    assert j.pos == pytest.approx(1.540)

    # Switch off disables lock_open cleanly (v12b behavior).
    AH.unlock(NS(), dw)
    monkeypatch.setenv('MAS_ARTICULATION_HOLD', 'off')
    assert AH.lock_open(NS(), dw, [j]) == []
    assert AH._HELD == {}


def test_snapshot_closed_doors_skips_held_open_door_even_if_bumped_below_threshold():
    mods, OpenCls, _ = _fake_og_modules()
    dw_j = FakeJoint('j_link_0', pos=1.54, lower=0.0, upper=1.5708)
    oven_j = FakeJoint('j_door', pos=0.0, lower=0.0, upper=1.5708)
    both_j = FakeJoint('j_both', pos=0.75, lower=0.0, upper=1.5)

    dw = _appliance('c4_dishwasher', OpenCls, [(dw_j, 1)])
    oven = _appliance('oven', OpenCls, [(oven_j, 1)])
    both = _appliance('both_cab', OpenCls, [(both_j, 1)], both_sides=True)

    backend = RGBBackend.__new__(RGBBackend)
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[dw, oven, both, backend.robot]))

    AH.lock_open(backend, dw, [dw_j])
    AH.lock_open(backend, both, [both_j])

    # Even if an impulse momentarily drops dw_j and both_j below the Open threshold (0.0785 rad),
    # _snapshot_closed_doors MUST NOT latch them into _latched_closed_positions.
    dw_j.pos = 0.02
    both_j.pos = 0.01

    with patch.dict('sys.modules', mods):
        snapped = backend._snapshot_closed_doors()

    assert [obj.name for obj, _, _ in snapped] == ['oven']
    latched_keys = list(backend._latched_closed_positions.keys())
    assert len(latched_keys) == 1 and latched_keys[0][0] == id(oven)


def test_open_locks_open_survives_settle_substeps_then_close_unlocks_and_locks_closed():
    mods, OpenCls, _ = _fake_og_modules()
    dw_j = FakeJoint('j_link_0', pos=0.0, lower=0.0, upper=1.5708)
    dw = _appliance('c4_dishwasher', OpenCls, [(dw_j, 1)])
    held = NS(name='c4_bowl2', wake=Mock(), states={})

    backend = RGBBackend.__new__(RGBBackend)
    backend._check_renderer_health = Mock()
    backend.deadline = NS(check=Mock())
    backend._base_target = None
    backend._trunk_hold_target = None
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[dw, held, backend.robot], active_systems={}))
    backend.primitives = NS(_empty_action=lambda: 'noop')
    backend.sampling_physics_steps = 0
    backend._check_placement_objects = Mock()
    backend._video_frame = Mock()
    backend._placement_record = Mock()

    art_diag = types.ModuleType('manipulation_agent.executors.articulation_diagnostics')
    art_diag.articulation_sample = lambda *a, **kw: {}
    art_diag.record_articulation = lambda *a, **kw: None
    art_goal = types.ModuleType('manipulation_agent.executors.articulation_goal')
    art_goal.native_open_goal = lambda b, obj, feedback=None: (torch.tensor([1.5708]), [(dw_j, 1)])
    art_goal.opened_joints = lambda obj, controlled: dw_j.pos > 0.0785

    def open_step_with_wheel_brush(_action):
        # Reproduces C4 r6 step 2: door settles at 1.5426 with -2.88 rad/s after brushing wheel.
        dw_j.pos = 1.5426
        dw_j.vel = -2.881

    def settle_step_slams_door():
        # Reproduces C4 r6 step 10 (200 physics substeps): wheel pushes door toward closed every substep.
        dw_j.pos = max(0.0, dw_j.pos - 0.05)
        dw_j.vel = -2.881

    backend.og = NS(sim=NS(
        _render_on_step=True,
        render_on_step=lambda _: nullcontext(),
        get_sim_step_dt=lambda: 1 / 30,
        get_physics_dt=lambda: 1 / 120,
        step_physics=settle_step_slams_door,
        sync_physx_to_fabric=Mock(),
    ))

    extra_mods = {
        **mods,
        'manipulation_agent.executors.articulation_diagnostics': art_diag,
        'manipulation_agent.executors.articulation_goal': art_goal,
    }
    with patch.dict('sys.modules', extra_mods):
        # 1. Explicit open verifies door genuinely opened, then locks it open with zero velocity.
        with patch.object(OmniGibsonBackend, '_step', side_effect=open_step_with_wheel_brush):
            res = backend._state_action('open', dw, 10)
        assert res['primitive'] == 'open'
        assert AH.is_held_open(dw, dw_j) is True
        assert dw_j.pos == pytest.approx(1.5426)
        assert dw_j.vel == 0.0

        # 2. 200 substeps of _settle_surface_contacts cannot slam the dishwasher door shut.
        CheckedPlacement._settle_surface_contacts(backend, held, dw, (), 50)
        assert dw_j.pos == pytest.approx(1.5426)
        assert dw.states[OpenCls].get_value() is True

        # 3. Post-placement _step() neither slams the door shut nor latches it closed.
        with patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: setattr(dw_j, 'pos', 0.01)):
            backend._step('noop')
        assert dw_j.pos == pytest.approx(1.5426)
        assert dw.states[OpenCls].get_value() is True
        assert backend._latched_closed_positions == {}

        # 4. Explicit close unlocks the open hold, closes the door, and locks it closed.
        with patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: None):
            backend._state_action('close', dw, 10)
        assert dw_j.pos == pytest.approx(0.0)
        assert AH.is_held_open(dw, dw_j) is False
        assert AH.is_held_closed(dw, dw_j) is True

        # 5. Once closed, subsequent nudges trying to open the door are pushed back to 0.0.
        with patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: setattr(dw_j, 'pos', 0.35)):
            backend._step('noop')
        assert dw_j.pos == pytest.approx(0.0)


def test_failed_open_blocked_by_static_obstacle_does_not_lock_open():
    mods, OpenCls, _ = _fake_og_modules()
    cab_j = FakeJoint('j_link_0', pos=0.0, lower=0.0, upper=1.5708)
    cab = _appliance('blocked_cabinet', OpenCls, [(cab_j, 1)])

    backend = RGBBackend.__new__(RGBBackend)
    backend._check_renderer_health = Mock()
    backend.deadline = NS(check=Mock())
    backend._base_target = None
    backend._trunk_hold_target = None
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[cab, backend.robot], active_systems={}))
    backend.primitives = NS(_empty_action=lambda: 'noop')
    backend.og = NS(sim=NS(_render_on_step=True, render_on_step=lambda _: nullcontext(),
                           sync_physx_to_fabric=Mock()))
    backend._check_placement_objects = Mock()
    backend._video_frame = Mock()
    backend._placement_record = Mock()

    art_diag = types.ModuleType('manipulation_agent.executors.articulation_diagnostics')
    art_diag.articulation_sample = lambda *a, **kw: {}
    art_diag.record_articulation = lambda *a, **kw: None
    art_goal = types.ModuleType('manipulation_agent.executors.articulation_goal')
    art_goal.native_open_goal = lambda b, obj, feedback=None: (torch.tensor([1.5708]), [(cab_j, 1)])
    art_goal.opened_joints = lambda obj, controlled: cab_j.pos > 0.0785

    def static_obstacle_rejects_open(_action):
        cab_j.pos = 0.01  # static furniture bounces door shut before stability check

    extra_mods = {
        **mods,
        'manipulation_agent.executors.articulation_diagnostics': art_diag,
        'manipulation_agent.executors.articulation_goal': art_goal,
    }
    with patch.dict('sys.modules', extra_mods), patch.object(OmniGibsonBackend, '_step', side_effect=static_obstacle_rejects_open):
        with pytest.raises(SkillError) as exc_info:
            backend._state_action('open', cab, 10)
    assert exc_info.value.code == 'postcondition_error'
    assert AH.is_held_open(cab, cab_j) is False
    assert AH._HELD == {}


def test_closure_clearance_pauses_open_hold_during_preview_and_resumes_after():
    mods, OpenCls, InsideCls = _fake_og_modules()
    oven_j = FakeJoint('j_door', pos=1.5, lower=0.0, upper=1.5)
    oven = _appliance('oven', OpenCls, [(oven_j, 1)])
    oven.scene = NS(idx=0)
    door_link = NS(prim_path='/oven/door', is_meta_link=False,
                   get_position_orientation=lambda: (torch.tensor([0., oven_j.pos, 0.5]), torch.tensor([0., 0., 0., 1.])))
    oven.links = {'door': door_link}
    class _HashableNS(NS):
        __hash__ = object.__hash__

    item = _HashableNS(name='dish', links={'base': NS(prim_path='/dish/base', is_meta_link=False)},
                       states={InsideCls: NS(get_value=lambda target: True)})

    AH.lock_open(NS(), oven, [oven_j])
    assert AH.is_held_open(oven, oven_j) is True

    door_pos_during_step = []

    def step_physics_with_physx_callback():
        # Simulate PhysX pre-step callback calling AH.enforce() right before physics step!
        AH.enforce()
        door_pos_during_step.append(oven_j.pos)

    saved_pos = {'q': None}

    def dump_state(serialized=False):
        saved_pos['q'] = oven_j.pos
        return oven_j.pos

    def load_state(state, serialized=False):
        oven_j.pos = float(state)

    backend = NS(
        torch=torch,
        deadline=NS(check=Mock()),
        og=NS(sim=NS(dump_state=dump_state, load_state=load_state, step_physics=step_physics_with_physx_callback)),
        _container_payload=lambda target: [],
        _relocate_container_payload=lambda payload: None,
        frames_revision=0,
    )

    usd_mod = types.ModuleType('omnigibson.utils.usd_utils')
    usd_mod.RigidContactAPI = NS(get_contact_pairs=lambda *a, **kw: [])
    extra_mods = {**mods, 'omnigibson.utils.usd_utils': usd_mod}
    with patch.dict('sys.modules', extra_mods), \
            patch('manipulation_agent.executors.container_support.contacting_support', return_value=None), \
            patch('manipulation_agent.executors.particle_payload.snapshot_particle_state', return_value=[]), \
            patch('manipulation_agent.executors.particle_payload.particle_state_unchanged', return_value=True), \
            patch('manipulation_agent.executors.particle_payload.restore_particle_state', return_value=None), \
            patch('manipulation_agent.executors.placement.scene_object_identities', return_value=()):
        res = check_closure_clearance(backend, oven, [item])

    # During preview step, oven door stayed closed (0.0) instead of being snapped back open by AH.enforce()!
    assert door_pos_during_step == [0.0]
    assert res['clear'] is True
    # After preview rollback, oven is restored open and lock_open is active again!
    assert oven_j.pos == pytest.approx(1.5)
    oven_j.pos = 0.02
    assert AH.enforce() == 1
    assert oven_j.pos == pytest.approx(1.5)


# ---- N5a: _verify_container_payload zero-displacement unmoved exemption ----

def _payload_fixture(tmp_path):
    mods, OpenCls, InsideCls = _fake_og_modules()
    base_lo = torch.tensor([8.1, -2.1, 0.7])
    base_hi = torch.tensor([8.9, -1.4, 1.4])
    base_link = NS(prim_path='/World/scene_0/oven_ffitak_0/base_link', is_meta_link=False, aabb=(base_lo, base_hi))
    rack_pos = torch.tensor([8.5, -1.75, 1.0])
    rack_ori = torch.tensor([0., 0., 0., 1.])
    rack_link = NS(
        prim_path='/World/scene_0/oven_ffitak_0/rack2',
        is_meta_link=False,
        get_position_orientation=lambda: (rack_pos.clone(), rack_ori.clone()),
    )
    container = NS(
        name='oven_ffitak_0',
        root_link_name='base_link',
        links={'base_link': base_link, 'rack2': rack_link},
        aabb=(base_lo, base_hi),
    )

    def make_item(name, init_pos, half=(0.06, 0.06, 0.02)):
        state = {
            'pos': torch.tensor(init_pos, dtype=torch.float32),
            'ori': torch.tensor([0., 0., 0., 1.], dtype=torch.float32),
            'vel': torch.zeros(3, dtype=torch.float32),
            'inside': True,
        }
        half_t = torch.tensor(half, dtype=torch.float32)

        class Item:
            pass

        item = Item()
        item.name = name
        item.get_position_orientation = lambda: (state['pos'].clone(), state['ori'].clone())
        item.get_linear_velocity = lambda: state['vel'].clone()
        item.states = {InsideCls: NS(get_value=lambda target: state['inside'])}
        item._state = state
        type(item).aabb = property(lambda self: (state['pos'] - half_t, state['pos'] + half_t))
        return item

    backend = ControlledCarry()
    backend.torch = torch
    backend.output = tmp_path
    backend.steps = 131
    return backend, container, rack_link, make_item, mods


def test_verify_container_payload_waives_zero_displacement_boundary_flip(tmp_path):
    backend, oven, rack, make_item, mods = _payload_fixture(tmp_path)
    fish = make_item('c2_fish', [8.50275, -1.76205, 1.05076])
    rel = (fish._state['pos'] - rack.get_position_orientation()[0], torch.tensor([0., 0., 0., 1.]))
    payload = [(fish, rack, rel)]

    # Reproduces C2 r4 step 89: native Inside flips False with ~1e-6 m displacement & near-zero velocity inside oven.
    fish._state['inside'] = False
    fish._state['pos'] = fish._state['pos'] + torch.tensor([0.0, 0.0, 1e-6])
    fish._state['vel'] = torch.tensor([3.4e-5, 1.5e-5, -2.8e-5])

    with patch.dict('sys.modules', mods):
        backend._verify_container_payload(oven, payload, stage='articulation_contents_unconfirmed')

    records = [json.loads(line) for line in (tmp_path / 'carry_diagnostics.jsonl').read_text().splitlines()]
    assert len(records) == 1
    assert records[0]['event'] == 'container_payload_boundary_unmoved_waived'
    waived = records[0]['waived_contents'][0]
    assert waived['object'] == 'c2_fish'
    assert waived['near_boundary_unmoved'] is True
    assert waived['aabb_center_in_container_body'] is True
    assert waived['displacement_m'] <= 0.01


@pytest.mark.parametrize('case', ['displaced_5cm', 'moving_fast', 'outside_body'])
def test_verify_container_payload_still_rejects_real_displacement_or_escape(tmp_path, case):
    backend, oven, rack, make_item, mods = _payload_fixture(tmp_path)
    init = [8.50, -1.76, 1.05] if case != 'outside_body' else [8.50, -1.20, 1.05]
    fish = make_item('c2_fish', init)
    rel = (fish._state['pos'] - rack.get_position_orientation()[0], torch.tensor([0., 0., 0., 1.]))
    payload = [(fish, rack, rel)]

    fish._state['inside'] = False
    if case == 'displaced_5cm':
        fish._state['pos'] = fish._state['pos'] + torch.tensor([0.0, 0.05, 0.0])  # pushed 5 cm by closing door
    elif case == 'moving_fast':
        fish._state['vel'] = torch.tensor([0.0, 0.2, 0.0])  # still flying at 0.2 m/s
    elif case == 'outside_body':
        # Zero displacement relative to initial pos, but center is outside oven base_link AABB!
        fish._state['pos'] = fish._state['pos'] + torch.tensor([0.0, 0.0, 1e-6])

    with patch.dict('sys.modules', mods):
        with pytest.raises(SkillError, match='Operation displaced existing contents') as exc_info:
            backend._verify_container_payload(oven, payload, stage='articulation_contents_unconfirmed')
    assert exc_info.value.code == 'postcondition_error'
    assert exc_info.value.stage == 'articulation_contents_unconfirmed'

    records = [json.loads(line) for line in (tmp_path / 'carry_diagnostics.jsonl').read_text().splitlines()]
    assert any(r['event'] == 'container_payload_violation' for r in records)


def test_state_action_logs_failed_articulation_when_payload_verification_fails():
    mods, OpenCls, _ = _fake_og_modules()
    oven_j = FakeJoint('j_door', pos=1.5, lower=0.0, upper=1.5)
    oven = _appliance('oven', OpenCls, [(oven_j, 1)])

    backend = RGBBackend.__new__(RGBBackend)
    backend._check_renderer_health = Mock()
    backend.deadline = NS(check=Mock())
    backend._base_target = None
    backend._trunk_hold_target = None
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[oven, backend.robot], active_systems={}))
    backend.primitives = NS(_empty_action=lambda: 'noop')
    backend.og = NS(sim=NS(_render_on_step=True, render_on_step=lambda _: nullcontext(),
                           sync_physx_to_fabric=Mock()))
    backend._check_placement_objects = Mock()
    backend._video_frame = Mock()
    backend._placement_record = Mock()
    backend._verify_container_payload = Mock(
        side_effect=SkillError('postcondition_error', 'Operation displaced existing contents from the container',
                               changed=True, stage='articulation_contents_unconfirmed')
    )

    recorded = []
    art_diag = types.ModuleType('manipulation_agent.executors.articulation_diagnostics')
    art_diag.articulation_sample = lambda *a, **kw: {}
    art_diag.record_articulation = lambda b, obj, prim, samples, **kw: recorded.append(kw)

    extra_mods = {**mods, 'manipulation_agent.executors.articulation_diagnostics': art_diag}
    with patch.dict('sys.modules', extra_mods), patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: None):
        with pytest.raises(SkillError, match='Operation displaced existing contents'):
            backend._state_action('close', oven, 10)

    assert len(recorded) == 1
    assert recorded[0]['status'] == 'failed'
    assert recorded[0]['reason'] == 'container_payload_violation'


def test_multi_panel_open_preserves_other_panel_closed_hold():
    mods, OpenCls, _ = _fake_og_modules()
    upper_j = FakeJoint('upper_freezer', pos=0.0, lower=0.0, upper=1.4)
    lower_j = FakeJoint('lower_fridge', pos=0.0, lower=0.0, upper=1.4)
    fridge = _appliance('fridge', OpenCls, [(upper_j, 1), (lower_j, 1)])

    backend = RGBBackend.__new__(RGBBackend)
    backend._check_renderer_health = Mock()
    backend.deadline = NS(check=Mock())
    backend._base_target = None
    backend._trunk_hold_target = None
    backend.robot = NS(name='robot')
    backend.env = NS(scene=NS(objects=[fridge, backend.robot], active_systems={}))
    backend.primitives = NS(_empty_action=lambda: 'noop')
    backend.og = NS(sim=NS(_render_on_step=True, render_on_step=lambda _: nullcontext(),
                           sync_physx_to_fabric=Mock()))
    backend._check_placement_objects = Mock()
    backend._video_frame = Mock()
    backend._placement_record = Mock()

    art_diag = types.ModuleType('manipulation_agent.executors.articulation_diagnostics')
    art_diag.articulation_sample = lambda *a, **kw: {}
    art_diag.record_articulation = lambda *a, **kw: None
    art_goal = types.ModuleType('manipulation_agent.executors.articulation_goal')
    art_goal.native_open_goal = lambda b, obj, feedback=None: (torch.tensor([0.0, 1.4]), [(lower_j, 1)])
    art_goal.opened_joints = lambda obj, controlled: lower_j.pos > 0.5

    extra_mods = {
        **mods,
        'manipulation_agent.executors.articulation_diagnostics': art_diag,
        'manipulation_agent.executors.articulation_goal': art_goal,
    }
    with patch.dict('sys.modules', extra_mods), patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: None):
        AH.lock_closed(backend, fridge)
        assert AH.is_held_closed(fridge, upper_j) and AH.is_held_closed(fridge, lower_j)

        backend._state_action('open', fridge, 10)
        assert AH.is_held_closed(fridge, upper_j) is True
        assert AH.is_held_open(fridge, lower_j) is True

        # Nudging upper_j open and lower_j shut: enforce puts both back to their respective targets!
        upper_j.pos = 0.4
        lower_j.pos = 0.02
        assert AH.enforce() == 2
        assert upper_j.pos == pytest.approx(0.0)
        assert lower_j.pos == pytest.approx(1.4)


def test_verify_container_payload_mixed_waived_and_displaced_still_raises(tmp_path):
    backend, oven, rack, make_item, mods = _payload_fixture(tmp_path)
    fish = make_item('c2_fish', [8.50, -1.76, 1.05])
    tray = make_item('c2_tray', [8.40, -1.76, 1.08])
    r0 = rack.get_position_orientation()[0]
    payload = [
        (fish, rack, (fish._state['pos'] - r0, torch.tensor([0., 0., 0., 1.]))),
        (tray, rack, (tray._state['pos'] - r0, torch.tensor([0., 0., 0., 1.]))),
    ]

    fish._state['inside'] = False
    fish._state['pos'] = fish._state['pos'] + torch.tensor([0.0, 0.0, 1e-6])
    tray._state['inside'] = False
    tray._state['pos'] = tray._state['pos'] + torch.tensor([0.0, 0.05, 0.0])  # real 5 cm displacement

    with patch.dict('sys.modules', mods):
        with pytest.raises(SkillError, match='Operation displaced existing contents'):
            backend._verify_container_payload(oven, payload, stage='articulation_contents_unconfirmed')

    records = [json.loads(line) for line in (tmp_path / 'carry_diagnostics.jsonl').read_text().splitlines()]
    events = {r['event']: r for r in records}
    assert 'container_payload_boundary_unmoved_waived' in events
    assert 'container_payload_violation' in events
    assert [d['object'] for d in events['container_payload_boundary_unmoved_waived']['waived_contents']] == ['c2_fish']
    assert [d['object'] for d in events['container_payload_violation']['escaped_contents']] == ['c2_tray']
