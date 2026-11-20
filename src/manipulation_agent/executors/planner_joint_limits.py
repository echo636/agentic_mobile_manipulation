"""Project-local adaptation for the pinned cuRobo joint-unbounded policy.

Only planner objects are changed. Native articulations, assets, and upstream
source files are untouched. Finite seed distributions remain useful proposals;
they are not feasibility bounds on the optimized solution.
"""
from types import MethodType


def _no_bound_constraint(self, state, *args, **kwargs):
    # ArmBase.constraint_fn calls this even if enabled=False. Return (B,H),
    # leaving its separate world and self collision constraints in place.
    return state.position.sum(dim=-1) * 0.


def _smoothness(self, js, *args):
    import torch
    valid = torch.isfinite(js.position).all(dim=-1).all(dim=-1)
    cost = torch.zeros_like(js.position[..., 0, 0])
    for name, weight in (('velocity', .001), ('acceleration', .01), ('jerk', .001)):
        value = getattr(js, name, None)
        if value is not None:
            valid = valid & torch.isfinite(value).all(dim=-1).all(dim=-1)
            cost = cost + weight * value.abs().mean(dim=(-2, -1))
    return valid, cost


def _interpolated_score(self, js, opt_dt, cspace_distance_weight, max_vel):
    label, cost = _smoothness(self, js)
    path = (js.velocity.abs() * cspace_distance_weight).sum(dim=(-2, -1))
    return label, path + self.cost_weight * cost


def disable_planner_joint_limits(mg, native_policy=None):
    """Remove bound costs/constraints, optimizer clamps and rate screening.

    Infinity expresses an absent clamp, never a large surrogate joint limit.
    Clone optimizer/graph tensors so finite robot bounds and samplers are not
    mutated through shared tensor storage. Smoothness remains a preference.
    """
    from curobo.opt.particle.particle_opt_utils import SquashType
    from curobo.util.trajectory import InterpolateType

    rollouts = {id(r): r for r in mg.get_all_rollout_instances()}
    for rollout in rollouts.values():
        bound = rollout.bound_cost
        bound.weight.zero_()
        bound._weight.zero_()  # enable_cost must not restore the old penalty.
        rollout.bound_constraint.forward = MethodType(_no_bound_constraint, rollout.bound_constraint)
    optimizers, evaluators = {}, {}
    for name in ('ik_solver', 'trajopt_solver', 'finetune_trajopt_solver',
                 'js_trajopt_solver', 'finetune_js_trajopt_solver'):
        solver = getattr(mg, name)
        for optimizer in solver.solver.optimizers:
            optimizers[id(optimizer)] = optimizer
        if name != 'ik_solver':
            solver.optimize_dt = False
            evaluators[id(solver.traj_evaluator)] = solver.traj_evaluator
    for optimizer in optimizers.values():
        optimizer.action_lows = optimizer.action_lows.clone().fill_(-float('inf'))
        optimizer.action_highs = optimizer.action_highs.clone().fill_(float('inf'))
        if hasattr(optimizer, 'squash_fn'):
            optimizer.squash_fn = SquashType.IDENTITY
    for evaluator in evaluators.values():
        evaluator._check_smoothness = MethodType(_smoothness, evaluator)
        evaluator.evaluate_interpolated_smootheness = MethodType(_interpolated_score, evaluator)
    mg.optimize_dt = False
    graph = mg.graph_planner
    graph.bounds = graph.bounds.clone()
    graph.bounds[0].fill_(-float('inf'))
    graph.bounds[1].fill_(float('inf'))
    # The alternative Kunz-Stilman interpolator imposes rate limits. Linear
    # graph interpolation is geometric; final execution timing is model-owned.
    graph.interpolation_type = InterpolateType.LINEAR
    return dict(policy='model_authored_v1', bound_rollouts=len(rollouts),
        unbounded_optimizers=len(optimizers), rate_evaluators=len(evaluators),
        planning_joint_limits=False, execution_joint_limits=False,
        native_mechanical_limits=(native_policy or {}).get('status', 'unchanged'),
        finite_seed_proposals='unchanged',
        world_collision_checks=True, self_collision_checks=True)
