"""Model-owned timing and unbounded planning, with collision checks preserved."""
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import Mock

import numpy as np
import pytest

from manipulation_agent.trajectory_timing import authored_path, dense_joint_samples


@pytest.mark.parametrize('steps', [None, 1, 37, 240])
def test_duration_is_model_owned_without_rate_extension(steps):
    raw = np.linspace(-8., 9., 301)[:, None]
    q, info = authored_path(raw, 1/30, control_steps=steps)
    assert len(q) == (301 if steps is None else steps+1)
    assert np.array_equal(q[[0, -1]], raw[[0, -1]].astype(np.float32))
    assert not info['estimate_extended'] and info['joint_limit_checks'] is False
    assert np.abs(np.diff(dense_joint_samples(q, .005), axis=0)).max() <= .005+1e-6


@pytest.mark.parametrize('raw,dt,steps', [([[0.], [float('nan')]], 1/30, 20),
    ([[0.], [float('inf')]], 1/30, 20), ([[0.], [1.]], 0., 20),
    ([[0.], [1.]], 1/30, True), ([[0.], [1.]], 1/30, 0)])
def test_invalid_commands_and_duration_are_still_rejected(raw, dt, steps):
    with pytest.raises(ValueError): authored_path(raw, dt, control_steps=steps)


