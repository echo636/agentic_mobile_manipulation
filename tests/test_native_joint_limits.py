"""CPU adapter checks; optional pinned-source checks do not run a simulator."""
import ast
from functools import cached_property
import json
from contextlib import nullcontext
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from manipulation_agent.executors import native_joint_limits as policy
from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend
from manipulation_agent.posture_goals import POSTURE_JOINT_NAMES

th = pytest.importorskip('torch')


class JointController:
    pass


class View:
    def __init__(self, limits):
        self.limits = limits.clone()
        self.reject_once = False
        self.writes = 0

    def get_joint_limits(self):
        return self.limits.clone()

    def set_joint_limits(self, values, joint_indices):
        self.writes += 1
        self.limits[:, joint_indices] = values
        if self.reject_once:
            self.reject_once = False
            raise RuntimeError('injected physics write failure')


class Robot:
    @cached_property
    def joint_lower_limits(self):
        values = self._articulation_view.get_joint_limits()[0, :, 0]
        return th.where(th.isfinite(values), values, -1000.)

    @cached_property
    def joint_upper_limits(self):
        values = self._articulation_view.get_joint_limits()[0, :, 1]
        return th.where(th.isfinite(values), values, 1000.)

    @property
    def joint_has_limits(self):
        return th.isfinite(self._articulation_view.get_joint_limits()[0]).all(dim=-1)

    @cached_property
    def control_limits(self):
        return dict(position=(self.joint_lower_limits, self.joint_upper_limits),
            has_limit=self.joint_has_limits, velocity=(-th.ones(21), th.ones(21)),
            effort=(-th.ones(21) * 10, th.ones(21) * 10))


@pytest.fixture(params=['numpy', 'torch'])
def rig(monkeypatch, request):
    cb = (SimpleNamespace(from_torch=lambda x: x.detach().cpu().numpy(), to_torch=th.as_tensor)
          if request.param == 'numpy' else SimpleNamespace(from_torch=lambda x: x, to_torch=lambda x: x))
    robot = Robot()
    robot.joints = dict.fromkeys(['base'] + list(POSTURE_JOINT_NAMES) + ['left_finger', 'right_finger'])
    robot.articulation_root_path = '/robot'
    # Include the two limits implicated by the recorded failed placements.
    low, high = -th.ones(21), th.ones(21)
    low[list(robot.joints).index('right_arm_joint4')] = -2.0943997
    low[list(robot.joints).index('right_arm_joint6')] = -1.047198
    robot._articulation_view = View(th.stack([low, high], dim=-1)[None])
    robot._mas_original_position_limits_rad = {
        name: [float(low[list(robot.joints).index(name)]), float(high[list(robot.joints).index(name)])]
        for name in POSTURE_JOINT_NAMES}
    # Native FREE flags are authoritative even if tensor limit parameters are
    # finite/stale. This also exercises removal of pre-existing controller clips.
    robot.native_flags = {name: name not in POSTURE_JOINT_NAMES for name in robot.joints}
    monkeypatch.setattr(policy, '_native_limit_flags', lambda r: r.native_flags.copy())
    robot.trunk_control_idx = th.arange(1, 5)
    robot.arm_control_idx = dict(left=th.arange(5, 12), right=th.arange(12, 19))
    robot.controller_action_idx = dict(trunk=th.arange(4), arm_left=th.arange(4, 11),
        arm_right=th.arange(11, 18), gripper_left=th.tensor([18]), gripper_right=th.tensor([19]))
    robot.arm_names = ['left', 'right']
    robot.action_dim = 20
    robot.q = th.zeros(21)
    robot.get_joint_positions = lambda: robot.q.clone()
    robot._controller_config, robot.controllers, groups = {}, {}, {}
    for name, dofs in [('trunk', robot.trunk_control_idx), ('arm_left', robot.arm_control_idx['left']),
                       ('arm_right', robot.arm_control_idx['right'])]:
        c = JointController()
        c.control_type = c._motor_type = 'position'
        c._use_delta_commands = False
        c.command_input_limits = c.command_output_limits = None
        c._articulation_root_paths = ['/robot']
        c._unregistered_controllers = cb.from_torch(th.zeros(1))
        c.dof_idx = cb.from_torch(dofs)
        c._control_limits = {key: [cb.from_torch(x) for x in robot.control_limits[key]]
                             for key in ('position', 'velocity', 'effort')}
        c._dof_has_limits = cb.from_torch(robot.control_limits['has_limit'])
        c._clip_lo, c._clip_hi = [cb.from_torch(x[dofs]) for x in robot.control_limits['position']]
        robot._controller_config[name] = dict(control_limits=robot.control_limits)
        robot.controllers[name] = (name, 0)
        groups[name] = c
    control_type = SimpleNamespace(POSITION='position', get_type=lambda x: x)
    monkeypatch.setattr(policy, '_native_api', lambda: (control_type,
        SimpleNamespace(_controller_groups=groups), JointController, cb))
    return robot, groups, cb, control_type


