"""Joint recovery contracts and CPU adapter tests; never a native robot trial."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.manip_programs import ManipPrograms, checked_invocation, checked_output, checked_phases
from manipulation_agent.posture_goals import POSTURE_JOINT_NAMES


SOURCE = Path(__file__).resolve().parents[1] / 'program_skills/geometry/recover_body/source.py'


def recovery():
    spec = importlib.util.spec_from_file_location('saved_recovery', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def oracle(left=None, right=None):
    return dict(frame='robot_base', robot=dict(trunk=dict(joint_positions_rad=[0.]*4),
        arms={a: dict(joint_positions_rad=[0.]*7, held_object=held)
              for a, held in [('left', left), ('right', right)]}))


@pytest.mark.parametrize('left,right', [(None, None), ('different_bottle', None),
    (None, 'another_object'), ('left_payload', 'right_payload')])
def test_saved_reference_is_the_same_whole_body_pose_for_either_hand(left, right):
    module = recovery()
    generated = module.generate('', oracle(left, right), {})
    phase = checked_phases(generated)[0]
    assert phase['arm'] == 'both' and phase['gripper'] == 'hold' and phase['contact_target'] is None
    preset = json.loads(SOURCE.with_name('preset.json').read_text())
    assert {k: v for k, v in phase.items() if k != 'name'} == {k: v for k, v in preset['pose_template'].items() if k != 'mode'}
    assert set(phase['goal']['joint_positions_rad']) == set(POSTURE_JOINT_NAMES)
    checked_invocation(dict(mode='pose', revision=12, **{k: v for k, v in phase.items() if k != 'name'}))


@pytest.mark.parametrize('bad', ['missing_joint', 'base', 'gripper_joint', 'one_arm', 'open', 'close', 'contact', 'mixed_goal', 'missing_goal', 'nan'])
def test_incomplete_or_mixed_recovery_rejected_before_motion(bad):
    phase = recovery().generate('', oracle(), {})['phases'][0]
    joints = phase['goal']['joint_positions_rad']
    if bad == 'missing_joint': joints.pop('left_arm_joint1')
    elif bad == 'base': joints['base_footprint_rz_joint'] = 0.
    elif bad == 'gripper_joint': joints['left_gripper_finger_joint1'] = 0.
    elif bad == 'one_arm': phase['arm'] = 'right'
    elif bad in ('open', 'close'): phase['gripper'] = bad
    elif bad == 'contact': phase['contact_target'] = dict(object_id='historical_bottle', link_name='base_link')
    elif bad == 'mixed_goal': phase['goal']['position_m'] = [0., 0., 1.]
    elif bad == 'missing_goal': phase['goal'] = {}
    else: joints['torso_joint1'] = float('nan')
    with pytest.raises(SkillError): checked_phases({'phases': [phase]})
    with pytest.raises(SkillError):
        checked_invocation(dict(mode='pose', revision=0, **{k: v for k, v in phase.items() if k != 'name'}))


def test_whole_body_targets_are_not_accepted_as_eef_grasp_candidates():
    phase = recovery().generate('', oracle(), {})['phases'][0]
    with pytest.raises(SkillError, match='phase sequence'):
        checked_output(dict(candidates=[dict(name='whole_body', score=0., phases=[phase])], prepare_gripper=None))


@pytest.mark.parametrize('parameters', [{'arm': 'left'}, {'control_steps': 0}, {'control_steps': True}, {'control_steps': 1.5}])
def test_saved_reference_does_not_accept_posture_overrides_or_invalid_timing(parameters):
    with pytest.raises(ValueError): recovery().generate('', oracle(), parameters)


def test_saved_reference_rejects_world_frame_and_other_robot_layout():
    state = oracle(); state['frame'] = 'world'
    with pytest.raises(ValueError): recovery().generate('', state, {})
    state = oracle(); state['robot']['arms']['left']['joint_positions_rad'] = [0.]*6
    with pytest.raises(ValueError): recovery().generate('', state, {})


def adapter(tmp_path):
    th = pytest.importorskip('torch')
    from manipulation_agent.executors.oracle_joint_posture import ModelJointPosture
    # Deliberately use different physical ordering and extra locked joints.
    names = ['base_footprint_rz_joint', *reversed(POSTURE_JOINT_NAMES), 'left_gripper_finger_joint1', 'right_gripper_finger_joint1']
    q = th.arange(len(names), dtype=th.float32)*.01
    robot = SimpleNamespace(joints=dict.fromkeys(names), q=q,
        joint_lower_limits=th.full_like(q, -4.), joint_upper_limits=th.full_like(q, 4.))
    robot.get_joint_positions = lambda: robot.q.clone()
    b = SimpleNamespace(robot=robot, torch=th, output=tmp_path, steps=0,
        _gripper_commands=dict(left=-1., right=-1.), _body_posture_state=lambda: {'upper_body_tilt_deg': 2.34})
    b._retention = Mock(return_value={'retained': True})
    engine = ModelJointPosture(b)
    engine.retained = dict(left=('left_object', 'left_link'), right=('right_object', 'right_link'))
    engine.attachments = dict(left_eef='left_link', right_eef='right_link')
    return engine, b, names


def test_joint_targets_map_by_name_leave_base_grippers_and_check_both_retained_objects(tmp_path):
    engine, b, names = adapter(tmp_path)
    goal = recovery().generate('', oracle(), {})['phases'][0]['goal']
    target = engine.goal_vector(goal)
    for name in POSTURE_JOINT_NAMES:
        assert float(target[names.index(name)]) == pytest.approx(goal['joint_positions_rad'][name])
    for name in set(names)-set(POSTURE_JOINT_NAMES):
        assert target[names.index(name)] == b.robot.q[names.index(name)]
    b.robot.q = target.clone()
    result = engine.verify_joint_goal(goal, dict(b._gripper_commands))
    assert result['verified'] and set(result['retention']) == {'left', 'right'}
    assert b._retention.call_count == 2
    b.robot.q[names.index('left_arm_joint4')] += .08
    assert engine.verify_joint_goal(goal, dict(b._gripper_commands))['reason'] == 'joint_posture_not_reached'
    b.robot.q = target.clone()
    b._retention.return_value = {'retained': False}
    assert engine.verify_joint_goal(goal, dict(b._gripper_commands))['reason'] == 'native_retention_failed'


def test_native_joint_limits_are_checked_without_clamping_or_sending_commands(tmp_path):
    engine, b, _ = adapter(tmp_path)
    engine.execution_timing = 'model_estimated_v1'
    goal = recovery().generate('', oracle(), {})['phases'][0]['goal']
    before = b.robot.q.clone()
    goal['joint_positions_rad']['torso_joint1'] = 8.
    with pytest.raises(SkillError, match='mechanical limits'): engine.goal_vector(goal)
    assert b.torch.equal(before, b.robot.q)


def test_default_posture_policy_accepts_out_of_range_goal_without_mutating_native_limits(tmp_path):
    engine, b, names = adapter(tmp_path)
    goal = recovery().generate('', oracle(), {})['phases'][0]['goal']
    goal['joint_positions_rad']['torso_joint1'] = 8.
    lower, upper, before = b.robot.joint_lower_limits.clone(), b.robot.joint_upper_limits.clone(), b.robot.q.clone()
    assert engine.execution_timing == 'model_authored_v1'
    target = engine.goal_vector(goal)
    assert target[names.index('torso_joint1')] == 8.
    assert b.torch.equal(before, b.robot.q)
    assert b.torch.equal(lower, b.robot.joint_lower_limits) and b.torch.equal(upper, b.robot.joint_upper_limits)


@pytest.mark.parametrize('status', ['success', 'reject', 'exception', 'second_attachment_error'])
def test_joint_planner_uses_joint_space_actual_payloads_and_always_detaches(tmp_path, monkeypatch, status):
    engine, b, names = adapter(tmp_path)
    th = b.torch
    class JointState:
        def __init__(self, position, joint_names): self.position, self.joint_names = position, list(joint_names)
        from_position = classmethod(lambda cls, position, joint_names: cls(position, joint_names))
        def get_ordered_joint_state(self, ordered):
            return JointState(self.position[:, [self.joint_names.index(n) for n in ordered]], ordered)
    modules = {'curobo': ModuleType('curobo'), 'curobo.types': ModuleType('curobo.types'),
        'curobo.types.state': ModuleType('curobo.types.state'), 'curobo.wrap': ModuleType('curobo.wrap'),
        'curobo.wrap.reacher': ModuleType('curobo.wrap.reacher'),
        'curobo.wrap.reacher.motion_gen': ModuleType('curobo.wrap.reacher.motion_gen')}
    modules['curobo.types.state'].JointState = JointState
    modules['curobo.wrap.reacher.motion_gen'].MotionGenPlanConfig = lambda **kw: SimpleNamespace(**kw)
    for name, value in modules.items(): monkeypatch.setitem(sys.modules, name, value)
    full = JointState(b.robot.q[None].repeat(3, 1), names)
    result = SimpleNamespace(success=th.tensor([status == 'success']), status=status,
        get_interpolated_plan=lambda: full, interpolation_dt=1/30,
        get_paths=Mock(side_effect=AssertionError('Single joint plans must not use the batched result API')))
    mg = SimpleNamespace(kinematics=SimpleNamespace(joint_names=list(POSTURE_JOINT_NAMES)),
        plan_single_js=Mock(return_value=result, side_effect=RuntimeError('planner error') if status == 'exception' else None),
        get_full_js=lambda q: q, interpolation_dt=1/30)
    engine.mg, engine.emb = mg, 'default'
    attached = []
    engine.planner = SimpleNamespace(robot_joint_names=names, tensor_args=SimpleNamespace(to_device=lambda x: x),
        update_obstacles=Mock(), update_locked_joints=Mock(),
        _attach_objects_to_robot=Mock(side_effect=lambda objects, scales, start, emb: attached.append((objects, scales)) or [dict(objects=objects)]),
        _detach_objects_from_robot=Mock())
    if status == 'second_attachment_error':
        engine.planner._attach_objects_to_robot.side_effect = [[{'first_payload': True}], RuntimeError('attach error')]
    b.og = SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: 1/30))
    engine.finish_planned_path = Mock(return_value=('validated_path', {}))
    goal = recovery().generate('', oracle(), {})['phases'][0]['goal']
    if status == 'second_attachment_error':
        with pytest.raises(RuntimeError, match='attach error'): engine.plan_goal(goal)
        assert engine.planner._detach_objects_from_robot.call_args.args[0] == [{'first_payload': True}]
        assert not mg.plan_single_js.called
        return
    if status == 'exception':
        with pytest.raises(RuntimeError, match='planner error'): engine.plan_goal(goal)
    else:
        path, planning = engine.plan_goal(goal)
        assert path == ('validated_path' if status == 'success' else None)
        assert engine.finish_planned_path.call_count == (1 if status == 'success' else 0)
    start, end, config = mg.plan_single_js.call_args.args
    assert start.joint_names == list(POSTURE_JOINT_NAMES)
    assert end.position[0].tolist() == pytest.approx([goal['joint_positions_rad'][n] for n in POSTURE_JOINT_NAMES])
    assert attached == [({'left_eef': 'left_link'}, {'left_eef': 1.}), ({'right_eef': 'right_link'}, {'right_eef': 1.})]
    assert len(engine.planner._detach_objects_from_robot.call_args.args[0]) == 2
    if status == 'success':
        args = engine.finish_planned_path.call_args.args
        assert args[4]['attached_obj'] == engine.attachments and args[5] is None
        assert not result.get_paths.called


@pytest.mark.parametrize('same_object', [True, False])
def test_bimanual_retention_of_one_object_requires_constrained_planning(tmp_path, monkeypatch, same_object):
    engine, _, _ = adapter(tmp_path)
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    first = object()
    engine.retained = {'left': (first, 'left_link'), 'right': (first if same_object else object(), 'right_link')}
    monkeypatch.setattr(ModelPoseManip, 'preflight', lambda *a: None)
    if same_object:
        with pytest.raises(SkillError, match='constrained dual-arm'): engine.preflight(None, 'hold')
    else: engine.preflight(None, 'hold')


def test_existing_pose_entrypoint_dispatches_both_arms_to_joint_template(tmp_path, monkeypatch):
    from manipulation_agent.executors.oracle_joint_posture import ModelJointPosture
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    run = Mock(return_value={'verified': True})
    monkeypatch.setattr(ModelJointPosture, 'execute_phases', run)
    phase = recovery().generate('', oracle(), {})['phases'][0]
    assert ModelPoseManip(SimpleNamespace(output=tmp_path)).execute(**{k: v for k, v in phase.items() if k != 'name'})['verified']
    assert run.call_args.args[0][0]['goal'] == phase['goal']


def test_full_body_stream_updates_all_three_groups_and_failure_rolls_back(tmp_path):
    th = pytest.importorskip('torch')
    from test_oracle_open_loop import backend
    from manipulation_agent.executors.oracle_open_loop import OpenLoopPickTrial
    b, event, _, commands, _ = backend(tmp_path)
    b.robot.controller_action_idx['arm_left'] = b.robot.arm_control_idx['left']
    b._model_posture_planners = {'both': object()}
    before = b.robot.q.clone()
    path = th.stack([before, before+th.tensor([.03, -.03, .05, -.05, .08, -.08])])
    with OpenLoopPickTrial(b, event) as trial:
        trial.stream(path, 'both')
        assert th.allclose(b.robot.q, path[-1]) and th.allclose(b._command_q, path[-1])
        # Leave unaccepted, exercising the same whole-call rollback as EEF Pose.
    assert len(commands) == 1 and th.equal(b.robot.q, before)
    assert trial.manifest['rollback'] == 'restored_and_verified' and b._model_posture_planners == {}


def test_shared_recovery_source_freezes_and_generates_in_existing_sandbox(tmp_path):
    from manipulation_agent.geometry_library import GeometryProgramLibrary, checked_source
    source = SOURCE.read_text()
    shared = tmp_path/'shared'
    release = GeometryProgramLibrary(shared).publish(dict(program='recover_body', source=source,
        source_sha256=checked_source(source), author='test_fixture', description='CPU recovery fixture'), None)
    episode = ManipPrograms(tmp_path/'episode', library_root=shared)
    phases, evidence = episode.generate('recover_body', release['source_sha256'], task='recover',
        oracle=oracle('left_payload', 'right_payload'), parameters={'control_steps': 90}, output=tmp_path/'generated')
    assert phases[0]['arm'] == 'both' and phases[0]['control_steps'] == 90
    assert evidence['sandbox'] == 'bubblewrap_offline_no_gpu_no_robot'
    assert evidence['library_release']['library_commit'] == release['library_release']['library_commit']
