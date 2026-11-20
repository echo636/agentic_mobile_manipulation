"""Timing feasibility, rigid placement geometry and checked navigation contracts."""
from copy import deepcopy
from types import SimpleNamespace
import math
import numpy as np
import pytest

from manipulation_agent.trajectory_timing import estimated_path, compact_path
from manipulation_agent.manip_programs import checked_phases, checked_invocation, checked_output
from manipulation_agent.geometry_programs.place_in_open_bin import generate, rotation


def test_estimate_extends_past100_and_preserves_nonmonotonic_path():
    raw = np.array([[0., 0.], [.2, .1], [-.1, .15], [.8, 0.]])
    q, info = estimated_path(raw, 1/30, [.15, .15], [1., 1.], [30., 30.], control_steps=25)
    assert info['control_steps'] > 100 and info['requested_control_steps'] == 25
    assert info['estimate_extended'] and info['within_joint_limits']
    assert np.allclose(q[[0,-1]], raw[[0,-1]]) and q[:,0].min() < -.09
    assert max(info['limit_ratios'].values()) <= 1+1e-6
    assert compact_path(q, 1/30, [.15,.15], [1.,1.], [30.,30.], max_steps=len(q)-1)[0] is not None


def test_long_estimate_and_missing_estimate_have_no_hidden100_cap():
    for estimate in [None, 220]:
        q, info = estimated_path(np.linspace(0,.1,161)[:,None], 1/30, [1.], [2.], [50.], control_steps=estimate)
        assert len(q) >= (161 if estimate is None else 221)
        assert info['max_control_steps'] is None
    q, _ = estimated_path([[.1]], 1/30, [1.], [2.], [50.], control_steps=3)
    assert q.shape == (4,1) and np.all(q == np.float32(.1))


@pytest.mark.parametrize('estimate', [0, -1, True, 1.5])
def test_invalid_estimates_refused_before_motion(estimate):
    with pytest.raises(ValueError): estimated_path([[0.],[.1]], 1/30, [1.], [2.], [50.], control_steps=estimate)


def mesh_fixture():
    # Hollow square bin: both inner/outer walls and annular top, no roof.
    v = np.array([[x,y,z] for z in [.1,.6] for x,y in [(-.3,-.3),(.3,-.3),(.3,.3),(-.3,.3),
                                                                    (-.25,-.25),(.25,-.25),(.25,.25),(-.25,.25)]])
    faces=[]
    for start in [0,4]:
        for k in range(4):
            a,b=start+k,start+(k+1)%4
            faces.extend([[a,b,b+8],[a,b+8,a+8]])
    for k in range(4):
        a,b=8+k,8+(k+1)%4;c,d=12+k,12+(k+1)%4
        faces.extend([[a,b,d],[a,d,c]])
    pts=np.array([[x,y,z] for x in [.37,.43] for y in [-.03,.03] for z in [.5,.8]])
    mesh=lambda a,f:dict(frame='robot_base', vertices_m=a.tolist(),faces=f)
    obj=dict(id='bottle',pose=dict(position_m=[.4,0,.65],quaternion_xyzw=[0,0,0,1]),collision_mesh=mesh(pts,[]))
    bucket=dict(id='bin',pose=dict(position_m=[0,0,.1],quaternion_xyzw=[0,0,0,1]),collision_mesh=mesh(v,faces))
    hand=dict(held_object='bottle',eef_pose=dict(position_m=[.35,-.05,.68],quaternion_xyzw=[0,0,0,1]))
    return dict(frame='robot_base',objects=[obj,bucket],robot=dict(arms=dict(left=hand)))


def test_direct_placement_rigid_transform_full_fit_and_explicit_release():
    oracle=mesh_fixture();args=dict(arm='left',object_id='bottle',container_id='bin',control_steps=145)
    result=checked_output(generate('',oracle,args))
    for c in result['candidates']:
        assert len(c['phases'])==1
        p=c['phases'][0];assert p['control_steps']==145 and p['gripper']=='hold'
        r=rotation(p['goal']['quaternion_xyzw'])
        hp=np.array(oracle['robot']['arms']['left']['eef_pose']['position_m'])
        vertices=np.array(oracle['objects'][0]['collision_mesh']['vertices_m'])
        moved=(vertices-hp)@r.T+np.array(p['goal']['position_m'])
        assert moved[:,2].min()==pytest.approx(.645)
        assert np.linalg.norm(moved[:,:2],axis=1).max() < .25-.035
    result=checked_output(generate('',oracle,{**args,'release':True}))
    assert result['candidates'][0]['phases'][0]['gripper']=='open'
    pose={k:v for k,v in result['candidates'][0]['phases'][0].items() if k!='name'}
    assert checked_invocation(dict(mode='pose',revision=0,**pose))['control_steps']==145