def test_native_physics_and_both_clamps_are_disabled_without_touching_other_limits(rig):
    robot, groups, cb, _ = rig
    original = robot._articulation_view.get_joint_limits()
    cached_low = robot.joint_lower_limits
    old_controls = robot.control_limits
    old_group = groups['arm_right']._control_limits
    q = robot.q.clone()
    result = policy.disable_native_joint_position_limits(robot)
    indices = result['dof_indices']
    after = robot._articulation_view.get_joint_limits()
    assert th.equal(after, original)
    assert th.equal(after[:, [0, 19, 20]], original[:, [0, 19, 20]])
    assert not any(robot.native_flags[name] for name in POSTURE_JOINT_NAMES)
    assert th.equal(robot.q, q)
    assert robot._articulation_view.writes == 0
    assert result['physical_limit_readback'] == 'native_dof_has_limits_false'
    assert th.equal(cached_low, original[0, :, 0])  # no shared-storage mutation
    assert robot.control_limits['velocity'] is old_controls['velocity']
    assert robot.control_limits['effort'] is old_controls['effort']
    for c in groups.values():
        dofs = cb.to_torch(c.dof_idx).long()
        assert th.isneginf(cb.to_torch(c._control_limits['position'][0])[dofs]).all()
        assert th.isposinf(cb.to_torch(c._clip_hi)).all()
        assert not cb.to_torch(c._dof_has_limits)[dofs].any()
        assert robot._controller_config[next(k for k, v in groups.items() if v is c)]['control_limits'] is robot.control_limits
    assert groups['arm_right']._control_limits['velocity'] is old_group['velocity']
    assert th.isfinite(cb.to_torch(old_group['position'][0])).all()
    json.dumps(result, allow_nan=False)


def test_native_limited_flag_is_rejected_even_when_tensor_bounds_look_unlimited(rig):
    robot, groups, cb, _ = rig
    robot._articulation_view.limits[:, 1:19, 0] = -float('inf')
    robot._articulation_view.limits[:, 1:19, 1] = float('inf')
    original = robot._articulation_view.get_joint_limits()
    caches = {name: getattr(robot, name) for name in ('joint_lower_limits', 'joint_upper_limits', 'control_limits')}
    bounds = groups['arm_right']._control_limits
    robot.native_flags['right_arm_joint6'] = True
    with pytest.raises(RuntimeError, match='still reports limited'):
        policy.disable_native_joint_position_limits(robot)
    assert th.equal(robot._articulation_view.get_joint_limits(), original)
    assert groups['arm_right']._control_limits is bounds
    assert all(getattr(robot, key) is value for key, value in caches.items())
    assert robot._articulation_view.writes == 0


def test_missing_prephysics_authoring_aborts_without_runtime_tensor_write(rig):
    robot, _, _, _ = rig
    del robot._mas_original_position_limits_rad
    with pytest.raises(RuntimeError, match='not authored before physics creation'):
        policy.disable_native_joint_position_limits(robot)
    assert robot._articulation_view.writes == 0


