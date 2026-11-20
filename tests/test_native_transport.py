"""CPU integration checks; these are not native simulator acceptance."""
import math
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.native_transport import local_velocity, placement_state


@pytest.mark.parametrize('native', [True, False])
def test_oracle_step_preserves_native_action_and_legacy_hold(monkeypatch, native):
    from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend
    from manipulation_agent.omnigibson_backend import OmniGibsonBackend
    b = OracleWaypointBackend.__new__(OracleWaypointBackend)
    b._navigation_active = True
    b._native_transport_active = native
    b._base_target = None
    b._motor_ready = False
    b.deadline = SimpleNamespace(check=Mock())
    hold = [0., 0., 0., .2, -1., 1.]
    command = [.2, -.1, .3, .2, -1., 1.]
    b._hold_action = lambda: hold
    b._video_frame = Mock()
    physics_step = Mock()
    monkeypatch.setattr(OmniGibsonBackend, '_step', physics_step)
    b._step(command)
    physics_step.assert_called_once_with(b, command if native else hold)


def test_rotated_base_command_is_local_and_wraps_yaw():
    dt = 1/30
    vx,vy,w = local_velocity((0.,0.,math.pi/2),(.005,0.,math.pi/2),dt)
    assert vx == pytest.approx(0., abs=1e-12)
    assert vy == pytest.approx(-.15)
    assert w == 0
    assert local_velocity((0,0,math.pi-.001),(0,0,-math.pi+.001),dt)[2] == pytest.approx(.06)
    vx,vy,w = local_velocity((0,0,0),(2,2,4),dt)
    assert math.hypot(vx,vy) == pytest.approx(.25)
    assert abs(w) <= .4
    with pytest.raises(ValueError): local_velocity((0,0,0),(0,0,0),0)


