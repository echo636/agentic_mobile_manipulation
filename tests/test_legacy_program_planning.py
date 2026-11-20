"""Regression checks for reuse of historical planning, without native actions."""
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys

import pytest


def engine(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    # These regressions cover the explicitly selected historical planner/path.
    # Adaptive and capped execution policies have their own rate-aware fixtures.
    monkeypatch.setenv('MAS_MANIP_TIMING', 'historical_full_trajectory')
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    transform = ModuleType('omnigibson.utils.transform_utils')
    transform.pose_transform = lambda p, q, target, quat: (p+target, quat)
    transform.quat2mat = lambda q: torch.eye(3)
    for name in ('omnigibson', 'omnigibson.utils'):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.transform_utils', transform)
    pose = (torch.tensor([10., 0., 0.]), torch.tensor([0., 0., 0., 1.]))
    b = SimpleNamespace(output=tmp_path, torch=torch, steps=0,
        robot=SimpleNamespace(links={'torso_link4': SimpleNamespace(get_position_orientation=lambda: pose)},
            eef_links={'right': SimpleNamespace(get_position_orientation=lambda:
                (torch.tensor([10.4, -.2, .8]), pose[1]))}),
        og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: 1/30)))
    result = ModelPoseManip(b)
    result.arm = 'right'; result.reference = pose
    result.attachments = {}; result.retained = {}; result.contact_link = None
    result.enable_legacy_planning()
    return result, torch


@pytest.mark.parametrize('contact', [False, True])
def test_program_profile_reuses_actual_old_planner_and_contact_policy(tmp_path, monkeypatch, contact):
    from manipulation_agent.executors.oracle_pick import GeometryPick
    e, torch = engine(tmp_path, monkeypatch)
    e.contact_link = object() if contact else None
    path = torch.zeros((3, 6))
    def old_plan(*args, **kwargs):
        e.last_trajectory_validation = {'artifact': 'old_path.npz'}
        return path
    old = Mock(side_effect=old_plan)
    monkeypatch.setattr(GeometryPick, 'plan', old)
    e.finish_planned_path = Mock(return_value=(path, {'success': True}))
    goal = {'position_m': [.4, -.2, .8], 'quaternion_xyzw': [0., 0., 0., 1.], 'gripper': 'hold'}
    e.plan_goal(goal)
    assert e.planner_link_names == ()
    assert GeometryPick.planner_link_names == ('torso_link4',)  # Separate historical engine.
    args, kwargs = old.call_args
    assert args[0] is e and args[1] == pytest.approx([10.4, -.2, .8])
    assert kwargs['approach'] is contact and kwargs['ignore_target'] is contact
    assert kwargs['attached'] is False
    assert not e.finish_planned_path.called
    assert e.planner_limit_scales == GeometryPick.planner_limit_scales


@pytest.mark.parametrize('collision', [False, True])
def test_profile_plans_bent_arm_without_torso_goal_but_still_rejects_collisions(
        tmp_path, monkeypatch, collision):
    """Exercise shared planning and validation, not only the profile selector."""
    e, torch = engine(tmp_path, monkeypatch)
    import omnigibson
    import omnigibson.utils.transform_utils as transform
    omnigibson.sim = e.b.og.sim
    transform.mat2quat = lambda rotation: torch.tensor([0., 0., 0., 1.])
    e.b.robot.eef_link_names = {'right': 'eef'}
    # Synthetic torso/arm path includes a 29-degree torso and 103-degree arm
    # bend. No home_body field exists: this mode must not need a body target.
    path = torch.stack([torch.linspace(0., .5, 151), torch.linspace(0., -1.8, 151)], dim=1)
    full = SimpleNamespace(position=path, velocity=None, acceleration=None)
    full.get_ordered_joint_state = lambda names: full
    result = SimpleNamespace(success=torch.tensor([True]), status='SUCCESS',
        valid_query=True, attempts=1, trajopt_attempts=1, position_error=None,
        rotation_error=None, get_paths=lambda: [full])
    e.planner = SimpleNamespace(robot_joint_names=['torso', 'arm'],
        update_obstacles=Mock(), compute_trajectories=Mock(return_value=[result]),
        check_collisions=Mock(return_value=torch.tensor([collision])))
    e.mg = SimpleNamespace(interpolation_dt=1/30, get_full_js=lambda value: value)
    q, info = e.plan_goal({'position_m': [.4, -.2, .8],
                          'quaternion_xyzw': [0., 0., 0., 1.], 'gripper': 'hold'})
    sent = e.planner.compute_trajectories.call_args.kwargs
    assert set(sent['target_pos']) == set(sent['target_quat']) == {'eef'}
    assert e.planner.check_collisions.called
    assert (q is None) is collision
    assert info['extra_body_posture_constraints'] is False
    if q is not None:
        assert torch.allclose(q[-1], path[-1])
        assert info['torso_link'] is None


@pytest.mark.parametrize('lateral', [0., .03])
def test_only_exact_vertical_held_goal_can_reuse_old_support_departure(tmp_path, monkeypatch, lateral):
    from manipulation_agent.executors.oracle_pick import GeometryPick
    e, torch = engine(tmp_path, monkeypatch)
    e.attachments = {'right_eef': object()}
    e.retained = {'right': (object(), object())}
    monkeypatch.setattr(GeometryPick, 'plan', Mock(return_value=None))
    def depart(*args):
        e.last_trajectory_validation = {'artifact': 'certified_path.npz'}
        return torch.zeros((3, 6))
    old_depart = Mock(side_effect=depart)
    monkeypatch.setattr(GeometryPick, 'plan_support_departure', old_depart)
    q, info = e.plan_goal({'position_m': [.4+lateral, -.2, .92],
                         'quaternion_xyzw': [0., 0., 0., 1.], 'gripper': 'hold'})
    assert old_depart.called is (lateral == 0.)
    assert (q is not None) is (lateral == 0.)
    if q is not None:
        assert info['support_departure_certified']
        assert info['fallback'] == 'GeometryPick.plan_support_departure'


def test_historical_profile_is_explicit_and_rejects_unknown_policy():
    from test_model_candidates import batch
    from manipulation_agent.manip_programs import checked_output
    from manipulation_agent.contracts import SkillError
    value = batch(); value['planning_profile'] = 'geometry_pick_v1'
    for candidate in value['candidates']:
        candidate['phases'][-1].update(gripper='close',
            contact_target={'object_id': 'bottle', 'link_name': 'base_link'})
    assert checked_output(value)['planning_profile'] == 'geometry_pick_v1'
    value['planning_profile'] = 'unknown'
    with pytest.raises(SkillError):
        checked_output(value)