def test_shared_controller_is_rejected_before_any_physics_write(rig):
    robot, groups, cb, _ = rig
    groups['arm_left']._articulation_root_paths.append('/other_robot')
    groups['arm_left']._unregistered_controllers = cb.from_torch(th.zeros(2))
    with pytest.raises(RuntimeError, match='shared controller'):
        policy.disable_native_joint_position_limits(robot)
    assert robot._articulation_view.writes == 0


def test_backend_startup_applies_policy_before_accepting_commands(rig, tmp_path, monkeypatch):
    from manipulation_agent.executors.omnigibson_rgb import RGBBackend
    robot, groups, cb, _ = rig
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    monkeypatch.setenv('MAS_MANIP_TIMING', 'model_authored_v1')
    def parent_init(b):
        b.robot, b.torch, b.output = robot, th, tmp_path
        b.env = SimpleNamespace(task=SimpleNamespace(object_scope={}))
    def layout(b):
        assert b._native_joint_limit_policy['status'] == 'disabled'
        assert not any(robot.native_flags[name] for name in POSTURE_JOINT_NAMES)
        return {}
    monkeypatch.setattr(RGBBackend, '__init__', parent_init)
    monkeypatch.setattr(OracleWaypointBackend, '_build_ik_layout', layout)
    b = OracleWaypointBackend()
    result = json.loads((tmp_path / 'native_joint_limit_policy.json').read_text())
    assert result['status'] == 'disabled' and len(result['joints']) == 18
    b._wrist_camera_contract = lambda: {}
    assert b.control_contract()['native_mechanical_limits'] == 'disabled_torso_and_both_arms'
    # OG normalization placeholders must not become command clamps or fake limits.
    b._command_q[:] = -1200.
    assert th.equal(b._hold_action()[:18], th.full((18,), -1200.))
    limits = b._reported_joint_limits(robot.trunk_control_idx)
    assert limits == dict(lower=[None] * 4, upper=[None] * 4, enabled=False)
    json.dumps(limits, allow_nan=False)


@pytest.mark.parametrize('engine,timing,expected', [('model_pose_v1', None, True),
    ('model_program_v1', 'model_authored_v1', True), ('geometry_pick_v1', None, False),
    ('model_program_v1', 'model_estimated_v1', False), ('model_program_v1', 'explicit_fast_v1', False)])
def test_policy_selection_preserves_explicit_historical_modes(monkeypatch, engine, timing, expected):
    monkeypatch.setenv('MAS_MANIP_ENGINE', engine)
    monkeypatch.delenv('MAS_MANIP_TIMING', raising=False)
    if timing is not None:
        monkeypatch.setenv('MAS_MANIP_TIMING', timing)
    assert policy.enabled() is expected


def test_recorded_failed_targets_pass_actual_pinned_controller_methods(rig):
    source = os.environ.get('MAS_TEST_OG_SOURCE')
    if not source:
        pytest.skip('Set MAS_TEST_OG_SOURCE for pinned-source CPU compatibility checks')
    robot, groups, cb, control_type = rig
    methods = {}
    for filename, classname, method in [('joint_controller.py', 'JointController', '_update_goal'),
                                       ('controller_base.py', 'BaseController', 'clip_control')]:
        tree = ast.parse((Path(source) / 'controllers' / filename).read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == classname)
        node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
        namespace = {'ControlType': control_type}
        exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
        methods[method] = namespace[method]
    c = groups['arm_right']
    command = cb.from_torch(th.tensor([0., 0., 0., -2.1493144, 0., -1.797005, 0.]))
    before = methods['_update_goal'](c, 0, command)['target']
    assert not th.equal(cb.to_torch(before), cb.to_torch(command))
    policy.disable_native_joint_position_limits(robot)
    after = methods['_update_goal'](c, 0, command)['target']
    sent = methods['clip_control'](c, after[None])
    assert th.equal(cb.to_torch(after), cb.to_torch(command))
    assert th.equal(cb.to_torch(sent)[0], cb.to_torch(command))


class Attr:
    def __init__(self, value):
        self.value = value
        self.reject_once = False

    def Get(self):
        return self.value

    def Set(self, value):
        if self.reject_once:
            self.reject_once = False
            return False
        self.value = value
        return True