@pytest.mark.parametrize('disturbance', [False, True])
@pytest.mark.parametrize('smooth', [False, True])
@pytest.mark.parametrize('overlay', [False, True])
def test_actual_follower_uses_native_actions_with_motor_lag_and_no_setters(tmp_path, monkeypatch, disturbance, smooth, overlay):
    import hashlib
    import json
    from pathlib import Path
    from manipulation_agent.executors import gt_navigation
    monkeypatch.delenv('MAS_NAV_ENGINE_MANIFEST', raising=False)
    if overlay:
        payload = Path(gt_navigation.__file__).read_bytes()
        (tmp_path/'gt_navigation.py').write_bytes(payload)
        manifest = tmp_path/'navigation.json'
        manifest.write_text(json.dumps(dict(format_version=1, root=str(tmp_path),
            source_sha256=hashlib.sha256(payload).hexdigest())))
        monkeypatch.setenv('MAS_NAV_ENGINE_MANIFEST', str(manifest))
    torch = pytest.importorskip('torch')
    from manipulation_agent.deadline import EpisodeDeadline
    from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend
    from manipulation_agent.omnigibson_backend import OmniGibsonBackend
    from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan
    from manipulation_agent.executors.forward_navigation import ForwardRoute
    modules={name:ModuleType(name) for name in ('omnigibson','omnigibson.utils','omnigibson.utils.transform_utils')}
    t=modules['omnigibson.utils.transform_utils']
    def quat2mat(q):
        yaw=2*math.atan2(float(q[2]),float(q[3]));c,s=math.cos(yaw),math.sin(yaw)
        return torch.tensor([[c,-s,0],[s,c,0],[0,0,1]])
    t.quat2mat=quat2mat
    t.euler2quat=lambda r:torch.tensor([0.,0.,math.sin(float(r[2])/2),math.cos(float(r[2])/2)])
    modules['omnigibson'].utils=modules['omnigibson.utils'];modules['omnigibson.utils'].transform_utils=t
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    b=OracleWaypointBackend.__new__(OracleWaypointBackend)
    b.output=tmp_path;b.steps=0;b.deadline=EpisodeDeadline();b.torch=torch
    b._native_transport_active=True;b._navigation_active=True;b._base_target=None
    b._motor_ready=False;b._video_frame=Mock()
    b.og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda:1/30))
    b._navigation_layers=[];b._navigation_support=[];b._navigation_layer_yaw=math.pi/2
    position=torch.tensor([0.,0.,0.]);yaw=math.pi/2;actions=[]
    def pose():return position.clone(),torch.tensor([0.,0.,math.sin(yaw/2),math.cos(yaw/2)])
    b.robot=SimpleNamespace(get_position_orientation=pose,get_joint_positions=lambda:torch.zeros(6),
        base_idx=torch.tensor([0,1,2]),base_action_idx=torch.tensor([0,1,2]),is_holonomic_base=True,
        is_asleep=True)
    def wake():b.robot.is_asleep=False
    b.robot.wake=Mock(side_effect=wake)
    if smooth:monkeypatch.setenv('MAS_NAV_SMOOTH','1')
    else:monkeypatch.delenv('MAS_NAV_SMOOTH',raising=False)
    b._get_held=lambda:None
    b._restore_base_target=Mock(side_effect=AssertionError('projection forbidden'))
    b._hold_action=lambda:torch.tensor([0.,0.,0.,.2,-1.,1.])
    def step(backend,action):
        nonlocal yaw
        assert not b.robot.is_asleep, 'native velocity commands must wake a sleeping articulation'
        actions.append(action.clone())
        # Lagged native-motor fixture. Reaching a target requires actual feedback.
        vx,vy,w=action[:3].tolist();c,s=math.cos(yaw),math.sin(yaw)
        position[0]+=(c*vx-s*vy)*(.8/30)
        position[1]+=(s*vx+c*vy)*(.8/30)
        yaw+=w*.8/30;b.steps+=1
        if disturbance:position[1]=.2
    monkeypatch.setattr(OmniGibsonBackend,'_step',step)
    grid=GridMap(40,40,.1,(-2.,-2.),bytes([1])*1600)
    plan=NavigationPlan(points=((0.,0.),(.3,0.)),goal=(.3,0.),target=(.3,0.),
        geodesic_m=.3,score=0.,candidates_considered=1,candidates_reachable=1,
        start_grid_offset_m=0.,expanded_cells=0,
        travel_yaw=math.pi/2,final_yaw=math.pi/2,motion_mode='holonomic')
    collision=SimpleNamespace(grid=grid,motion_free=Mock(side_effect=lambda a,b:abs(b[1])<.02))
    monkeypatch.setattr('manipulation_agent.executors.heading_collision.HeadingCollisionChecker',Mock(return_value=collision))
    route=ForwardRoute(start=(0.,0.,math.pi/2),phases=(plan,),method='cpu_fixture',distance_m=.3,expanded_states=0)
    if disturbance:
        with pytest.raises(SkillError) as error:b._execute_gt_plan(grid,plan,200,prepared_route=route)
        assert error.value.code=='native_transport_swept_collision'
        assert b.steps==1 and b._base_target is None
        b.robot.wake.assert_called_once()
        b._restore_base_target.assert_not_called()
        return
    result=b._execute_gt_plan(grid,plan,200,prepared_route=route)
    assert result['motor']=='native_holonomic_joint_velocity'
    versions = [json.loads(line) for line in (tmp_path/'navigation_engine_versions.jsonl').read_text().splitlines()]
    assert len(versions) == 1 and versions[0]['version'] == result['navigation_engine_version']
    assert versions[0]['manifest'] == (str(manifest) if overlay else None)
    assert result['final_position_error_m'] <= (.005 if smooth else .015)
    assert .28 <= float(position[0]) <= .31
    assert len(actions)>40
    assert all(torch.equal(a[3:],torch.tensor([.2,-1.,1.])) for a in actions)
    assert all(float(torch.linalg.norm(a[:2]))<=.250001 for a in actions)
    assert all(torch.equal(a[:3],torch.zeros(3)) for a in actions[-10:])
    b._restore_base_target.assert_not_called()
    b.robot.wake.assert_called_once()
    assert b._base_target is None


@pytest.mark.parametrize('held,inside,released', [(True,True,False),(False,False,True),(False,True,True)])
def test_native_inside_and_release_are_independent(monkeypatch,held,inside,released):
    class Inside:pass
    state=SimpleNamespace(get_value=Mock(return_value=inside))
    module=ModuleType('omnigibson.object_states');module.Inside=Inside
    monkeypatch.setitem(sys.modules,'omnigibson.object_states',module)
    obj=SimpleNamespace(states={Inside:state},get_position_orientation=lambda:([0,0,0],[0,0,0,1]))
    container=SimpleNamespace(get_position_orientation=lambda:([0,0,0],[0,0,0,1]))
    b=SimpleNamespace(robot=SimpleNamespace(arm_names=['left','right']),steps=123,
        _resolve_object=lambda name:obj if name=='bottle' else container,
        _grasp_state=lambda arm:{'object_id':'bottle' if arm=='right' and held else None},
        _pose=lambda pose:pose)
    result=placement_state(b,'bottle','bin')
    state.get_value.assert_called_once_with(container)
    assert result['relation_satisfied'] is inside
    assert result['released'] is released
    assert result['physics_steps']==0 and b.steps==123
