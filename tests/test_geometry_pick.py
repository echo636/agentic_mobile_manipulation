import numpy as np
import pytest
from manipulation_agent.grasp_geometry import bottle_grasps, resample_positions, body_path_envelope
from manipulation_agent.oracle_contracts import tool_specs


def bottle():
    rows = []
    for z in np.linspace(0, .30, 61):
        r = .035 if z < .21 else .015
        for a in np.linspace(0, 2*np.pi, 32, endpoint=False):
            rows.append([r*np.cos(a), r*np.sin(a), z])
    return np.array(rows)


def test_neck_grasp_when_body_wider_than_gripper():
    cs = bottle_grasps(bottle(), np.array([.4, 0, .3]), [0, 0, .02], .045)
    assert cs
    assert all(c.width < .039 and c.contact[2] > .21 for c in cs)
    for c in cs:
        assert np.linalg.det(c.rotation) == pytest.approx(1)
        assert np.allclose(c.eef+c.rotation@np.array([0, 0, .02]), c.contact)
        assert np.dot(c.eef-c.pregrasp, c.rotation[:, 2]) == pytest.approx(.10)


def test_candidate_frames_follow_translated_scene():
    points = bottle()
    hand, offset = np.array([.4, 0, .3]), np.array([0, 0, .02])
    delta = np.array([5, -2, 1])
    a = bottle_grasps(points, hand, offset, .09)
    b = bottle_grasps(points+delta, hand+delta, offset, .09)
    assert len(a) == len(b)
    assert np.allclose(a[0].eef+delta, b[0].eef)


def test_bad_geometry_and_timing_fail_before_motion():
    with pytest.raises(ValueError):
        bottle_grasps([[float('nan'), 0, 0]]*8, [0, 0, 0], [0, 0, 0], .08)
    with pytest.raises(ValueError):
        resample_positions([[0], [1]], 0, .02)


def test_sparse_mesh_sections_intersect_edges_instead_of_requiring_midheight_vertices():
    from scipy.spatial import ConvexHull
    points = np.array([[x, y, z] for x in (-.02, .02) for y in (-.02, .02) for z in (0, .3)])
    candidates = bottle_grasps(points, np.array([.4, 0, .2]), [0, 0, 0], .09,
                                faces=ConvexHull(points).simplices)
    assert candidates
    assert all(.10 < c.contact[2] < .27 for c in candidates)


def test_trajectory_uses_elapsed_time_and_keeps_endpoint():
    q = resample_positions([[0, 1], [.5, .5], [1, 0]], .01, .01, slowdown=2)
    assert np.allclose(q, [[0, 1], [.25, .75], [.5, .5], [.75, .25], [1, 0]])


def test_geometric_path_timing_respects_velocity_acceleration_and_jerk():
    from manipulation_agent.grasp_geometry import time_joint_curve
    points = np.array([[0., 0.], [.1, -.3], [.3, -.5]])
    limits = (np.array([.15, .2]), np.array([.3, .4]), np.array([.5, .6]))
    q, timing = time_joint_curve(points, *limits, 1/30)
    assert np.allclose(q[[0, -1]], points[[0, -1]])
    for order, limit in enumerate(limits, 1):
        assert np.all(np.abs(np.diff(q, n=order, axis=0))/(1/30)**order <= limit)
    assert timing['duration_s'] == pytest.approx((len(q)-1)/30)


def test_departure_allows_only_decreasing_initial_carried_support_contact():
    from manipulation_agent.grasp_geometry import separating_contact_path
    depths = np.array([[0., .0004, 0.], [0., .0002, 0.], [0., 0., 0.]])
    p = np.array([[0., 0., 0.], [0., 0., .0002], [0., 0., .004]])
    rotations = np.repeat(np.eye(3)[None], 3, axis=0)
    check = lambda d, positions=p: separating_contact_path(
        d, [False, True, True], positions, rotations, [0., 0., 1.])['valid']
    assert check(depths)
    for column in (0, 2):
        changed = depths.copy(); changed[1, column] = .0001
        assert not check(changed)  # No new robot or object collision.
    changed = depths.copy(); changed[1, 1] = .0006
    assert not check(changed)
    changed = depths.copy(); changed[-1, 1] = .00001
    assert not check(changed)
    sliding = p.copy(); sliding[1, 0] = .002
    assert not check(depths, sliding)


def test_body_path_checks_intermediate_posture_and_final_restoration():
    from scipy.spatial.transform import Rotation
    p = np.array([[0, 0, 1.14], [.3, 0, .94], [0, 0, 1.14]])
    r = Rotation.from_euler('z', [0, 1, 0]).as_matrix()
    assert body_path_envelope(p, r, 1.14, restore=True)['valid']
    tilted = r.copy(); tilted[1] = Rotation.from_euler('y', 15, degrees=True).as_matrix()
    assert not body_path_envelope(p, tilted, 1.14, restore=True)['valid']
    low = p.copy(); low[1, 2] = .5
    assert not body_path_envelope(low, r, 1.14, restore=True)['valid']
    crouched = p.copy(); crouched[-1, 2] = .94
    assert body_path_envelope(crouched, r, 1.14)['valid']
    assert not body_path_envelope(crouched, r, 1.14, restore=True)['valid']