class Prim:
    def __init__(self, name):
        self.name = name
        self.attrs = {'physics:lowerLimit': Attr(-60.), 'physics:upperLimit': Attr(90.)}

    def GetName(self): return self.name
    def GetTypeName(self): return 'PhysicsRevoluteJoint'
    def GetAttribute(self, key): return self.attrs[key]


@pytest.fixture
def stage():
    robot = SimpleNamespace(initialized=False)
    sim = SimpleNamespace(is_stopped=lambda: True, editing_usd=nullcontext)
    prims = [Prim(name) for name in [*POSTURE_JOINT_NAMES, 'gripper_joint', 'base_joint']]
    return robot, sim, prims


def test_stage_authors_exact18_before_physics_and_retains_original_degrees_as_radians(stage):
    robot, sim, prims = stage
    policy.author_unlimited_positions(robot, sim, prims)
    for p in prims[:18]:
        assert p.attrs['physics:lowerLimit'].Get() == -float('inf')
        assert p.attrs['physics:upperLimit'].Get() == float('inf')
        assert robot._mas_original_position_limits_rad[p.name] == pytest.approx([-np.pi / 3, np.pi / 2])
    assert all(p.attrs['physics:lowerLimit'].Get() == -60 for p in prims[18:])


@pytest.mark.parametrize('late', ['playing', 'initialized'])
def test_late_stage_authoring_rejected_without_mutation(stage, late):
    robot, sim, prims = stage
    if late == 'playing': sim.is_stopped = lambda: False
    else: robot.initialized = True
    with pytest.raises(RuntimeError, match='before physics creation'):
        policy.author_unlimited_positions(robot, sim, prims)
    assert all(p.attrs['physics:lowerLimit'].Get() == -60 for p in prims)


def test_stage_write_rejection_restores_all_original_values(stage):
    robot, sim, prims = stage
    prims[5].attrs['physics:upperLimit'].reject_once = True
    with pytest.raises(RuntimeError, match='USD rejected'):
        policy.author_unlimited_positions(robot, sim, prims)
    assert all(p.attrs['physics:lowerLimit'].Get() == -60 for p in prims)
    assert all(p.attrs['physics:upperLimit'].Get() == 90 for p in prims)
    assert not hasattr(robot, '_mas_original_position_limits_rad')


@pytest.mark.parametrize('fail_load', [False, True])
def test_environment_import_callback_precedes_first_play_and_is_removed(stage, monkeypatch, fail_load):
    _, sim, prims = stage
    callbacks, events = {}, []
    sim.add_callback_on_add_obj = lambda name, cb: callbacks.update({name: cb})
    sim.remove_callback_on_add_obj = callbacks.pop
    class NativeRobot:
        initialized = False
        model = 'r1pro'
        _prim = prims
    robot = NativeRobot()
    class Environment:
        def __init__(self, configs): self.load()
        def load(self):
            assert events == ['extension']
            monkeypatch.setitem(sys.modules, 'pxr', SimpleNamespace(Usd=SimpleNamespace(PrimRange=lambda p: p)))
            next(iter(callbacks.values()))(robot)
            assert prims[0].attrs['physics:lowerLimit'].Get() == -float('inf')
            events.append('first_play')
            if fail_load: raise RuntimeError('load failure')
    monkeypatch.setitem(sys.modules, 'omnigibson.robots', SimpleNamespace(Robot=NativeRobot))
    # pxr must not be imported before Environment starts SimulationApp.
    # Make it available only when the import callback is about to run.
    monkeypatch.delitem(sys.modules, 'pxr', raising=False)
    monkeypatch.setitem(sys.modules, 'isaacsim.core.utils.extensions',
                        SimpleNamespace(enable_extension=lambda _: events.append('extension')))
    og = SimpleNamespace(sim=sim, Environment=Environment)
    if fail_load:
        with pytest.raises(RuntimeError, match='load failure'):
            policy.create_unbounded_environment(og, {})
    else:
        policy.create_unbounded_environment(og, {})
    assert events == ['extension', 'first_play'] and not callbacks