@pytest.mark.parametrize('failure',['occupied','closed','too_large','stale'])
def test_placement_bad_preconditions(failure):
    o=mesh_fixture()
    if failure=='occupied':o['robot']['arms']['left']['held_object']=None
    elif failure=='closed':o['objects'][1]['collision_mesh']['faces'] += [[12,13,14],[12,14,15]]
    elif failure=='too_large':
        for p in o['objects'][0]['collision_mesh']['vertices_m']:p[1]*=20
    else:o['objects'][0]['collision_mesh']['frame']='world'
    with pytest.raises(ValueError):generate('',o,dict(arm='left',object_id='bottle',container_id='bin'))


def test_smooth_follower_accelerates_brakes_and_reaches_goal():
    from manipulation_agent.executors.gt_navigation import GridMap,NavigationPlan,SmoothGridFollower
    grid=GridMap(60,60,.1,(-3.,-3.),bytes([1])*3600)
    plan=NavigationPlan(points=((0.,0.),(1.,0.)),goal=(1.,0.),target=(1.,0.),final_yaw=0.,travel_yaw=0.,
                        geodesic_m=1.,score=0.,candidates_considered=1,candidates_reachable=1,start_grid_offset_m=0.,expanded_cells=0,motion_mode='holonomic')
    calls=[];f=SmoothGridFollower(grid,plan,1/30,speed=.25,motion_check=lambda a,b:calls.append((a,b)) or True)
    actual=(0.,0.,0.);speeds=[]
    for _ in range(600):
        q=f.next_pose(actual)
        if q is None:break
        speeds.append(math.dist(actual[:2],q[:2])*30);actual=q
    else:pytest.fail('follower did not converge')
    assert math.dist(actual[:2],plan.goal)<=f.position_tolerance
    assert max(speeds)<=.25+1e-9 and speeds[0]<.02 and speeds[-1]<.02
    assert np.max(np.abs(np.diff([0.,*speeds,0.]))) <= .35/30+.002
    assert len(calls)==len(speeds)


@pytest.mark.parametrize('yaw_sign', [-1, 1])
def test_smooth_native_heading_feedback_crosses_braking_deadband(yaw_sign):
    """Reproduce native309_19: travel error .00890858rad, terminal tolerance .01.

    Float32 feedback cannot represent the old 1e-8rad command; the robot stopped
    translating 17.5cm short while demanding that ineffective turn indefinitely.
    """
    from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan, SmoothGridFollower
    grid = GridMap(60, 60, .1, (-3., -3.), bytes([1])*3600)
    heading = yaw_sign*2.0844508887464688
    plan = NavigationPlan(points=((0., 0.), (1., 0.)), goal=(1., 0.), target=(1., 0.),
        final_yaw=heading, travel_yaw=heading, geodesic_m=1., score=0.,
        candidates_considered=1, candidates_reachable=1, start_grid_offset_m=0.,
        expanded_cells=0, motion_mode='holonomic')
    f = SmoothGridFollower(grid, plan, 1/30, speed=.25, turn_speed=.4, stall_steps=60)
    f.position_tolerance = .005
    f.final_yaw_tolerance = .01
    actual = (0., 0., float(np.float32(heading+yaw_sign*.00890858)))
    assert f.heading_tolerance < abs(f.angle(heading-actual[2])) < .9*f.final_yaw_tolerance
    for _ in range(600):
        command = f.next_pose(actual)
        if command is None:
            break
        actual = tuple(float(np.float32(v)) for v in command)
    else:
        pytest.fail('native-style feedback did not reach the goal')
    assert math.dist(actual[:2], plan.goal) <= f.position_tolerance
    assert abs(f.angle(actual[2]-heading)) <= f.final_yaw_tolerance