def stream_fixture(tmp_path, engine=None):
    from types import SimpleNamespace
    from manipulation_agent.executors.oracle_pick import GeometryPick
    torch = pytest.importorskip('torch')
    state = torch.zeros(18)
    commands = []
    root = SimpleNamespace(get_position_orientation=lambda: (torch.zeros(3), torch.tensor([0., 0., 0., 1.])))
    body = SimpleNamespace(get_position_orientation=lambda: (torch.tensor([0., 0., 1.14]), torch.tensor([0., 0., 0., 1.])))
    robot = SimpleNamespace(trunk_control_idx=torch.arange(4), arm_control_idx={'right': torch.arange(4, 11)},
                            get_joint_positions=lambda: state.clone(), root_link=root, links={'torso_link4': body})
    b = SimpleNamespace(output=tmp_path, robot=robot, torch=torch, steps=0, _command_q=state.clone())
    b._hold_action = lambda: b._command_q.clone()
    def step(action):
        commands.append(action.clone())
        state.copy_(action)
        b.steps += 1
    b._step = step
    picker = (engine or GeometryPick)(b)
    picker.arm = 'right'
    picker.home_body = (np.array([0., 0., 1.14]), np.array([0., 0., 0., 1.]))
    return picker, b, commands


def test_streams_torso_and_arm_each_tick_without_per_point_waits(tmp_path):
    picker, b, commands = stream_fixture(tmp_path)
    q = b.torch.arange(5).unsqueeze(1).repeat(1, 18)*.01
    q[:, 11:] = .2  # An unselected arm must remain at its measured lock state.
    picker.stream(q)
    assert len(commands) == len(q)-1 == b.steps
    assert b.torch.allclose(b.torch.stack(commands)[:, :11], q[1:, :11])
    assert b.torch.count_nonzero(b.torch.stack(commands)[:, 11:]) == 0


def test_tracking_error_stops_before_sending_a_large_joint_jump(tmp_path):
    from manipulation_agent.contracts import SkillError
    picker, b, commands = stream_fixture(tmp_path)
    q = b.torch.zeros((2, 18)); q[1, 0] = .5
    with pytest.raises(SkillError, match='tracking error'):
        picker.stream(q)
    assert commands == [] and b.steps == 0


def test_published_engine_streams_and_checks_measured_posture(tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys
    from types import SimpleNamespace
    from manipulation_agent.contracts import SkillError
    from manipulation_agent.pick_engine import prepare

    project = Path(__file__).resolve().parents[1]
    manifest = tmp_path / 'active.json'
    subprocess.run([sys.executable, str(project / 'scripts/publish_pick_engine.py'),
                    '--source', str(project), '--output', str(tmp_path / 'engines'),
                    '--activate', str(manifest)], check=True, capture_output=True)
    host = SimpleNamespace(output=tmp_path, steps=0, robot=object(), env=object())
    engine, version = prepare(host, manifest)
    assert version == json.loads(manifest.read_text())['version']
    picker, backend, commands = stream_fixture(tmp_path, engine)
    q = backend.torch.arange(4).unsqueeze(1).repeat(1, 18)*.01
    picker.stream(q)
    assert backend.steps == len(commands) == 3
    # The same published path must retain the actual posture guard.
    body = backend.robot.links['torso_link4']
    body.get_position_orientation = lambda: (
        backend.torch.tensor([0., 0., 1.14]),
        backend.torch.tensor([0., .2, 0., .98]))
    with pytest.raises(SkillError, match='execution envelope'):
        picker.stream(q[-1:].repeat(2, 1))
    assert backend.steps == 4


def test_intent_protocol_removes_model_waypoints_and_recipes(monkeypatch):
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'geometry_pick_v1')
    rows = {r['name']: r for r in tool_specs()}
    assert not {'move_joints', 'grasp', 'execute_waypoints', 'list_manip_recipes'} & rows.keys()
    assert rows['manip']['inputSchema']['required'] == ['operation', 'target', 'arm', 'lift_m', 'revision']
    monkeypatch.delenv('MAS_MANIP_ENGINE')
    assert any(r['name'] == 'move_joints' for r in tool_specs())