@pytest.mark.parametrize('collision', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
def test_pose_and_program_paths_use_exact_duration_but_reject_collision(tmp_path, monkeypatch, collision, legacy):
    from test_fast_execution_timing import engine
    e, raw, pose = engine(tmp_path, monkeypatch)
    e.execution_timing = 'model_authored_v1'
    e.requested_control_steps = 11
    # Neither rates nor joint bounds may be consulted by execution timing.
    e.execution_rates = Mock(side_effect=AssertionError('rate check must be off'))
    e.mg.robot_cfg.kinematics.get_joint_limits = Mock(side_effect=AssertionError('bounds must be off'))
    e.planner.check_collisions.return_value[:] = collision
    if legacy:
        q, info = e.retime_legacy_path(raw, pose, {}, None, False, None)
    else:
        q, info = e.finish_planned_path(raw.numpy(), pose, {}, None, {}, None, 1/30)
    assert info['timing']['control_steps'] == 11
    assert e.planner.check_collisions.called
    dense = e.planner.check_collisions.call_args.args[0].numpy()
    assert np.abs(np.diff(dense, axis=0)).max() <= .005+1e-6
    if collision:
        assert q is None and info['success'] is False
    else:
        assert len(q) == 12 and np.array_equal(q[[0, -1]].numpy(), raw[[0, -1]].numpy())


def test_support_departure_keeps_model_duration_and_dense_certificate_samples(tmp_path, monkeypatch):
    from test_fast_execution_timing import engine
    e, raw, _ = engine(tmp_path, monkeypatch)
    e.execution_timing, e.requested_control_steps = 'model_authored_v1', 29
    e.execution_rates = Mock(side_effect=AssertionError('rate checks disabled'))
    q, timing, dense = e.time_support_departure(raw.numpy(), None, 1/30)
    assert len(q) == 30 and timing['control_steps'] == 29
    assert np.array_equal(q[[0, -1]], raw[[0, -1]].numpy())
    assert np.abs(np.diff(dense, axis=0)).max() <= .005+1e-6
    th = e.b.torch
    first = th.tensor([[0., 0.], [4., 0.]])
    second = th.tensor([[4., 0.], [-3., 0.]])
    assert e.validate_execution_join([('a', first), ('b', second)])
    second[0, 0] += .01
    assert not e.validate_execution_join([('a', first), ('b', second)])


def test_all_planner_components_disable_bounds_without_mutating_model_or_collision(monkeypatch):
    th = pytest.importorskip('torch')
    from manipulation_agent.executors.planner_joint_limits import disable_planner_joint_limits
    # Inject enum-only imports: no simulator or GPU is needed for this adapter test.
    for name, attr in [('curobo.opt.particle.particle_opt_utils', 'SquashType'),
                       ('curobo.util.trajectory', 'InterpolateType')]:
        module = ModuleType(name)
        setattr(module, attr, SimpleNamespace(IDENTITY='identity', LINEAR='linear'))
        monkeypatch.setitem(sys.modules, name, module)
    low, high = th.tensor([-1., -2.]), th.tensor([1., 2.])
    world, self_collision = Mock(), Mock()
    rollouts = [SimpleNamespace(bound_cost=SimpleNamespace(weight=th.ones(4), _weight=th.ones(4)),
        bound_constraint=SimpleNamespace(forward=Mock(side_effect=AssertionError('old bounds'))),
        primitive_collision_constraint=world, robot_self_collision_constraint=self_collision) for _ in range(6)]
    solvers = {}
    for name in ('ik_solver', 'trajopt_solver', 'finetune_trajopt_solver', 'js_trajopt_solver', 'finetune_js_trajopt_solver'):
        optimizer = SimpleNamespace(action_lows=low, action_highs=high, squash_fn='clamp')
        solvers[name] = SimpleNamespace(solver=SimpleNamespace(optimizers=[optimizer]),
            optimize_dt=True, traj_evaluator=SimpleNamespace(cost_weight=.01))
    graph = SimpleNamespace(bounds=th.stack([low, high]), interpolation_type='rate_limited')
    before = graph.bounds.clone()
    mg = SimpleNamespace(**solvers, graph_planner=graph, optimize_dt=True,
                         get_all_rollout_instances=lambda: rollouts)
    evidence = disable_planner_joint_limits(mg)
    assert evidence['bound_rollouts'] == 6 and evidence['unbounded_optimizers'] == 5
    assert th.equal(low, before[0]) and th.equal(high, before[1])
    positions = th.tensor([[[8., -9.], [12., 10.]]], requires_grad=True)
    for rollout in rollouts:
        assert not rollout.bound_cost.weight.any() and not rollout.bound_cost._weight.any()
        assert th.equal(rollout.bound_constraint.forward(SimpleNamespace(position=positions)), th.zeros(1, 2))
        assert rollout.primitive_collision_constraint is world and rollout.robot_self_collision_constraint is self_collision
    for name, solver in solvers.items():
        optimizer = solver.solver.optimizers[0]
        assert th.equal(th.clamp(positions, optimizer.action_lows, optimizer.action_highs), positions)
        assert optimizer.squash_fn == 'identity'
        if name != 'ik_solver':
            state = SimpleNamespace(position=positions, velocity=positions*1000, acceleration=positions*1e6, jerk=positions*1e9)
            assert solver.traj_evaluator._check_smoothness(state, None, None)[0].all()
            label, cost = solver.traj_evaluator.evaluate_interpolated_smootheness(state, None, th.ones(2), None)
            assert label.all() and th.isfinite(cost).all() and not solver.optimize_dt
    assert not mg.optimize_dt and graph.interpolation_type == 'linear'
    assert th.isneginf(graph.bounds[0]).all() and th.isposinf(graph.bounds[1]).all()
    world.assert_not_called(); self_collision.assert_not_called()


@pytest.mark.parametrize('check_limits', [False, True])
def test_stream_removes_only_selected_policy_command_clamp_and_keeps_all_commands(tmp_path, check_limits):
    from test_oracle_open_loop import backend
    from manipulation_agent.executors.oracle_open_loop import OpenLoopPickTrial
    from manipulation_agent.contracts import SkillError
    b, event, _, commands, _ = backend(tmp_path)
    th = b.torch
    lower, upper = b.robot.joint_lower_limits.clone(), b.robot.joint_upper_limits.clone()
    q = th.linspace(0., 2., 8)[:, None].repeat(1, 6)
    def run():
        with OpenLoopPickTrial(b, event) as trial:
            trial.joint_limit_checks = check_limits
            trial.stream(q, 'right')
            trial.accepted = True
    if check_limits:
        with pytest.raises(SkillError, match='endpoint'): run()
    else:
        run()
    sent = th.stack(commands)
    assert len(sent) == 7
    assert float(sent[-1, 0]) == (1. if check_limits else 2.)
    assert th.equal(lower, b.robot.joint_lower_limits) and th.equal(upper, b.robot.joint_upper_limits)