@pytest.mark.parametrize('final_yaw', [.3, .8, -.3])
def test_smooth_native_feedback_overcomes_low_speed_nonresponse(final_yaw):
    """A conservative nonlinear motor fixture for the measured 1.5cm freeze.

    This checks feedback compensation, not the unmeasured physical cause of the
    native low-speed response. A blocked robot must still fail its stall check.
    """
    from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan, SmoothGridFollower, NavigationError
    grid = GridMap(60, 60, .1, (-3., -3.), bytes([1])*3600)
    plan = NavigationPlan(points=((0., 0.), (1., .5)), goal=(1., .5), target=(1., .5),
        final_yaw=final_yaw, travel_yaw=.3, geodesic_m=math.hypot(1., .5), score=0.,
        candidates_considered=1, candidates_reachable=1, start_grid_offset_m=0.,
        expanded_cells=0, motion_mode='holonomic')
    for blocked in [False, True]:
        f = SmoothGridFollower(grid, plan, 1/30, speed=.25, turn_speed=.4,
                               stall_steps=60, tracking_gain=3.)
        f.position_tolerance = .005
        f.final_yaw_tolerance = .01
        actual = (0., 0., .3)
        try:
            for _ in range(700):
                command = f.next_pose(actual)
                if command is None:
                    break
                velocity = (np.array(command)-actual)*30
                assert np.linalg.norm(velocity[:2]) <= .25+1e-9
                assert abs(velocity[2]) <= .4+1e-9
                if np.linalg.norm(velocity[:2]) < .08 or blocked:
                    velocity[:2] = 0.
                if abs(velocity[2]) < .03 or blocked:
                    velocity[2] = 0.
                actual = tuple(np.array(actual)+velocity/30)
            else:
                pytest.fail('feedback controller did not converge')
        except NavigationError as exc:
            assert blocked and exc.code == 'navigation_stalled'
        else:
            assert not blocked
            assert math.dist(actual[:2], plan.goal) <= f.position_tolerance
            assert abs(f.angle(actual[2]-plan.final_yaw)) <= f.final_yaw_tolerance


@pytest.mark.parametrize('goal', [(1.,.1),(1.,-.1),(.1,1.),(.05,1.)])
@pytest.mark.parametrize('forward_only', [False,True])
@pytest.mark.parametrize('response_scale',[1.,.8,.95])
def test_smooth_native_axis_nonresponse_keeps_checked_diagonal(goal,forward_only,response_scale):
    """Observed missing world-Y motion despite a nonzero world-Y command.

    The per-axis deadzone is a conservative fixture, not a PhysX diagnosis.
    Correction must preserve the checked corridor and velocity limit.
    """
    from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan, SmoothGridFollower
    grid=GridMap(60,60,.1,(-3.,-3.),bytes([1])*3600)
    heading=math.atan2(goal[1],goal[0]);length=math.hypot(*goal)
    plan=NavigationPlan(points=((0.,0.),goal),goal=goal,target=goal,final_yaw=heading,
        travel_yaw=heading,geodesic_m=length,score=0.,candidates_considered=1,
        candidates_reachable=1,start_grid_offset_m=0.,expanded_cells=0,motion_mode='holonomic')
    def safe(a,b):
        return abs(b[0]*goal[1]-b[1]*goal[0])/length <= .012
    follower=SmoothGridFollower(grid,plan,1/30,speed=.25,turn_speed=.4,
        tracking_gain=3.,motion_check=safe,stall_steps=60,forward_only=forward_only)
    follower.position_tolerance=.005;follower.final_yaw_tolerance=.01
    actual=(0.,0.,heading)
    for _ in range(800):
        command=follower.next_pose(actual)
        if command is None:break
        velocity=(np.array(command[:2])-actual[:2])*30
        assert np.linalg.norm(velocity) <= .25+1e-9
        velocity[np.abs(velocity)<.08]=0.
        actual=(*(float(np.float32(v)) for v in np.array(actual[:2])+response_scale*velocity/30),heading)
        assert safe(actual,actual)
    else:pytest.fail('Axis feedback did not converge')
    assert math.dist(actual[:2],goal)<=follower.position_tolerance


def test_smooth_navigation_prefers_checked_holonomic_direct(monkeypatch):
    from manipulation_agent.executors.gt_navigation import NavigationPlan
    from manipulation_agent.executors.forward_navigation import select_forward_route
    monkeypatch.setenv('MAS_NAV_SMOOTH','1')
    plan=NavigationPlan(points=((0.,0.),(0.,1.)),goal=(0.,1.),target=(0.,1.),final_yaw=0.,
                        geodesic_m=1.,score=0.,candidates_considered=1,candidates_reachable=1,start_grid_offset_m=0.,expanded_cells=0)
    checker=SimpleNamespace(motion_free=lambda a,b:True)
    selected,route,_=select_forward_route([plan],(0,0,0),checker,allow_lateral=True)
    assert selected==plan and len(route.phases)==1 and route.turn_radians==0
    assert route.phases[0].motion_mode=='holonomic'