def test_attachment_frame_matches_base_relative_world_meshes():
    from scipy.spatial.transform import Rotation
    from manipulation_agent.grasp_geometry import attachment_pose_in_base
    base_p = np.array([7.4, .05, .005])
    base_r = Rotation.from_euler('z', 2.1)
    local_p = np.array([-.9, -.3, 1.0])
    local_r = Rotation.from_euler('xyz', [.1, .2, .3])
    eef_p = base_p + base_r.apply(local_p)
    eef_r = base_r * local_r
    p, q = attachment_pose_in_base(eef_p, eef_r.as_quat(), base_p, base_r.as_quat())
    assert np.allclose(p, local_p)
    assert np.allclose(Rotation.from_quat(q).as_matrix(), local_r.as_matrix())
    # Both mesh and EEF must transform together; otherwise the carried geometry
    # is displaced by metres from its actual gripper.
    contact_eef = np.array([.01, .02, -.04])
    mesh_point_base = base_r.inv().apply(eef_p + eef_r.apply(contact_eef) - base_p)
    assert np.allclose(Rotation.from_quat(q).inv().apply(mesh_point_base-p), contact_eef)


def pick_reference_events():
    body = {'position_m': [7., 0., 1.14], 'quaternion_xyzw': [0., 0., 0., 1.]}
    observation = {'revision': 2, 'oracle_3d': {
        'robot': {'arms': {'left': {'held_object': None}, 'right': {'held_object': None}},
                  'base_pose_world': {'position_m': [7., 0., 0.], 'quaternion_xyzw': [0., 0., 0., 1.]},
                  'trunk': {'body_posture': {'upper_body_pose_world': body}}},
        'objects': [{'id': 'bottle', 'pose': {'position_m': [1., .2, .98]}}]}}
    return [{'id': 'source-observation', 'kind': 'tool_result', 'result': {'observation': observation}},
            {'kind': 'tool_call', 'name': 'manip', 'request_id': 'retaining-pick'}]


def test_recorded_pick_reference_uses_state_before_retention_source():
    import copy
    from manipulation_agent.grasp_geometry import recorded_pick_reference
    events = pick_reference_events()
    current = copy.deepcopy(events[0])
    current['result']['observation']['oracle_3d']['robot']['trunk']['body_posture'][
        'upper_body_pose_world']['position_m'][2] = .95
    events.append(current)
    ref = recorded_pick_reference(events, 'retaining-pick', 'bottle')
    assert ref['body_position_world_m'][2] == 1.14
    assert ref['target_position_world_m'] == [8., .2, .98]
    with pytest.raises(ValueError, match='request is missing'):
        recorded_pick_reference(events, 'missing', 'bottle')
    with pytest.raises(ValueError, match='reference target'):
        recorded_pick_reference(events, 'retaining-pick', 'another-object')


