"""An explicit elevated click cannot accept a reachable goal behind a wall."""
import json
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
        def execute(grid,plan,limit):
            b.moved=True;b.position=Tensor([*plan.goal,0.])
            return {'steps':3,'motion_steps':3,'actual_path_distance_m':plan.geodesic_m}
        b._execute_gt_plan=Mock(side_effect=execute)
        modules={name:ModuleType(name) for name in ['omnigibson','omnigibson.utils','omnigibson.utils.transform_utils']}
        modules['omnigibson'].utils=modules['omnigibson.utils']
        modules['omnigibson.utils'].transform_utils=modules['omnigibson.utils.transform_utils']
        modules['omnigibson.utils.transform_utils'].quat2mat=lambda _:np.eye(3)
        p=patch.dict(sys.modules,modules);p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.heading_collision.HeadingCollisionChecker',
                return_value=SimpleNamespace(motion_free=lambda a,b:True))
        p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.live_gt_map.build_navigation_grid',return_value=(grid,
            {'chassis_footprint_world_offsets':[],'collision_layers':[],'dynamic_collision_check':True}))
        self.build_grid=p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.live_gt_map.feasible_heading_grids',return_value=[])
        self.headings=p.start();self.addCleanup(p.stop)
        p=patch('manipulation_agent.executors.live_gt_map.rotation_staging_plan',side_effect=NavigationError('No staging'))
        p.start();self.addCleanup(p.stop)

    def target(self,z=1.):
        return SimpleNamespace(get_position_orientation=lambda:(Tensor([2.3,1.,z]),None))

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
        self.assertEqual(b._execute_gt_plan.call_count,2)

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

    def test_failed_forward_plan_after_turn_reports_changed_world(self):
        b=self.backend
        raw=np.ones((41,41),dtype='uint8');free=raw.copy();free[:,20]=0
        grid=GridMap(41,41,.1,(0.,0.),free.tobytes())
        rotated=GridMap(41,41,.1,(0.,0.),raw.tobytes())
        self.build_grid.return_value=(grid,self.build_grid.return_value[1])
        self.headings.return_value=[(rotated,.5,rotated,[])]
        b._execute_gt_plan.side_effect=[{'steps':3,'motion_steps':3,'actual_path_distance_m':0.},
                                        SkillError('navigation_unreachable','No forward route')]
        with self.assertRaises(SkillError) as caught:
            b._navigate(self.target(0.),1000)
        self.assertTrue(caught.exception.changed)
        self.assertEqual(caught.exception.code,'navigation_unreachable')

    def test_recovery_stops_cycle_and_marks_world_changed(self):
        b=self.backend;raw=np.ones((41,41),dtype='uint8');free=raw.copy();free[:,5]=0
        g=GridMap(41,41,.1,(0.,0.),free.tobytes())
        self.build_grid.return_value=(g,self.build_grid.return_value[1])
        b._navigation_clearance=(raw.tobytes(),1-free.ravel(),.35)
        b._live_gt_collision_cache={0:{'support':raw}}
        b._execute_gt_plan.side_effect=lambda *a:{'steps':3,'motion_steps':3,'actual_path_distance_m':.01}
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
