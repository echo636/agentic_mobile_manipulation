"""Whole-assembly collision regressions; real numpy/torch, fake PhysX boundary."""
import importlib.util
import math
import sys
import unittest
from types import SimpleNamespace,ModuleType
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('torch'),'Pinned simulator Python required')
class CarryGeometryTests(unittest.TestCase):
    def scene(self, *, ceiling=False, wall=False, extra_payload=False):
        import numpy as np
        import torch
        from manipulation_agent.executors.carry_geometry import PayloadSweep
        class Body:
            n_joints=0
            def __init__(self,name,lo,hi):
                self.name=name;self.prim_path='/'+name
                self.aabb=(torch.tensor(lo,dtype=torch.float32),torch.tensor(hi,dtype=torch.float32))
                self.position=(self.aabb[0]+self.aabb[1])/2
                points=torch.tensor([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
                self.links={'base':SimpleNamespace(prim_path=self.prim_path,is_meta_link=False,
                    collision_boundary_points_world=points,aabb=self.aabb)}
            def get_position_orientation(self):return self.position,torch.tensor([0.,0.,0.,1.])
        robot=Body('robot',[-.2,-.2,.03],[.2,.2,1.5]);robot.position=torch.zeros(3)
        obj=Body('plate',[1.1,-.08,.9],[1.3,.08,1.0])
        table=Body('table',[.8,-.4,0.],[1.6,.4,.9])
        objects=[robot,obj,table];payload=[obj]
        if ceiling:objects.append(Body('ceiling',[1.,-.2,1.05],[1.4,.2,1.15]))
        if wall:objects.append(Body('wall',[.82,-.2,1.05],[.85,.2,1.4]))
        if extra_payload:
            child=Body('food',[1.2,-.05,1.],[1.4,.05,1.15]);objects.append(child);payload.append(child)
            objects.append(Body('over_food',[1.32,-.1,1.24],[1.5,.1,1.4]))
        def overlap(half,center,orientation,callback,any_hit):
            low=np.asarray(center)-half;high=np.asarray(center)+half
            for body in objects:
                a,b=(v.numpy() for v in body.aabb)
                if np.all(high>=a) and np.all(low<=b):
                    if callback(SimpleNamespace(rigid_body=body.prim_path)) is False:break
        og=ModuleType('omnigibson');lazy=ModuleType('omnigibson.lazy');og.lazy=lazy
        lazy.omni=SimpleNamespace(physx=SimpleNamespace(get_physx_scene_query_interface=lambda:SimpleNamespace(overlap_box=overlap)))
        backend=SimpleNamespace(robot=robot,env=SimpleNamespace(scene=SimpleNamespace(objects=objects)),_local_pose=lambda:(0.,0.,0.))
        with patch.dict(sys.modules,{'omnigibson':og,'omnigibson.lazy':lazy}):
            return PayloadSweep(backend,obj,payload,(0.,0.,0.)),backend,obj

    def test_supported_plate_can_lift_then_compact_above_counter(self):
        from manipulation_agent.executors.carry_geometry import plan_lift_and_compact
        sweep,_,_=self.scene()
        route,status=plan_lift_and_compact(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.clear)
        self.assertEqual(status,'compact');self.assertIsNotNone(route)
        self.assertAlmostEqual(route[1][2]-route[0][2],.18)
        self.assertLess(math.hypot(*route[-1][:2]),.7)
        self.assertTrue(all(sweep.clear(a,b,extraction=i==0) for i,(a,b) in enumerate(zip(route,route[1:]))))

    def test_new_contact_during_lift_is_rejected(self):
        from manipulation_agent.executors.carry_geometry import plan_lift_and_compact
        sweep,_,_=self.scene(ceiling=True)
        route,status=plan_lift_and_compact(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.clear)
        self.assertIsNone(route);self.assertEqual(status,'lift_blocked')
        self.assertIn('/ceiling',sweep.last_failure['blockers'])

    def test_nested_payload_is_checked_during_lift(self):
        from manipulation_agent.executors.carry_geometry import plan_lift_and_compact
        sweep,_,_=self.scene(extra_payload=True)
        route,status=plan_lift_and_compact(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.clear)
        self.assertIsNone(route);self.assertEqual(status,'lift_blocked')
        self.assertIn('/over_food',sweep.last_failure['blockers'])

    def test_translation_checks_between_clear_endpoints(self):
        sweep,_,_=self.scene(wall=True)
        start=(*sweep.origin[:2],sweep.origin[2]+.18)
        self.assertFalse(sweep.clear(start,(.55,0.,start[2])))
        self.assertIn('/wall',sweep.last_failure['blockers'])

    def test_hypothetical_robot_pose_is_a_real_collision_body(self):
        sweep,_,_=self.scene()
        start=(*sweep.origin[:2],sweep.origin[2]+.18)
        self.assertFalse(sweep.clear(start,(.1,0.,start[2])))
        self.assertTrue(any(k.startswith('robot_link:') for k in sweep.last_failure['blockers']))

    def test_loaded_departure_sweeps_payload_not_only_base(self):
        from manipulation_agent.executors.carry_geometry import LoadedCollision
        sweep,_,_=self.scene(wall=True)
        lifted=(*sweep.origin[:2],sweep.origin[2]+.18)
        checker=LoadedCollision(SimpleNamespace(grid=None,motion_free=lambda a,b:True),sweep,(0.,0.,0.),
                                (tuple(sweep.origin),lifted))
        self.assertTrue(checker.motion_free((0.,0.,0.),(.2,0.,0.)))
        self.assertFalse(checker.motion_free((0.,0.,0.),(-.4,0.,0.)))
        self.assertTrue(sweep.robot_bounds)


if __name__=='__main__':unittest.main()