def test_retained_pick_resumes_without_reopening_or_replaying_motion(tmp_path):
    import json
    from types import SimpleNamespace
    from manipulation_agent.executors.oracle_pick import GeometryPick
    from manipulation_agent.contracts import SkillError
    torch = pytest.importorskip('torch')
    obj = SimpleNamespace(name='bottle', fixed_base=False, root_link=object(),
        get_position_orientation=lambda: (torch.tensor([8., .2, .98]), torch.tensor([0., 0., 0., 1.])))
    link = SimpleNamespace(get_position_orientation=lambda: (
        torch.tensor([7., 0., .95]), torch.tensor([0., 0., 0., 1.])))
    robot = SimpleNamespace(arm_names=['left', 'right'], links={'torso_link4': link},
                             eef_links={'left': link, 'right': link})
    held = {'left': None, 'right': 'bottle'}
    backend = SimpleNamespace(output=tmp_path, torch=torch, robot=robot, steps=1317,
        env=SimpleNamespace(task=SimpleNamespace(object_scope={'target': obj})),
        _held=lambda arm: held[arm], _primitive_context=None, _phase=lambda phase: None,
        _retention=lambda *args: {'retained': True},
        _set_gripper=lambda *args: pytest.fail('Must preserve existing grip'),
        _step=lambda *args: pytest.fail('No replay before resumed lift'))
    picker = GeometryPick(backend)
    picker.get_planner = lambda arm: None
    picker.lift_and_hold = lambda lift, before, rejected: (picker.home_body[0].copy(), before.clone())
    (tmp_path / 'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in pick_reference_events()))
    picker.log.write_text(json.dumps({'kind': 'retention', 'retained': True,
        'request_id': 'retaining-pick', 'native_grasp': {'object_id': 'bottle'}})+'\n')
    # This unit isolates retained-pick selection; transaction behavior has its
    # own native-state fixture in test_oracle_open_loop.py.
    home, before = picker._execute_pick('pick', 'target')
    assert home[2] == 1.14 and float(before[2]) == pytest.approx(.98)
    assert backend.steps == 1317 and held['right'] == 'bottle'
    assert backend._primitive_context is None
    with pytest.raises(SkillError, match='differs from the actual retained arm'):
        picker._execute_pick('pick', 'target', arm='left')
    held['right'] = 'other-object'
    with pytest.raises(SkillError, match='Only the retained pick target'):
        picker._execute_pick('pick', 'target')


def test_elevated_clearance_and_restoration_are_both_planned_before_motion():
    from manipulation_agent.executors.oracle_pick import GeometryPick
    picker = GeometryPick.__new__(GeometryPick)
    picker.home_body = (np.array([0., 0., 1.14]), np.array([0., 0., 0., 1.]))
    picker.set_phase = lambda phase: None
    picker.event = lambda *args, **kwargs: None
    picker.body_clearance = lambda *args, **kwargs: False
    picker.stream = lambda *args, **kwargs: pytest.fail('Planning must not execute partial paths')
    goal, rotation = np.array([.8, 0., 1.2]), np.eye(3)
    clearance, recovery = np.zeros((3, 11)), np.ones((4, 11))
    calls = []
    def plan(position, orientation, **options):
        calls.append(options)
        return clearance if len(calls) == 1 else recovery
    picker.plan = plan
    paths = picker.plan_lift_paths(goal, rotation)
    assert [name for name, path in paths] == ['clearance', 'recovery']
    assert calls[0] == {'attached': True}
    assert calls[1]['restore_body'] and calls[1]['attached']
    assert np.array_equal(calls[1]['initial'], clearance[-1])
    picker.plan = lambda *args, **kwargs: None
    picker.plan_support_departure = lambda *args, **kwargs: None
    assert picker.plan_lift_paths(goal, rotation) == []


def test_lift_endpoint_that_passes_original_posture_guard_needs_no_recovery():
    from manipulation_agent.executors.oracle_pick import GeometryPick
    picker = GeometryPick.__new__(GeometryPick)
    picker.set_phase = lambda phase: None
    picker.event = lambda *args, **kwargs: None
    path = np.zeros((3, 11))
    calls = []
    picker.plan = lambda *args, **kwargs: (calls.append(kwargs) or path)
    def envelope(q, *, restore):
        assert q is path and restore is True
        return True
    picker.body_clearance = envelope
    result = picker.plan_lift_paths(np.ones(3), np.eye(3))
    assert len(result) == len(calls) == 1
    assert result[0][0] == 'clearance' and result[0][1] is path


def test_retained_geometry_is_identical_for_planning_and_recheck(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from manipulation_agent.executors.oracle_pick import GeometryPick
    torch = pytest.importorskip('torch')
    class RigidLink:
        collision_meshes = {'mesh': SimpleNamespace(prim_path='/bottle/mesh')}
    class Pose:
        def __init__(self, **values): self.__dict__.update(values)
    math_module, rigid_module = ModuleType('curobo.types.math'), ModuleType('omnigibson.prims.rigid_dynamic_prim')
    math_module.Pose, rigid_module.RigidDynamicPrim = Pose, RigidLink
    monkeypatch.setitem(sys.modules, 'curobo.types.math', math_module)
    monkeypatch.setitem(sys.modules, 'omnigibson.prims.rigid_dynamic_prim', rigid_module)
    fitted = torch.tensor([[0., 0., 0., .02], [0., 0., .04, .02]])
    fit_calls, reuse_calls, disabled = [], [], []
    mg = SimpleNamespace(attach_objects_to_robot=lambda **kw: fit_calls.append(kw),
        attach_spheres_to_robot=lambda **kw: reuse_calls.append(kw),
        world_coll_checker=SimpleNamespace(enable_obstacle=lambda **kw: disabled.append(kw)),
        kinematics=SimpleNamespace(kinematics_config=SimpleNamespace(get_link_spheres=lambda name: fitted)))
    pose = lambda: (torch.zeros(3), torch.tensor([0., 0., 0., 1.]))
    generator = SimpleNamespace(_pick_attachment_cache={}, mg={'default': mg},
        tensor_args=SimpleNamespace(to_device=torch.as_tensor),
        robot=SimpleNamespace(root_link=SimpleNamespace(get_position_orientation=pose),
            links={'eef': SimpleNamespace(get_position_orientation=pose)},
            curobo_attached_object_link_names={'eef': 'carried'}))
    target = {'eef': RigidLink()}
    for _ in range(2):
        GeometryPick.attach_objects_in_base(generator, target, {'eef': 1.0}, object(), 'default')
    assert len(fit_calls) == len(reuse_calls) == 1
    assert fit_calls[0]['scale'] == 1.0
    assert torch.equal(reuse_calls[0]['sphere_tensor'], fitted)
    fitted.fill_(42.)  # The frozen geometry must not alias the fitter's buffer.
    assert not torch.equal(reuse_calls[0]['sphere_tensor'], fitted)
    assert disabled == [{'enable': False, 'name': '/bottle/mesh'}]
