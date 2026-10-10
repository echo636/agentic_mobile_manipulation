"""Current-scene occupancy/cache behavior with the PhysX query boundary replaced."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('torch'),
                     'Run with the pinned simulator Python; simulator itself is not started')
class LiveGridTests(unittest.TestCase):
    def test_rigid_props_door_updates_and_robot_exclusion(self):
        import cv2
        import numpy as np
        import torch
        from manipulation_agent.executors.live_gt_map import build_navigation_grid
        class Obj:
            def __init__(self,name,lo,hi,position,n_joints=0):
                self.name=name;self.aabb=(torch.tensor(lo),torch.tensor(hi));self.position=torch.tensor(position)
                self.n_joints=n_joints;self.angle=0.;self.links={}
            def get_position_orientation(self):return self.position,torch.tensor([0.,0.,0.,1.])
            def get_joint_positions(self):
                if not self.n_joints:raise AssertionError('Rigid entity must not receive a joint query')
                return torch.tensor([self.angle])
        with tempfile.TemporaryDirectory() as temp:
            layout=Path(temp)/'layout';layout.mkdir();cv2.imwrite(str(layout/'floor_trav_no_obj_0.png'),np.full((40,40),255,dtype='uint8'))
            robot=Obj('robot',[-.8,-.1,0.],[-.6,.1,1.5],[-.7,0.,0.])
            link=SimpleNamespace(is_meta_link=False,prim_path='/robot/base',collision_boundary_points_world=torch.tensor([
                [-.8,-.1,.05],[-.8,.1,.05],[-.6,-.1,.05],[-.6,.1,.05]]))
            robot.links={'base':link}
            wall=Obj('wall',[-.04,-1.,0.],[.04,1.,1.8],[0.,0.,0.])
            door=Obj('door',[-.04,-.4,0.],[.04,.4,1.8],[0.,0.,0.],1)
            def overlap(half_extent,origin,rotation,callback,any_hit):
                # Robot geometry is always ignored, even when PhysX reports it
                # before a real blocker. Walls remain and the door can open.
                if callback(SimpleNamespace(rigid_body='/robot/base')):
                    x,y,z=origin
                    if abs(x)<.049 and (abs(y)>.4 or door.angle==0):callback(SimpleNamespace(rigid_body='/door/leaf'))
            fake=ModuleType('omnigibson');lazy=ModuleType('omnigibson.lazy');fake.lazy=lazy
            lazy.omni=SimpleNamespace(physx=SimpleNamespace(get_physx_scene_query_interface=lambda:SimpleNamespace(overlap_box=overlap)))
            trav=SimpleNamespace(map_resolution=.05,floor_heights=[0.],floor_map=[torch.ones((40,40))])
            scene=SimpleNamespace(scene_dir=temp,trav_map=trav,objects=[wall,door,robot])
            backend=SimpleNamespace(robot=robot,env=SimpleNamespace(scene=scene),_get_held=lambda:None,_carry_contents=[],deadline=SimpleNamespace(check=lambda:None))
            with patch.dict(sys.modules,{'omnigibson':fake,'omnigibson.lazy':lazy}):
                closed,meta=build_navigation_grid(backend,0)
                self.assertFalse(closed.navigable(closed.cell((0.,0.))))
                self.assertTrue(closed.navigable(closed.cell((-.7,0.))))
                door.angle=1.57
                opened,updated=build_navigation_grid(backend,0)
                self.assertTrue(opened.navigable(opened.cell((0.,0.))))
                self.assertFalse(opened.navigable(opened.cell((0.,.7))))
                self.assertGreater(updated['updated_cells'],0)
                self.assertLess(updated['updated_cells'],meta['updated_cells'])
                _,unchanged=build_navigation_grid(backend,0)
                self.assertEqual(unchanged['updated_cells'],0)

    def test_heading_fallback_checks_rotation_sweep(self):
        import numpy as np
        from manipulation_agent.executors.gt_navigation import GridMap
        from manipulation_agent.executors.live_gt_map import feasible_heading_grids
        raw=np.ones((41,41),dtype='uint8')
        grid=GridMap(41,41,.05,(-1.,-1.),raw.tobytes())
        footprint=[[-.4,-.06],[.4,-.06],[.4,.06],[-.4,.06]]
        clear=list(feasible_heading_grids(grid,raw.tobytes(),footprint,(0.,0.),0.))
        self.assertEqual(len(clear),24)
        # End orientations at 0 and 90 degrees miss this obstacle, but the
        # body crosses it during the turn. Endpoint-only checking is unsafe.
        raw[25,25]=0
        blocked=list(feasible_heading_grids(grid,raw.tobytes(),footprint,(0.,0.),0.))
        positive=[yaw for _,yaw,_,_ in blocked if yaw>0]
        self.assertTrue(all(yaw<np.pi/4 for yaw in positive))

    def test_height_layers_do_not_extend_wide_base_into_overhead_obstacle(self):
        import numpy as np
        from manipulation_agent.executors.gt_navigation import GridMap
        from manipulation_agent.executors.live_gt_map import feasible_heading_grids
        raw=np.ones((41,41),dtype='uint8');upper=raw.copy();upper[:,25]=0
        grid=GridMap(41,41,.05,(-1.,-1.),raw.tobytes())
        wide=[[-.35,-.35],[.35,-.35],[.35,.35],[-.35,.35]]
        narrow=[[-.05,-.05],[.05,-.05],[.05,.05],[-.05,.05]]
        self.assertFalse(list(feasible_heading_grids(grid,upper.tobytes(),wide,(0.,0.),0.)))
        layered=list(feasible_heading_grids(grid,upper.tobytes(),wide,(0.,0.),0.,
                     collision_layers=[(raw,wide),(upper,narrow)]))
        self.assertTrue(layered)
        self.assertTrue(all(turn.navigable(turn.cell((0.,0.))) for _,_,turn,_ in layered))

    def test_collision_hull_is_clipped_before_height_projection(self):
        import numpy as np
        from manipulation_agent.executors.live_gt_map import clipped_body_projection
        points=np.array([[x,y,z] for z,radius in [(0.,.1),(1.,.4)]
                         for x in (-radius,radius) for y in (-radius,radius)])
        lower=clipped_body_projection(points,0.,.2)
        self.assertAlmostEqual(float(abs(lower).max()),.16,places=6)
        self.assertIsNone(clipped_body_projection(points,1.1,1.2))
