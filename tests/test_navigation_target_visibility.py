"""An explicit elevated click cannot accept a reachable goal behind a wall."""
import json
import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.gt_navigation import GridMap, NavigationError
from manipulation_agent.executors.forward_navigation import select_forward_route
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class Tensor:
    def __init__(self, value): self.value=np.asarray(value)
    def cpu(self): return self
    def numpy(self): return self.value
    def tolist(self): return self.value.tolist()
    def __getitem__(self, key):
        value=self.value[key]
        return value.item() if np.ndim(value)==0 else Tensor(value)


class TargetVisibilityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        b=self.backend=RGBBackend.__new__(RGBBackend)
        b.output=Path(temp.name);b.deadline=EpisodeDeadline();b.navigation_distance=0.
        b.yaw=0.
        b.position=Tensor([.5,1.,0.]);b.robot=SimpleNamespace(
            get_position_orientation=lambda:(b.position,None))
        b.env=SimpleNamespace(scene=SimpleNamespace(trav_map=SimpleNamespace(floor_heights=[0.])))
        # A wall with an opening: both sides are reachable, but only the target
        # side is visible. Grid reachability alone must not admit the wrong side.
        free=np.ones((41,41),dtype='uint8');free[:30,20]=0
        grid=GridMap(41,41,.1,(0.,0.),free.tobytes())
        b._navigation_clearance=(grid.free,np.zeros_like(free),.35)
        b._navigation_layers=[(free,np.zeros((4,2)))];b._navigation_support=np.ones_like(free)
        b._approach_visible=Mock(side_effect=lambda xy,*_,**kw:xy[0]>2.)
        b.moved=False
        def execute(grid,plan,limit,**kwargs):
            b.moved=True;b.position=Tensor([*plan.goal,0.]);b.yaw=plan.final_yaw
            return {'steps':3,'motion_steps':3,'actual_path_distance_m':plan.geodesic_m}
        b._execute_gt_plan=Mock(side_effect=execute)
        modules={name:ModuleType(name) for name in ['omnigibson','omnigibson.utils','omnigibson.utils.transform_utils']}
        modules['omnigibson'].utils=modules['omnigibson.utils']
        modules['omnigibson.utils'].transform_utils=modules['omnigibson.utils.transform_utils']
        modules['omnigibson.utils.transform_utils'].quat2mat=lambda _:np.array([
            [math.cos(b.yaw),-math.sin(b.yaw),0.],[math.sin(b.yaw),math.cos(b.yaw),0.],[0.,0.,1.]])
        p=patch.dict(sys.modules,modules);p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.heading_collision.HeadingCollisionChecker',
                return_value=SimpleNamespace(motion_free=lambda a,b:True,
                                             grid_at_heading=lambda yaw:self.build_grid.return_value[0]))
        self.collision=p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.forward_navigation.select_forward_route',
                side_effect=lambda options,*a,**kw:(min(options,key=lambda p:p.score),None,{'fixture':True}))
        self.route_selection=p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.live_gt_map.build_navigation_grid',return_value=(grid,
            {'chassis_footprint_world_offsets':[],'collision_layers':[],'dynamic_collision_check':True}))
        self.build_grid=p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.live_gt_map.feasible_heading_grids',return_value=[])
        self.headings=p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.live_gt_map.rotation_staging_plan',side_effect=NavigationError('No staging'))
        p.start();self.addCleanup(p.stop)

    def target(self,z=1.):
        return SimpleNamespace(get_position_orientation=lambda:(Tensor([2.3,1.,z]),None))

    def open_side_target(self):
        """Exercise the real complete-route selector in an unobstructed scene."""
        grid=GridMap(41,41,.1,(0.,0.),bytes([1])*41**2)
        self.build_grid.return_value=(grid,self.build_grid.return_value[1])
        self.collision.return_value.grid=grid
        self.route_selection.side_effect=select_forward_route
        self.backend._approach_visible.side_effect=lambda *a,**kw:True
        return SimpleNamespace(get_position_orientation=lambda:(Tensor([.5,2.3,1.]),None))

    def test_elevated_approach_keeps_arrival_heading_without_return_turn(self):
        b=self.backend;b._navigate(self.open_side_target(),1000)
        self.assertAlmostEqual(b.yaw,math.pi/2)
        route=b._execute_gt_plan.call_args.kwargs['prepared_route']
        self.assertAlmostEqual(route.turn_radians,math.pi/2)
        self.assertGreater(route.phases[-1].geodesic_m,0.)
        self.assertTrue(any(abs(c.kwargs['base_yaw']-math.pi/2)<1e-8
                            for c in b._approach_visible.call_args_list))
        arrival=json.loads((b.output/'navigation_arrivals.jsonl').read_text())
        self.assertTrue(arrival['target_visible'])
        self.assertIsNotNone(arrival['local_exit_certificate'])

    def test_arrival_heading_that_loses_target_keeps_original_view(self):
        b=self.backend;target=self.open_side_target()
        b._approach_visible.side_effect=lambda *a,base_yaw,**kw:abs(base_yaw)<.01
        b._navigate(target,1000)
        self.assertAlmostEqual(b.yaw,0.)
        saved=json.loads((b.output/'navigation_plans.jsonl').read_text())
        self.assertEqual(saved['arrival_heading_refinement'][0]['reason'],'target_not_visible')

    def test_radio_camera_gap_uses_small_view_turn_instead_of_departure_yaw(self):
        from manipulation_agent.executors.gt_navigation import NavigationPlan
        from manipulation_agent.observations.rig import visible_rig_rays
        b=self.backend;self.open_side_target()
        # Recorded radio stance: the straight-arrival yaw has safe geometry
        # but the selected surface falls between the margin-cropped cameras.
        b.position=Tensor([4.901851654,3.832315445,.005285129]);b.yaw=.858423037
        original_yaw=b.yaw;goal=(4.35,5.);point=(3.69466424,5.203101158,.582581937)
        grid=GridMap(81,81,.1,(0.,0.),bytes([1])*81**2)
        self.build_grid.return_value=(grid,self.build_grid.return_value[1]);self.collision.return_value.grid=grid
        b._navigation_clearance=(grid.free,np.zeros((81,81)),.35)
        b._navigation_layers=[(np.ones((81,81),dtype='uint8'),np.zeros((4,2)))]
        b._navigation_support=np.ones((81,81),dtype='uint8')
        def visible(xy,selected,*_,base_yaw,margin,**kw):
            return bool(visible_rig_rays(xy,base_yaw,.005285129,1.528794232,selected.tolist(),
                                        margin=margin,radius=.35,pitch_degrees=35.))
        b._approach_visible.side_effect=visible
        proposed=NavigationPlan((tuple(b.position.tolist()[:2]),goal),goal,point[:2],original_yaw,
                               1.291521,math.dist(goal,point[:2]),1,1,0.,0,travel_yaw=original_yaw)
        target=SimpleNamespace(get_position_orientation=lambda:(Tensor(point),None))
        with patch('manipulation_agent.executors.gt_navigation.plan_navigation',return_value=proposed):
            b._navigate(target,1000)
        self.assertGreater(abs(b.yaw-original_yaw),.1)
        route=b._execute_gt_plan.call_args.kwargs['prepared_route']
        self.assertLess(math.degrees(route.turn_radians),82.)
        saved=json.loads((b.output/'navigation_plans.jsonl').read_text())
        self.assertEqual(saved['arrival_heading_refinement'][0]['reason'],'target_not_visible')
        self.assertTrue(json.loads((b.output/'navigation_arrivals.jsonl').read_text())['target_visible'])

    def test_arrival_heading_requires_both_entry_and_exit(self):
        b=self.backend;target=self.open_side_target()
        for reject_incoming in (True,False):
            with self.subTest(reject_incoming=reject_incoming):
                b.position=Tensor([.5,1.,0.]);b.yaw=0.
                def certificate(pose,*_,incoming=False,**kw):
                    if abs(pose[2])>.01 and incoming==reject_incoming:return None
                    return {'poses':[pose]}
                with patch('manipulation_agent.executors.forward_navigation.local_exit_certificate',side_effect=certificate):
                    b._navigate(target,1000)
                self.assertAlmostEqual(b.yaw,0.)
                selected=self.route_selection.call_args.args[0]
                self.assertTrue(all(abs(p.final_yaw)<.01 for p in selected))

    def test_arrival_heading_timeout_retains_validated_original(self):
        from manipulation_agent.executors.gt_navigation import SearchBudgetExhausted
        b=self.backend;target=self.open_side_target()
        def certificate(pose,*_,**kw):
            if abs(pose[2])>.01:raise SearchBudgetExhausted()
            return {'poses':[pose]}
        with patch('manipulation_agent.executors.forward_navigation.local_exit_certificate',side_effect=certificate):
            b._navigate(target,1000)
        self.assertAlmostEqual(b.yaw,0.)
        saved=json.loads((b.output/'navigation_plans.jsonl').read_text())
        self.assertTrue(saved['arrival_heading_refinement_budget_exhausted'])

    def test_explicit_approach_rejects_reachable_wrong_side_and_records_arrival(self):
        b=self.backend;result=b._navigate(self.target(),1000)
        self.assertGreater(b.position.tolist()[0],2.)
        self.assertTrue(b._approach_visible.called)
        arrival=json.loads((b.output/'navigation_arrivals.jsonl').read_text())
        self.assertTrue(arrival['target_visible'])
        self.assertEqual(arrival['status'],'reached')

    def test_target_occluded_after_motion_is_not_reported_success(self):
        b=self.backend
        b._approach_visible.side_effect=lambda xy,*_,**kw:not b.moved and xy[0]>2.
        with self.assertRaises(SkillError) as failure:b._navigate(self.target(),1000)
        self.assertEqual(failure.exception.code,'navigation_unreachable')
        self.assertTrue(failure.exception.changed)
        self.assertFalse(json.loads((b.output/'navigation_arrivals.jsonl').read_text())['target_visible'])

    def test_reachable_visible_approach_stops_heading_enumeration(self):
        b=self.backend
        free=np.ones((41,41),dtype='uint8');free[:,24:]=0
        g=GridMap(41,41,.1,(0.,0.),free.tobytes())
        self.build_grid.return_value=(g,self.build_grid.return_value[1])
        b._approach_visible.side_effect=lambda *args,**kwargs:True
        target=SimpleNamespace(get_position_orientation=lambda:(Tensor([2.8,1.,1.5]),None))
        result=b._navigate(target,1000)
        self.assertLess(result['target_projection_distance_m'],1.4)
        self.headings.assert_not_called()
        self.assertTrue(all(call.kwargs['margin']>=.08 for call in b._approach_visible.call_args_list))

    def test_distant_floor_projection_is_approached_and_repeat_reports_no_progress(self):
        b=self.backend
        free=np.ones((41,41),dtype='uint8');free[:,20]=0
        g=GridMap(41,41,.1,(0.,0.),free.tobytes())
        self.build_grid.return_value=(g,self.build_grid.return_value[1])
        target=SimpleNamespace(get_position_orientation=lambda:(Tensor([3.8,1.,0.]),None))
        first=b._navigate(target,1000)
        self.assertEqual(first['nav_status'],'approached')
        self.assertGreater(first['target_projection_distance_m'],1.4)
        self.assertEqual(b._navigate(target,1000)['nav_status'],'already_at_closest_approach')

    def test_local_protocol_low_target_uses_visible_macro_standoff(self):
        b=self.backend
        self.open_side_target()
        b.local_manipulation=True;b.rig_height=1.83;b.rig_radius=.35;b.rig_pitch_degrees=20
        target=SimpleNamespace(get_position_orientation=lambda:(Tensor([.5,2.3,.24]),None))
        b._approach_visible.side_effect=lambda xy,*a,**kw:math.dist(xy,(.5,2.3))>1.4
        result=b._navigate(target,1000)
        self.assertEqual(result['manipulation_handoff'],'target_approach_available')
        self.assertGreater(result['target_projection_distance_m'],1.4)
        self.assertLessEqual(result['target_projection_distance_m'],2.4)
        self.assertLess(b._approach_visible.call_count,100)
        self.headings.assert_not_called()

    def test_distant_initial_projection_does_not_disable_heading_fallback(self):
        b=self.backend
        raw=np.ones((41,41),dtype='uint8');free=raw.copy();free[:,20]=0
        g=GridMap(41,41,.1,(0.,0.),free.tobytes())
        rotated=GridMap(41,41,.1,(0.,0.),raw.tobytes())
        self.build_grid.return_value=(g,self.build_grid.return_value[1])
        self.headings.return_value=[(rotated,.5,rotated,[])]
        result=b._navigate(self.target(0.),1000)
        self.assertEqual(result['nav_status'],'reached')
        self.assertAlmostEqual(b.position.tolist()[0],2.3)
        self.assertEqual(b._execute_gt_plan.call_count,1)
        saved=json.loads((b.output/'navigation_plans.jsonl').read_text())
        self.assertFalse(saved['pre_turn_executed'])
        self.assertIsNotNone(saved['local_exit_certificate'])

    def test_recovery_rechecks_changed_map_and_retreats_again(self):
        b=self.backend
        raw=np.ones((41,41),dtype='uint8');meta=self.build_grid.return_value[1]
        first=raw.copy();first[:,5]=0
        second=first.copy();second[:,4]=0
        grids=[GridMap(41,41,.1,(0.,0.),f.tobytes()) for f in (first,second,second)]
        b._live_gt_collision_cache={0:{'support':raw}}
        def build(*_):
            g=grids.pop(0)
            b._navigation_clearance=(raw.tobytes(),1-np.frombuffer(g.free,dtype='uint8'),.35)
            return g,meta
        self.build_grid.side_effect=build
        with patch('manipulation_agent.executors.live_gt_map.clearance_transition_guard',return_value=lambda a,b:True):
            result=b._navigate(SimpleNamespace(get_position_orientation=lambda:(Tensor([.1,1.,0.]),None)),1000)
        records=[json.loads(s) for s in (b.output/'navigation_recovery.jsonl').read_text().splitlines()]
        completed=[r for r in records if 'refreshed_start_navigable' in r]
        self.assertEqual([r['refreshed_start_navigable'] for r in completed],[False,True])
        self.assertEqual(result['steps'],9)
        self.assertEqual(b._execute_gt_plan.call_count,3)

    def test_failed_forward_plan_does_not_execute_an_obsolete_pre_turn(self):
        b=self.backend
        raw=np.ones((41,41),dtype='uint8');free=raw.copy();free[:,20]=0
        grid=GridMap(41,41,.1,(0.,0.),free.tobytes())
        rotated=GridMap(41,41,.1,(0.,0.),raw.tobytes())
        self.build_grid.return_value=(grid,self.build_grid.return_value[1])
        self.headings.return_value=[(rotated,.5,rotated,[])]
        b._execute_gt_plan.side_effect=SkillError('navigation_unreachable','No forward route')
        with self.assertRaises(SkillError) as caught:
            b._navigate(self.target(0.),1000)
        self.assertFalse(caught.exception.changed)
        self.assertEqual(caught.exception.code,'navigation_unreachable')
        self.assertEqual(b._execute_gt_plan.call_count,1)

    def test_terminal_without_exit_is_reprojected_before_motion(self):
        b=self.backend
        target=self.target(0.)
        def certificate(pose,*_,**kw):
            if kw.get('incoming'):return {'poses':[pose], 'scope':'fixture'}
            return {'poses':[pose], 'scope':'fixture'} if np.linalg.norm(np.array(pose[:2])-[2.3,1.])>.21 else None
        with patch('manipulation_agent.executors.forward_navigation.local_exit_certificate',side_effect=certificate):
            result=b._navigate(target,1000)
        self.assertGreater(result['target_projection_distance_m'],.21)
        saved=json.loads((b.output/'navigation_plans.jsonl').read_text())
        self.assertGreater(saved['exit_candidates_rejected'],0)
        self.assertIsNotNone(saved['local_exit_certificate'])

    def test_arrival_revalidates_exit_instead_of_claiming_success(self):
        b=self.backend
        with patch('manipulation_agent.executors.forward_navigation.local_exit_certificate',
                   side_effect=lambda pose,*_,**kw:None if b.moved else {'poses':[pose]}):
            with self.assertRaises(SkillError) as caught:b._navigate(self.target(0.),1000)
        self.assertTrue(caught.exception.changed)
        arrival=json.loads((b.output/'navigation_arrivals.jsonl').read_text())
        self.assertEqual(arrival['status'],'failed')
        self.assertIsNone(arrival['local_exit_certificate'])

    def test_recovery_stops_cycle_and_marks_world_changed(self):
        b=self.backend;raw=np.ones((41,41),dtype='uint8');free=raw.copy();free[:,5]=0
        g=GridMap(41,41,.1,(0.,0.),free.tobytes())
        self.build_grid.return_value=(g,self.build_grid.return_value[1])
        b._navigation_clearance=(raw.tobytes(),1-free.ravel(),.35)
        b._live_gt_collision_cache={0:{'support':raw}}
        b._execute_gt_plan.side_effect=lambda *a,**kw:{'steps':3,'motion_steps':3,'actual_path_distance_m':.01}
        with patch('manipulation_agent.executors.live_gt_map.clearance_transition_guard',return_value=lambda a,b:True):
            with self.assertRaises(SkillError) as error:b._navigate(self.target(0.),1000)
        self.assertEqual(error.exception.code,'navigation_invalid_start')
        self.assertTrue(error.exception.changed)
        self.assertEqual(b._execute_gt_plan.call_count,1)
        records=[json.loads(s) for s in (b.output/'navigation_recovery.jsonl').read_text().splitlines()]
        self.assertEqual(records[-1]['status'],'failed')

    def test_floor_click_remains_point_navigation_without_camera_constraint(self):
        b=self.backend;b._approach_visible.side_effect=AssertionError('Ground must not use camera visibility')
        result=b._navigate(self.target(0.),1000)
        self.assertEqual(result['goal_mode'],'point')
        self.assertAlmostEqual(b.position.tolist()[0],2.3)
        b._approach_visible.assert_not_called()


if __name__=='__main__':unittest.main()
