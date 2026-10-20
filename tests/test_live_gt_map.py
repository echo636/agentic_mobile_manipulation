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
    def test_gap_between_robot_and_carried_body_is_not_a_solid_bridge(self):
        import cv2
        import numpy as np
        import torch
        from manipulation_agent.executors.live_gt_map import build_navigation_grid
        class Body:
            n_joints=0
            def __init__(self,name,lo,hi):
                self.name=name;self.aabb=(torch.tensor(lo),torch.tensor(hi))
                self.position=(self.aabb[0]+self.aabb[1])/2
                points=torch.tensor([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
                self.links={'body':SimpleNamespace(is_meta_link=False,prim_path='/'+name,collision_boundary_points_world=points)}
            def get_position_orientation(self):return self.position,torch.tensor([0.,0.,0.,1.])
        robot=Body('robot',[-.2,-.2,.04],[.2,.2,1.2]);robot.position=torch.zeros(3)
        payload=Body('payload',[.95,-.05,.45],[1.05,.05,.55])
        obstacle=Body('obstacle',[.46,-.04,.45],[.54,.04,.55])
        queries=[]
        def overlap(half,center,rotation,callback,any_hit):
            queries.append((half,center))
            low=np.asarray(center)-half;high=np.asarray(center)+half
            for body in (obstacle,payload):
                if np.all(high>=body.aabb[0].numpy()) and np.all(low<=body.aabb[1].numpy()):
                    if callback(SimpleNamespace(rigid_body='/'+body.name)) is False:break
        modules={name:ModuleType(name) for name in ('omnigibson','omnigibson.lazy')}
        modules['omnigibson'].lazy=modules['omnigibson.lazy']
        modules['omnigibson.lazy'].omni=SimpleNamespace(physx=SimpleNamespace(
            get_physx_scene_query_interface=lambda:SimpleNamespace(overlap_box=overlap)))
        with tempfile.TemporaryDirectory() as temp,patch.dict(sys.modules,modules):
            layout=Path(temp)/'layout';layout.mkdir()
            cv2.imwrite(str(layout/'floor_trav_no_obj_0.png'),np.full((80,80),255,dtype='uint8'))
            trav=SimpleNamespace(map_resolution=.05,floor_heights=[0.],floor_map=[torch.ones((80,80))])
            b=SimpleNamespace(robot=robot,_get_held=lambda:None,_carry_contents=[],deadline=SimpleNamespace(check=lambda:None),
                env=SimpleNamespace(scene=SimpleNamespace(scene_dir=temp,trav_map=trav,objects=[robot,payload,obstacle])))
            _,empty=build_navigation_grid(b,0)
            b._get_held=lambda:payload
            queries.clear()
            grid,meta=build_navigation_grid(b,0)
            self.assertTrue(grid.navigable(grid.cell((0.,0.))))
            self.assertEqual({r['body_group'] for r in meta['collision_layers']},{0,1})
            self.assertEqual(meta['occupancy_height_bands'],empty['occupancy_height_bands'])
            self.assertLess(meta['updated_cells'],empty['updated_cells'])
            self.assertGreater(meta['updated_cells'],0)
            self.assertEqual(len(queries),meta['updated_cells']*meta['occupancy_height_bands'])
            self.assertGreater(len(meta['collision_layers']),meta['occupancy_height_bands'])
            # Keep actual carried geometry checked, even though the gap is free.
            obstacle.aabb=tuple(a+torch.tensor([.5,0.,0.]) for a in obstacle.aabb)
            obstacle.position+=torch.tensor([.5,0.,0.])
            grid,_=build_navigation_grid(b,0)
            self.assertFalse(grid.navigable(grid.cell((0.,0.))))

    def test_occupied_centre_can_shed_contacts_but_cannot_enter_new_obstacles(self):
        import cv2
        import numpy as np
        from manipulation_agent.executors.gt_navigation import GridMap,plan_start_clearance_exit,NavigationError
        from manipulation_agent.executors.live_gt_map import clearance_transition_guard,footprint_kernel
        raw=np.ones((21,21),dtype='uint8');raw[:,10]=0
        footprint=np.array([[-.2,-.1],[.2,-.1],[.2,.1],[-.2,.1]])
        kernel=footprint_kernel(footprint,.1)
        free=cv2.erode(raw,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
        overlap=cv2.filter2D((1-raw).astype('float32'),-1,kernel.astype('float32')).ravel()
        g=GridMap(21,21,.1,(0.,0.),free.tobytes())
        guard=clearance_transition_guard(g,[(raw,footprint)],np.ones_like(raw))
        self.assertTrue(guard((10,10),(10,9)))
        # Contact pixels can slide along the same leaf while retreating.
        self.assertTrue(guard((10,10),(11,10)))
        other=raw.copy();other[12,8]=0
        guarded=clearance_transition_guard(g,[(other,footprint)],np.ones_like(raw))
        self.assertFalse(guarded((10,10),(11,10)))
        # A free centre must not cross into the wall during recovery.
        self.assertFalse(guard((10,9),(10,10)))
        corridor,p,report=plan_start_clearance_exit(g,raw.tobytes(),overlap,(1.,1.),radius=.4,
                                                   transition_allowed=guard)
        self.assertTrue(g.navigable(g.cell(p.goal)))
        self.assertTrue(report['new_contacts_forbidden'])
        self.assertTrue(all(corridor.segment_free(a,b) for a,b in zip(p.points,p.points[1:])))
        blocked_support=np.ones_like(raw);blocked_support[10,10]=0
        with self.assertRaisesRegex(NavigationError,'supported'):
            plan_start_clearance_exit(g,raw.tobytes(),overlap,(1.,1.),radius=.4,
                transition_allowed=clearance_transition_guard(g,[(raw,footprint)],blocked_support))

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
                carried=Obj('held',[-.75,.55,.5],[.1,.65,.6],[-.3,.6,.55])
                carried.links={'bar':SimpleNamespace(is_meta_link=False,prim_path='/held/bar',
                    collision_boundary_points_world=torch.tensor([
                        [x,y,z] for x in (-.75,.1) for y in (.55,.65) for z in (.5,.6)]))}
                backend._get_held=lambda:carried
                carrying,carry_meta=build_navigation_grid(backend,0)
                self.assertTrue(carry_meta['held_object_footprint_included'])
                self.assertEqual(carry_meta['carried_collision_hulls'],1)
                self.assertGreater(len(carry_meta['collision_layers']),len(meta['collision_layers']))
                self.assertFalse(carrying.navigable(carrying.cell((-.7,0.))))

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
