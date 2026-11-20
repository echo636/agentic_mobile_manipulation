"""Execution retiming must preserve goals and recheck paths before native motion."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


def engine(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    monkeypatch.setenv('MAS_MANIP_TIMING', 'explicit_fast_v1')
    # Deliberately use different robot and planner joint orders.
    limits = SimpleNamespace(joint_names=['arm', 'torso'],
        velocity=torch.tensor([[-.125, -.125], [.125, .125]]),
        acceleration=torch.tensor([[-4.5, -4.5], [4.5, 4.5]]),
        jerk=torch.tensor([[-150., -150.], [150., 150.]]))
    robot = SimpleNamespace(joints={'torso': None, 'arm': None},
        max_joint_velocities=torch.tensor([2.5, .6]), eef_link_names={'right': 'eef'})
    b = SimpleNamespace(output=tmp_path, torch=torch, robot=robot,
        og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: 1/30)))
    e = ModelPoseManip(b)
    e.arm = 'right'; e.link = object()
    e.mg = SimpleNamespace(robot_cfg=SimpleNamespace(kinematics=
        SimpleNamespace(get_joint_limits=lambda: limits,
            cspace=SimpleNamespace(joint_names=['torso', 'arm'],
                max_acceleration=torch.tensor([15., 12.]),
                max_jerk=torch.tensor([500., 400.])))))
    e.planner = SimpleNamespace(robot_joint_names=['torso', 'arm'],
        check_collisions=Mock(return_value=torch.tensor([False])))
    e.event = Mock(); e.body_clearance = Mock(return_value=True)
    e.target_clearance = Mock(return_value=True)
    e.last_trajectory_validation = {'artifact': 'original.npz'}
    t = np.linspace(0., 1., 765)
    raw = torch.tensor(np.stack([.2*(10*t**3-15*t**4+6*t**5), np.zeros_like(t)], axis=1),
                       dtype=torch.float32)
    pose = dict(position_m=[.4, 0., .8], quaternion_xyzw=[0., 0., 0., 1.])
    return e, raw, pose


def test_measured_limits_are_mapped_and_planner_scaling_is_not_reapplied(tmp_path, monkeypatch):
    e, raw, pose = engine(tmp_path, monkeypatch)
    indices, rates, evidence = e.execution_rates()
    assert indices == [1, 0]
    assert rates[0] == pytest.approx([.6, .75])
    assert evidence['planner_limits']['velocity'] == [.125, .125]
    assert evidence['planner_configuration_changed'] is False
    q, info = e.retime_legacy_path(raw, pose, {}, None, False, object())
    assert q.shape == (101, 2)
    assert np.array_equal(q[[0, -1]].numpy(), raw[[0, -1]].numpy())
    assert e.body_clearance.called and e.target_clearance.called
    assert e.planner.check_collisions.call_args.args[0].shape[0] >= 101
    with np.load(tmp_path / info['trajectory']) as saved:
        assert saved['source_trajectory'] == 'original.npz'
        assert np.array_equal(saved['goal_position_m'], pose['position_m'])


@pytest.mark.parametrize('failure', ['collision', 'body', 'target'])
def test_new_interpolant_failures_never_return_an_executable_path(tmp_path, monkeypatch, failure):
    e, raw, pose = engine(tmp_path, monkeypatch)
    if failure == 'collision':
        e.planner.check_collisions.return_value[:] = True
    elif failure == 'body':
        e.body_clearance.return_value = False
    else:
        e.target_clearance.return_value = False
    q, info = e.retime_legacy_path(raw, pose, {}, None, False, object())
    assert q is None and info['success'] is False
    assert not list(tmp_path.glob('fast_trajectory_*.npz'))


def test_individually_valid_segments_with_discontinuous_join_are_rejected(tmp_path, monkeypatch):
    e, raw, pose = engine(tmp_path, monkeypatch)
    q, _ = e.retime_legacy_path(raw, pose, {}, None, False, None)
    assert e.validate_execution_join([('first', q)])
    following = q[-1:].repeat(20, 1)
    following[:, 0] += .01
    assert not e.validate_execution_join([('first', q), ('second', following)])


def test_model_estimate_uses_native_limits_and_certifies_complete_long_path(tmp_path, monkeypatch):
    e, raw, pose = engine(tmp_path, monkeypatch)
    e.execution_timing = 'model_estimated_v1'
    e.requested_control_steps = 180
    q, info = e.retime_legacy_path(raw, pose, {}, None, False, None)
    assert len(q) >= 181
    assert info['timing']['requested_control_steps'] == 180
    assert info['timing']['max_control_steps'] is None
    assert info['timing']['within_joint_limits']
    assert e.validate_execution_join([('align', q)])
    assert e.planner.check_collisions.called
    with np.load(tmp_path / info['trajectory']) as saved:
        assert np.array_equal(saved['source_position'], raw.numpy())
        assert np.array_equal(saved['position'][[0, -1]], raw[[0, -1]].numpy())
    # A short model estimate must extend this same complete path before motion.
    e.requested_control_steps = 2
    q, info = e.retime_legacy_path(raw, pose, {}, None, False, None)
    assert info['timing']['estimate_extended']
    assert info['timing']['control_steps'] > 2
    assert np.array_equal(q[[0, -1]].numpy(), raw[[0, -1]].numpy())


def test_default_timing_uses_uncapped_native_velocity_and_unscaled_model_rates(tmp_path, monkeypatch):
    e, raw, pose = engine(tmp_path, monkeypatch)
    e.execution_timing = 'model_estimated_v1'
    indices, rates, evidence = e.execution_rates()
    assert indices == [1, 0]
    assert rates[0] == pytest.approx([.6, 2.5])
    assert rates[1] == pytest.approx([12., 15.])
    assert rates[2] == pytest.approx([400., 500.])
    assert evidence['execution_limit_source'] == dict(
        velocity='robot.max_joint_velocities', acceleration='cspace.max_acceleration', jerk='cspace.max_jerk')
    assert evidence['planner_configuration_changed'] is False
    # A nonzero complete path can execute faster while collision checks remain dense.
    e.requested_control_steps = 1
    q, info = e.retime_legacy_path(raw, pose, {}, None, False, None)
    assert q is not None and info['timing']['within_joint_limits']
    assert info['timing']['limits']['execution_limits']['velocity'] == pytest.approx([.6, 2.5])
    assert np.array_equal(q[[0, -1]].numpy(), raw[[0, -1]].numpy())
    dense = e.planner.check_collisions.call_args.args[0].numpy()
    assert np.abs(np.diff(dense, axis=0)).max() <= .005+1e-6


@pytest.mark.parametrize('field,value', [
    ('max_acceleration', [0., 12.]), ('max_acceleration', [float('nan'), 12.]),
    ('max_jerk', [500., -1.]), ('max_jerk', [500.]),
])
def test_invalid_unscaled_model_rates_never_produce_an_executable_path(tmp_path, monkeypatch, field, value):
    e, raw, pose = engine(tmp_path, monkeypatch)
    e.execution_timing = 'model_estimated_v1'
    setattr(e.mg.robot_cfg.kinematics.cspace, field, e.b.torch.tensor(value))
    with pytest.raises(ValueError, match='Unscaled model limits'):
        e.retime_legacy_path(raw, pose, {}, None, False, None)
    assert not e.planner.check_collisions.called
    assert not list(tmp_path.glob('fast_trajectory_*.npz'))


def test_support_departure_uses_same_robot_limits_and_dense_certificate_samples(tmp_path, monkeypatch):
    from manipulation_agent.grasp_geometry import time_joint_curve
    from manipulation_agent.trajectory_timing import compact_path
    e, raw, _ = engine(tmp_path, monkeypatch)
    e.execution_timing = 'model_estimated_v1'
    e.requested_control_steps = 1
    e.mg.kinematics = SimpleNamespace(joint_names=['torso', 'arm'])
    old_rates = [np.full(2, value) for value in (.125, 4.5, 150.)]
    old_q, _ = time_joint_curve(raw.numpy(), *old_rates, 1/30)
    q, timing, validation = e.time_support_departure(raw.numpy(), old_rates, 1/30)
    assert len(q) < len(old_q) and timing['requested_control_steps'] == 1
    assert timing['limits']['execution_limits']['velocity'] == pytest.approx([.6, 2.5])
    assert compact_path(q, 1/30, [2.5, .6], [15., 12.], [500., 400.], max_steps=len(q)-1)[0] is not None
    assert np.array_equal(q[[0, -1]], raw[[0, -1]].numpy())
    assert np.abs(np.diff(validation, axis=0)).max() <= .005+1e-6
    assert np.array_equal(validation[[0, -1]], q[[0, -1]])


def test_historical_support_departure_keeps_original_timing(tmp_path, monkeypatch):
    from manipulation_agent.grasp_geometry import time_joint_curve
    e, raw, _ = engine(tmp_path, monkeypatch)
    rates = [np.full(2, value) for value in (.125, 4.5, 150.)]
    q, timing, validation = e.time_support_departure(raw.numpy(), rates, 1/30)
    old_q, old_timing = time_joint_curve(raw.numpy(), *rates, 1/30)
    assert np.array_equal(q, old_q) and timing == old_timing and validation is q
