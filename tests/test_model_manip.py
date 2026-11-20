"""Contract/sandbox/transaction tests; synthetic data, no native robot actions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.manip_programs import ManipPrograms, checked_invocation, checked_phases
from manipulation_agent.oracle_contracts import tool_specs
from manipulation_agent.oracle_harness import OracleWaypointHarness
from manipulation_agent.records import Recorder
from manipulation_agent.vision_policy import system_prompt


def phase(name='approach', gripper='hold'):
    return {'name': name, 'arm': 'right', 'frame': 'robot_base',
            'goal': {'position_m': [.4, -.2, .8], 'quaternion_xyzw': [0., 0., 0., 1.]},
            'gripper': gripper, 'contact_target': None}


def pose_call():
    return {'mode': 'pose', 'revision': 0, **{k: v for k, v in phase().items() if k != 'name'}}


SOURCE = '''def generate(task, oracle, parameters):
    p = list(oracle["objects"][0]["pose"]["position_m"])
    p[2] += parameters["offset_m"]
    return {"phases": [{"name": "lift" if "lift" in task else "approach",
        "arm": "right", "frame": "robot_base",
        "goal": {"position_m": p, "quaternion_xyzw": [0, 0, 0, 1]},
        "gripper": "hold", "contact_target": None}]}
'''


def test_shared_manip_accepts_program_or_direct_goal_without_changing_legacy(monkeypatch):
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    catalog = {t['name']: t for t in tool_specs()}
    assert {'write_manip_program', 'read_manip_program', 'list_manip_programs', 'manip'} <= catalog.keys()
    assert not {'grasp', 'move_joints', 'execute_waypoints', 'list_manip_recipes'} & catalog.keys()
    checked_invocation(pose_call())
    checked_invocation({'mode': 'program', 'revision': 0, 'program': 'side_grasp',
                        'source_sha256': 'a'*64, 'parameters': {}})
    assert 'SIMPLE lift' in system_prompt('oracle_waypoints')
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'geometry_pick_v1')
    old = next(t for t in tool_specs() if t['name'] == 'manip')
    assert old['inputSchema']['required'] == ['operation', 'target', 'arm', 'lift_m', 'revision']


@pytest.mark.parametrize('change', [lambda a: a.update(program='mixed'),
    lambda a: a.pop('goal'), lambda a: a.update(arm='auto'), lambda a: a.update(mode='unknown')])
def test_mixed_or_incomplete_modes_fail(change):
    args = pose_call(); change(args)
    with pytest.raises(SkillError):
        checked_invocation(args)


def test_source_versions_are_immutable_and_conflicts_preserve_existing(tmp_path):
    library = ManipPrograms(tmp_path)
    first = library.write('grasp', SOURCE, 'draft algorithm', None)
    with pytest.raises(SkillError, match='current source hash'):
        library.write('grasp', SOURCE+'\n', 'edit', None)
    second = library.write('grasp', SOURCE+'\n', 'new draft', first['source_sha256'])
    assert second['source_sha256'] != first['source_sha256']
    assert (tmp_path / 'grasp' / first['source_sha256'] / 'source.py').read_text() == SOURCE
    assert library.read('grasp')['source'] == SOURCE+'\n'
    assert library.catalog()[0]['status'] == 'draft'
    with pytest.raises(SkillError):
        library.write('../escape', SOURCE, '', None)


def test_real_offline_geometry_sandbox_uses_task_oracle_and_parameters(tmp_path):
    library = ManipPrograms(tmp_path / 'library')
    entry = library.write('geometry', SOURCE, 'CPU test source, not a model trial', None)
    phases, evidence = library.generate('geometry', entry['source_sha256'], task='lift object',
        oracle={'objects': [{'pose': {'position_m': [.4, -.2, .8]}}]},
        parameters={'offset_m': .1}, output=tmp_path)
    assert phases[0]['name'] == 'lift'
    assert phases[0]['goal']['position_m'] == pytest.approx([.4, -.2, .9])
    assert evidence['sandbox'] == 'bubblewrap_offline_no_gpu_no_robot'
    assert json.loads(Path(evidence['input']).read_text())['task'] == 'lift object'


def test_sandbox_exception_and_old_hash_fail_before_any_motion(tmp_path):
    library = ManipPrograms(tmp_path / 'library')
    source = 'def generate(task, oracle, parameters):\n    import omnigibson\n'
    entry = library.write('bad', source, 'negative test', None)
    with pytest.raises(SkillError, match='Geometry program failed'):
        library.generate('bad', entry['source_sha256'], task='', oracle={}, parameters={}, output=tmp_path)
    with pytest.raises(SkillError, match='Program changed'):
        library.generate('bad', '0'*64, task='', oracle={}, parameters={}, output=tmp_path)
    assert next(tmp_path.glob('geometry_program_*/result.json')).is_file()


def test_whole_generated_batch_is_validated_including_late_goals():
    a, b = phase(), phase('lift')
    for key, bad in [('quaternion_xyzw', [0, 0, 0, 0]), ('position_m', [0, 0, float('nan')])]:
        invalid = deepcopy(b); invalid['goal'][key] = bad
        with pytest.raises(SkillError):
            checked_phases({'phases': [a, invalid]})
    with pytest.raises(SkillError):
        checked_phases({'phases': [a, dict(b, arm='left')]})
    with pytest.raises(SkillError):
        checked_phases({'phases': [phase(gripper='close')]})


def test_harness_direct_mode_skips_program_runtime_and_deduplicates(tmp_path, monkeypatch):
    from test_oracle_waypoints import FakeBackend
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    backend = FakeBackend()
    harness = OracleWaypointHarness(backend, Recorder(tmp_path / 'episode', {}), Budget(wall_seconds=None))
    harness.manip_programs.generate = Mock(side_effect=AssertionError('Simple pose should not run code'))
    reply = harness.call('manip', pose_call(), 'same')
    assert reply['ok']
    assert harness.call('manip', pose_call(), 'same') == reply
    assert len(backend.moves) == 1
    assert backend.moves[0][1]['goal'] == pose_call()['goal']
    assert harness.call('manip', pose_call(), 'stale')['error']['code'] == 'stale_observation'


def test_harness_program_selection_passes_exact_current_geometry_and_one_batch(tmp_path, monkeypatch):
    from test_oracle_waypoints import FakeBackend
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    backend = FakeBackend()
    harness = OracleWaypointHarness(backend, Recorder(tmp_path / 'episode', {'instruction': 'pick then lift'}),
                                   Budget(wall_seconds=None))
    generate = harness.manip_programs.generate = Mock(return_value=([phase(), phase('lift')], {'program': 'chosen'}))
    reply = harness.call('manip', {'mode': 'program', 'program': 'chosen', 'source_sha256': 'a'*64,
                                 'parameters': {'lift_m': .1}, 'revision': 0}, 'program-call')
    assert reply['ok']
    assert generate.call_args.kwargs['task'] == 'pick then lift'
    assert generate.call_args.kwargs['oracle'] == backend.observe()['oracle_3d']
    assert backend.moves[0][0] == 'execute_model_phases'
    assert len(backend.moves[0][1]['phases']) == 2


def test_exploration_frontend_keeps_program_manip_schema(monkeypatch):
    from manipulation_agent.exploration_frontend import tool_specs as front_specs
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    rows = {r['name']: r for r in front_specs(tool_specs())}
    assert 'mode' in rows['manip']['inputSchema']['properties']
    assert 'list_manip_recipes' not in rows


@pytest.mark.parametrize('fail_later', [False, True])
def test_multiple_phases_share_one_checkpoint_and_failure_restores_start(tmp_path, monkeypatch, fail_later):
    torch = pytest.importorskip('torch')
    from test_oracle_open_loop import backend as make_backend
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    b, event, reads, applications, events = make_backend(tmp_path)
    sim, robot = b.og.sim, b.robot
    engine = ModelPoseManip(b)
    engine.preflight = Mock()
    engine.get_planner = Mock()
    # Native stream starts at measured state; make the planned paths continuous.
    paths = [torch.stack([torch.zeros(6), torch.full((6,), .01), torch.full((6,), .02)]),
             torch.stack([torch.full((6,), .02), torch.full((6,), .03), torch.full((6,), .04)])]
    def plan(pose, initial=None):
        assert initial is None
        index = len(applications) // 2
        assert engine.verify.call_count == index
        if fail_later and index == 1:
            return None, {'success': False}
        return paths[index], {'success': True}
    engine.plan_goal = Mock(side_effect=plan)
    robot.root_link = SimpleNamespace(get_position_orientation=lambda:
        (torch.tensor([100., 200., 0.]), torch.tensor([0., 0., 0., 1.])))
    engine.endpoint = Mock(return_value={'reached': True})
    engine.verify = Mock(return_value={'reached': True, 'verified': True, 'reason': None})
    original = robot.q.clone()
    result = engine.execute_phases([phase(), phase('lift')], program={'program': 'synthetic'})
    assert torch.equal(engine.reference[0], robot.get_position_orientation()[0])
    assert result['verified'] is (not fail_later)
    assert engine.plan_goal.call_count == 2
    assert len(list(tmp_path.glob('open_loop_trials/*/checkpoint.pt'))) == 1
    assert sim.loads == int(fail_later)
    assert b.steps > 10  # Failed-attempt accounting does not rewind.
    if fail_later:
        assert torch.equal(robot.q, original)
        assert result['execution']['rollback'] == 'restored_and_verified'
    assert {'spectator', 'left_wrist', 'right_wrist'} == set(reads)


def test_endpoint_uses_world_eef_and_call_start_base_not_relative_twice(tmp_path):
    torch = pytest.importorskip('torch')
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    ref = (torch.tensor([10., 0, 0]), torch.tensor([0., 0, 0, 1.]))
    eef = SimpleNamespace(get_position_orientation=lambda: (torch.tensor([10.4, -.2, .8]), ref[1]))
    b = SimpleNamespace(output=tmp_path, robot=SimpleNamespace(eef_links={'right': eef}),
        _relative=lambda world, base: (world[0]-base[0], world[1]),
        _pose=lambda pose: {'position_m': pose[0].tolist(), 'quaternion_xyzw': pose[1].tolist()})
    engine = ModelPoseManip(b); engine.arm = 'right'; engine.reference = ref
    assert engine.endpoint(dict(phase()['goal'], gripper='hold'))['reached']
    eef.get_position_orientation = lambda: (torch.tensor([10.5, -.2, .8]), ref[1])
    assert not engine.endpoint(dict(phase()['goal'], gripper='hold'))['reached']


def test_curobo_receives_only_authored_eef_goal_and_rechecks_collisions(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    from manipulation_agent.executors.oracle_pose import ModelPoseManip
    transform = ModuleType('omnigibson.utils.transform_utils')
    transform.pose_transform = lambda p, q, target, quat: (p+target, quat)
    transform.relative_pose_transform = lambda p, q, base, rot: (p-base, q)
    for name in ('omnigibson', 'omnigibson.utils'):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.transform_utils', transform)
    root = SimpleNamespace(get_position_orientation=lambda: (torch.zeros(3), torch.tensor([0., 0, 0, 1.])))
    b = SimpleNamespace(output=tmp_path, torch=torch, steps=0, robot=SimpleNamespace(
        root_link=root, eef_link_names={'right': 'right_eef'},
        joints=dict.fromkeys('abcdef'), max_joint_velocities=torch.full((6,), 10.)),
        og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: .1)))
    engine = ModelPoseManip(b); engine.arm = 'right'
    engine.reference = (torch.tensor([10., 5., 0.]), torch.tensor([0., 0., 0., 1.]))
    engine.attachments = {}; engine.contact_link = None
    full = SimpleNamespace(position=torch.zeros((3, 6)))
    full.get_ordered_joint_state = lambda names: full
    result = SimpleNamespace(success=torch.tensor([True]), status='SUCCESS', attempts=1, trajopt_attempts=1,
                             get_paths=lambda: [object()])
    engine.mg = SimpleNamespace(interpolation_dt=.1, get_full_js=lambda path: full)
    limits = SimpleNamespace(joint_names=list('abcdef'), **{name: torch.tensor([[-10.]*6, [10.]*6])
        for name in ('velocity', 'acceleration', 'jerk')})
    engine.mg.robot_cfg = SimpleNamespace(kinematics=SimpleNamespace(get_joint_limits=lambda: limits,
        cspace=SimpleNamespace(joint_names=list('abcdef'),
            max_acceleration=torch.full((6,), 15.), max_jerk=torch.full((6,), 500.))))
    engine.planner = SimpleNamespace(update_obstacles=Mock(), compute_trajectories=Mock(return_value=[result]),
        check_collisions=Mock(return_value=torch.tensor([False])), robot_joint_names=list('abcdef'))
    q, info = engine.plan_goal(dict(phase()['goal'], gripper='hold'))
    args = engine.planner.compute_trajectories.call_args.kwargs
    assert set(args['target_pos']) == set(args['target_quat']) == {'right_eef'}
    assert args['initial_joint_pos'] is None  # Pinned OG reads measured robot joints.
    assert args['target_pos']['right_eef'].tolist() == pytest.approx([10.4, 4.8, .8])
    assert not args['is_local'] and info['goal_count'] == 1
    assert engine.planner.check_collisions.call_count == 1 and q is not None
    predicted_start = torch.zeros(6)
    engine.plan_goal(dict(phase()['goal'], gripper='hold'), initial=predicted_start)
    assert engine.planner.compute_trajectories.call_args.kwargs['initial_joint_pos'] is predicted_start
    assert engine.planner.check_collisions.call_args.kwargs['initial_joint_pos'] is predicted_start
    engine.planner.check_collisions.return_value = torch.tensor([True])
    assert engine.plan_goal(dict(phase()['goal'], gripper='hold'))[0] is None
