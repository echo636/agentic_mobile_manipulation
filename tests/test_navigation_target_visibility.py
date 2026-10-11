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
from manipulation_agent.executors.gt_navigation import GridMap
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
        b._navigation_layers=[(free,None)]
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
        p=patch('manipulation_agent.executors.live_gt_map.build_navigation_grid',return_value=(grid,
            {'chassis_footprint_world_offsets':[],'collision_layers':[],'dynamic_collision_check':True}))
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

    def test_floor_click_remains_point_navigation_without_camera_constraint(self):
        b=self.backend;b._approach_visible.side_effect=AssertionError('Ground must not use camera visibility')
        result=b._navigate(self.target(0.),1000)
        self.assertEqual(result['goal_mode'],'point')
        self.assertAlmostEqual(b.position.tolist()[0],2.3)
        b._approach_visible.assert_not_called()


if __name__=='__main__':unittest.main()
