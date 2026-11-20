"""Synthetic contracts and native-adapter state machine; no simulator trial."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.manip_programs import ManipPrograms, checked_output
from manipulation_agent.manip_references import catalog, read
from manipulation_agent.oracle_harness import OracleWaypointHarness
from manipulation_agent.records import Recorder
from test_model_manip import phase


def batch():
    return {'candidates': [
        {'name': 'first', 'score': 0., 'phases': [phase('pre'), phase('contact')]},
        {'name': 'second', 'score': 1., 'phases': [phase('pre'), phase('contact')]}]}


@pytest.mark.parametrize('change', [
    lambda b: b['candidates'][-1]['phases'][-1]['goal'].update(quaternion_xyzw=[0, 0, 0, 0]),
    lambda b: b['candidates'][0]['phases'][0].update(gripper='open'),
    lambda b: b['candidates'][0].update(score=float('nan')),
    lambda b: b['candidates'][1].update(name='first'),
    lambda b: b.update(phases=[phase()]),
])
def test_whole_candidate_batch_rejects_invalid_late_goals_and_midpath_gripper(change):
    value = batch(); change(value)
    with pytest.raises(SkillError):
        checked_output(value)


def test_original_reference_is_available_without_claiming_model_authorship():
    entry = read('bottle_grasps')
    assert entry['author'] == 'harness_reference'
    assert 'for fraction in (.50, .65, .78, .87, .35)' in entry['source']
    assert catalog()[0]['source_sha256'] == entry['source_sha256']


def test_existing_program_can_be_selected_and_adapted_without_a_new_wrapper(tmp_path):
    lib = ManipPrograms(tmp_path/'library')
    current = lib.read('bottle_grasps')
    assert lib.catalog()[0]['program'] == 'bottle_grasps'
    assert current['author'] == 'harness_reference'
    assert 'def generate(' in current['source']
    with pytest.raises(SkillError, match='current source hash'):
        lib.write('bottle_grasps', current['source']+'\n', 'edit', None)
    edited = lib.write('bottle_grasps', current['source']+'\n', 'adapted', current['source_sha256'])
    assert edited['author'] == 'model'
    assert lib.read('bottle_grasps')['source_sha256'] == edited['source_sha256']
    assert (lib.root/'bottle_grasps'/current['source_sha256']/'source.py').read_text() == current['source']


@pytest.mark.parametrize('yaw', [0., 2.1])
def test_selectable_old_program_preserves_candidate_positions_rotations_and_authorship(tmp_path, monkeypatch, yaw):
    from scipy.spatial import ConvexHull
    from manipulation_agent.grasp_geometry import bottle_grasps
    vertices = np.array([[x, y, z] for x in (-.02, .02) for y in (-.02, .02) for z in (0., .3)])
    faces = ConvexHull(vertices).simplices
    rotation = np.array([[np.cos(yaw), -np.sin(yaw), 0.],
                         [np.sin(yaw), np.cos(yaw), 0.], [0., 0., 1.]])
    origin = np.array([7.4, .05, .005])
    oracle = {'frame': 'robot_base', 'objects': [{'id': 'bottle', 'links': [{'name': 'base_link'}],
        'collision_mesh': {'frame': 'robot_base', 'vertices_m': vertices.tolist(), 'faces': faces.tolist()}}],
        'robot': {'base_pose_world': {'position_m': origin.tolist(),
                     'quaternion_xyzw': [0., 0., np.sin(yaw/2), np.cos(yaw/2)]},
          'arms': {'right': {'held_object': None, 'gripper_command': 1.,
            'eef_pose': {'position_m': [.4, 0, .2]},
            'grasp_geometry': {'contact_offset_eef_m': [0, 0, .02], 'aperture_m': .09}}}}}
    lib = ManipPrograms(tmp_path/'library'); existing = lib.read('bottle_grasps')
    monkeypatch.setattr('manipulation_agent.manip_programs.subprocess.run',
                        Mock(side_effect=AssertionError('Trusted builtin must reuse native NumPy')))
    actual, evidence = lib.generate('bottle_grasps', existing['source_sha256'], task='pick',
        oracle=oracle, parameters={'object_id': 'bottle', 'arm': 'right',
                                  'control_steps': {'pregrasp': 85, 'contact_close': 40}}, output=tmp_path)
    expected = bottle_grasps(vertices @ rotation.T + origin,
        np.array([.4, 0, .2]) @ rotation.T + origin, [0, 0, .02], .09, faces=faces)
    assert len(actual['candidates']) == len(expected) == 70
    for candidate, original in zip(actual['candidates'], expected):
        pre, close = candidate['phases']
        assert pre['control_steps'] == 85 and close['control_steps'] == 40
        assert np.allclose(pre['goal']['position_m'], (original.pregrasp-origin) @ rotation)
        assert np.allclose(close['goal']['position_m'], (original.eef-origin) @ rotation)
        x,y,z,w = close['goal']['quaternion_xyzw']
        actual_rotation = np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
            [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
            [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        assert np.allclose(rotation @ actual_rotation, original.rotation)
        assert close['gripper'] == 'close' and pre['gripper'] == 'hold'
    assert evidence['author'] == 'harness_reference' and evidence['reference_used']
    assert evidence['sandbox'] == 'trusted_builtin_in_process'


def test_real_sandbox_can_reuse_numpy_reference_and_preserves_ranked_outputs(tmp_path):
    # Test-authored adapter exercises the actual old geometry kernel; it is not
    # presented as a model-generated program or as native grasp evidence.
    source = '''import numpy as np
from manip_reference import bottle_grasps
def generate(task, oracle, parameters):
    obj=oracle['objects'][0]
    cs=bottle_grasps(obj['collision_mesh']['vertices_m'], [.4,0,.3], [0,0,.02], .09)
    # Only positions matter to this sandbox/contract test, orientation is identity.
    def p(name,xyz):
        return dict(name=name,arm='right',frame='robot_base',
            goal=dict(position_m=xyz.tolist(),quaternion_xyzw=[0,0,0,1]),
            gripper='hold',contact_target=None)
    return dict(candidates=[dict(name='c'+str(i),score=c.score,
        phases=[p('pre',c.pregrasp),p('contact',c.eef)]) for i,c in enumerate(cs)])
'''
    from test_geometry_pick import bottle
    from manipulation_agent.grasp_geometry import bottle_grasps
    points = bottle()
    expected = bottle_grasps(points, [.4, 0, .3], [0, 0, .02], .09)
    lib = ManipPrograms(tmp_path/'programs')
    old = lib.read('bottle_grasps')
    entry = lib.write('bottle_grasps', source, 'test adapter', old['source_sha256'])
    generated, evidence = lib.generate('bottle_grasps', entry['source_sha256'], task='test only',
        oracle={'objects': [{'collision_mesh': {'vertices_m': points.tolist()}}]}, parameters={}, output=tmp_path)
    assert len(generated['candidates']) == len(expected) == 70
    assert np.allclose(generated['candidates'][0]['phases'][-1]['goal']['position_m'], expected[0].eef)
    assert evidence['reference_imported'] is True
    assert evidence['author'] == 'model'
    assert evidence['sandbox'] == 'bubblewrap_offline_no_gpu_no_robot'
    assert evidence['setup_seconds'] >= 0 and evidence['computation_seconds'] >= 0
    assert evidence['available_references'][0]['author'] == 'harness_reference'


def test_mesh_queries_do_not_move_and_only_enrich_same_revision(tmp_path, monkeypatch):
    from test_oracle_waypoints import FakeBackend
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    backend = FakeBackend()
    original = backend.observe
    def observe():
        value = original(); value['oracle_3d']['objects'] = [{'id': 'bottle'}]; return value
    backend.observe = observe
    backend.get_manip_geometry = Mock(return_value={'frame': 'robot_base', 'objects': [
        {'id': 'bottle', 'collision_mesh': {'vertices_m': [[1, 2, 3]], 'faces': []}}]})
    harness = OracleWaypointHarness(backend, Recorder(tmp_path/'episode', {}), Budget(wall_seconds=None))
    result = harness.call('get_manip_geometry', {'object_ids': ['bottle'], 'revision': 0}, 'mesh')
    assert result['ok'] and backend.steps == 0 and not backend.moves and harness.revision == 0
    generate = harness.manip_programs.generate = Mock(return_value=(batch(), {'program': 'test'}))
    args = {'mode': 'program', 'program': 'test', 'source_sha256': 'a'*64, 'parameters': {}, 'revision': 0}
    assert harness.call('manip', args, 'run')['ok']
    assert 'collision_mesh' in generate.call_args.kwargs['oracle']['objects'][0]
    assert isinstance(backend.moves[0][1]['phases'], dict)
    args['revision'] = 1
    assert harness.call('manip', args, 'run2')['ok']
    assert 'collision_mesh' not in generate.call_args.kwargs['oracle']['objects'][0]
    assert harness.call('get_manip_geometry', {'object_ids': ['bottle'], 'revision': 0}, 'stale')['error']['code'] == 'stale_observation'


def test_native_mesh_export_uses_base_frame_and_original_contact_geometry_without_steps(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    from manipulation_agent.executors.oracle_geometry import OracleGeometry
    from manipulation_agent.executors.oracle_pick import GeometryPick
    transform = ModuleType('omnigibson.utils.transform_utils')
    transform.quat2mat = lambda q: torch.tensor([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    for name in ('omnigibson', 'omnigibson.utils'):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.transform_utils', transform)
    monkeypatch.setattr(GeometryPick, 'mesh', Mock(return_value=(np.array([[10., 6., 1.]]), np.array([[0, 0, 0]]))))
    monkeypatch.setattr(GeometryPick, 'gripper_geometry', Mock(return_value=(np.array([0., 0., .02]), .09)))
    b = SimpleNamespace(output=tmp_path, robot=SimpleNamespace(arm_names=['right'],
        get_position_orientation=lambda: (torch.tensor([10., 5., 0.]), torch.tensor([0., 0., 0., 1.]))),
        _resolve_object=Mock(return_value=object()), step=Mock(), observe=Mock(), render=Mock())
    result = OracleGeometry.get_manip_geometry(b, ['bottle'])
    assert result['objects'][0]['collision_mesh']['vertices_m'] == [[1., 0., 1.]]
    assert result['robot']['arms']['right']['grasp_geometry']['contact_offset_eef_m'] == [0., 0., .02]
    assert result['robot']['arms']['right']['grasp_geometry']['aperture_m'] == .09
    assert not b.step.called and not b.observe.called and not b.render.called


@pytest.mark.parametrize('mode', ['success', 'reject_first', 'reject_all', 'late_plan_failure',
                                 'endpoint_failure', 'late_endpoint_failure', 'retention_failure'])
def test_plan_execute_verify_each_pose_from_measured_state_without_late_candidate_retry(tmp_path, mode):
    torch = pytest.importorskip('torch')
    from test_oracle_open_loop import backend as make_backend
    from manipulation_agent.executors.oracle_candidates import ModelCandidateManip
    b, _, reads, applications, _ = make_backend(tmp_path)
    # Holonomic root and the observed mobile base are distinct after navigation.
    b.robot.root_link = SimpleNamespace(get_position_orientation=lambda:
        (torch.tensor([100., 200., 0.]), torch.tensor([0., 0., 0., 1.])))
    engine = ModelCandidateManip(b); engine.preflight = Mock(); engine.get_planner = Mock()
    engine.validate_execution_join = Mock(side_effect=AssertionError('No preplanned segment batch'))
    engine.retained = {}
    order, starts, planned_paths = [], [], []
    def plan(pose, initial=None):
        assert torch.equal(engine.reference[0], b.robot.get_position_orientation()[0])
        order.append(engine.phase); starts.append(initial)
        assert initial is None  # cuRobo reads actual joints, never the predicted last waypoint.
        if engine.phase.endswith('/contact/plan'):
            assert len(applications) == 2
            assert engine.verify.call_count == 1  # Previous pose was executed AND verified.
            assert not torch.equal(b.robot.q, planned_paths[-1][-1])
        else:
            assert not applications
        if (mode == 'reject_all' or (mode == 'reject_first' and len(order) == 1)
                or (mode == 'late_plan_failure' and len(order) == 2)):
            return None, {'success': False, 'status': 'IK_FAIL'}
        measured = b.robot.get_joint_positions()
        q = torch.stack([measured, measured+.01, measured+.02])
        planned_paths.append(q)
        return q, {'success': True}
    engine.plan_goal = plan
    def endpoint(pose):
        reached = not (mode == 'endpoint_failure' or
                       (mode == 'late_endpoint_failure' and len(order) == 2))
        return {'reached': reached}
    engine.endpoint = Mock(side_effect=endpoint)
    def verify(pose, gripper):
        retained = mode != 'retention_failure'
        return {'reached': True, 'verified': retained,
                'reason': None if retained else 'native_retention_failed'}
    engine.verify = Mock(side_effect=verify)
    before = b.robot.q.clone()
    result = engine.execute_candidates(batch(), program={'program': 'test', 'author': 'harness_reference'})
    assert result['goal_source'] == 'model_selected_program'
    if mode in ('success', 'reject_first'):
        assert result['verified']
        assert result['selected_candidate'] == ('second' if mode == 'reject_first' else 'first')
        assert len(order) == (3 if mode == 'reject_first' else 2)
        assert len(applications) == 4
        assert [row['name'] for row in result['phases']] == ['pre', 'contact']
        assert all(row['verified'] for row in result['phases'])
    elif mode == 'reject_all':
        assert not applications and not result['verified']
        assert len(order) == 2 and result['reason'] == 'no_reachable_candidate'
        assert result['execution']['rollback'] == 'unchanged_no_physics_steps'
    else:
        first_phase_failure = mode in ('endpoint_failure', 'retention_failure')
        assert len(order) == (1 if first_phase_failure else 2)
        assert len(result['candidate_attempts']) == 1 and result['selected_candidate'] == 'first'
        assert not result['verified']
        assert len(applications) == (2 if first_phase_failure or mode == 'late_plan_failure' else 4)
        assert result['execution']['rollback'] == 'restored_and_verified'
        assert torch.equal(b.robot.q, before)
        assert result['phases'][-1]['reason'] == ('model_goal_planning_failed' if mode == 'late_plan_failure'
            else 'native_retention_failed' if mode == 'retention_failure' else 'eef_goal_not_reached')
    assert all(value is None for value in starts)
    assert result['execution']['fully_preplanned'] is False
    assert result['execution']['selection_basis'] == 'first_plannable_phase'
    assert not engine.validate_execution_join.called
    assert len(list(tmp_path.glob('open_loop_trials/*/checkpoint.pt'))) == 1
    assert set(reads) == {'spectator', 'left_wrist', 'right_wrist'}


def test_each_candidate_pose_preserves_duration_contact_and_terminal_gripper_order(tmp_path):
    torch = pytest.importorskip('torch')
    from test_oracle_open_loop import backend as make_backend
    from manipulation_agent.executors.oracle_candidates import ModelCandidateManip
    b, _, _, applications, _ = make_backend(tmp_path)
    engine = ModelCandidateManip(b)
    engine.get_planner = Mock()
    engine.retained = {}
    order = []
    def preflight(target, command):
        order.append(('preflight', target, command, len(applications)))
    engine.preflight = Mock(side_effect=preflight)
    def plan(pose, initial=None):
        order.append(('plan', engine.requested_control_steps, len(applications)))
        q = b.robot.get_joint_positions()
        return torch.stack([q, q+.01, q+.02]), {'success': True}
    engine.plan_goal = plan
    engine.endpoint = Mock(return_value={'reached': True})
    def verify(pose, command):
        order.append(('verify', command, len(applications)))
        return {'reached': True, 'verified': True, 'reason': None}
    engine.verify = Mock(side_effect=verify)
    b._set_gripper = Mock(side_effect=lambda arm, command: order.append(('gripper', command, len(applications))))
    pre, close = phase('pre'), phase('close', 'close')
    pre['control_steps'], close['control_steps'] = 85, 40
    close['contact_target'] = {'object_id': 'bottle', 'link_name': 'base_link'}
    result = engine.execute_candidates({'candidates': [{'name': 'grasp', 'score': 0., 'phases': [pre, close]}]},
                                      program={'program': 'bottle_grasps'})
    assert result['verified']
    assert [item for item in order if item[0] != 'preflight'] == [
        ('plan', 85, 0), ('verify', 'hold', 2), ('plan', 40, 2),
        ('gripper', 'close', 4), ('verify', 'close', 4)]
    assert ('preflight', close['contact_target'], 'close', 2) in order
    events = [json.loads(line) for line in (tmp_path/'model_pose.jsonl').read_text().splitlines()]
    goals = [row for row in events if row['kind'] == 'pose_phase_goal']
    assert [row['pose_operation'] for row in goals] == [
        dict(mode='pose', **{k: v for k, v in item.items() if k != 'name'}) for item in (pre, close)]
    assert [row['sim_step'] for row in goals] == [10, 12]
    assert all(row['operation_scope'] == 'internal_pose_template' for row in goals)
    assert [row['phase_name'] for row in events if row['kind'] == 'pose_phase_result'] == ['pre', 'close']
