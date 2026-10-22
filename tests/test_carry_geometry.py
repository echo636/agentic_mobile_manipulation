"""Whole-assembly collision regressions; real numpy/torch, fake PhysX boundary."""
import importlib.util
import math
import sys
import unittest
from types import SimpleNamespace,ModuleType
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('torch'),'Pinned simulator Python required')
class CarryGeometryTests(unittest.TestCase):
    def scene(self, *, ceiling=False, wall=False, extra_payload=False, compound_support=False,
              support_roof=False, low_ceiling=False, split_payload=False):
        import numpy as np
        import torch
        from manipulation_agent.executors.carry_geometry import PayloadSweep
        class Body:
            n_joints=0
            def __init__(self,name,lo,hi):
                self.name=name;self.prim_path='/'+name
                self.aabb=(torch.tensor(lo,dtype=torch.float32),torch.tensor(hi,dtype=torch.float32))
                self.collider_boxes=[self.aabb]
                self.position=(self.aabb[0]+self.aabb[1])/2
                points=torch.tensor([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
                self.links={'base':SimpleNamespace(prim_path=self.prim_path,is_meta_link=False,
                    collision_boundary_points_world=points,aabb=self.aabb)}
            def get_position_orientation(self):return self.position,torch.tensor([0.,0.,0.,1.])
        robot=Body('robot',[-.2,-.2,.03],[.2,.2,1.5]);robot.position=torch.zeros(3)
        obj=Body('plate',[1.1,-.08,.9],[1.3,.08,1.0])
        table=Body('table',[.8,-.4,0.],[1.6,.4,.9])
        if compound_support:
            # Like a trunk: a low support and tall side share one rigid body.
            # Its broad AABB encloses the freely rising payload.
            side=Body('side',[1.55,-.4,0.],[1.6,.4,1.6])
            table.collider_boxes.append(side.aabb)
            table.aabb=(table.aabb[0],side.aabb[1])
            table.links['base'].aabb=table.aabb
            if support_roof:
                roof=Body('roof',[1.,-.2,1.08],[1.4,.2,1.09])
                table.collider_boxes.append(roof.aabb)
        objects=[robot,obj,table];payload=[obj]
        if ceiling:objects.append(Body('ceiling',[1.,-.2,1.005],[1.4,.2,1.25]))
        if low_ceiling:objects.append(Body('ceiling',[1.,-.2,1.10],[1.4,.2,1.25]))
        if split_payload:
            bracket=Body('bracket',[.8,-.4,.86],[1.6,.4,.89])
            left=Body('left',[.8,-.4,.86],[1.,.4,.89])
            right=Body('right',[1.4,-.4,.86],[1.6,.4,.89])
            bracket.links['base'].collision_meshes={
                'left':SimpleNamespace(aabb=left.aabb),'right':SimpleNamespace(aabb=right.aabb)}
            objects.append(bracket);payload.append(bracket)
            # The support occupies only the empty centre of this rigid link.
            table.collider_boxes=[(torch.tensor([1.05,-.3,0.]),torch.tensor([1.35,.3,.9]))]
        if wall:objects.append(Body('wall',[.82,-.2,1.05],[.85,.2,1.4]))
        if extra_payload:
            child=Body('food',[1.2,-.05,1.],[1.4,.05,1.15]);objects.append(child);payload.append(child)
            objects.append(Body('over_food',[1.32,-.1,1.155],[1.5,.1,1.4]))
        def overlap(half,center,orientation,callback,any_hit):
            low=np.asarray(center)-half;high=np.asarray(center)+half
            for body in objects:
                for a,b in body.collider_boxes:
                    a,b=a.numpy(),b.numpy()
                    if np.all(high>=a) and np.all(low<=b):
                        if callback(SimpleNamespace(rigid_body=body.prim_path)) is False:return
        og=ModuleType('omnigibson');lazy=ModuleType('omnigibson.lazy');og.lazy=lazy
        lazy.omni=SimpleNamespace(physx=SimpleNamespace(get_physx_scene_query_interface=lambda:SimpleNamespace(overlap_box=overlap)))
        backend=SimpleNamespace(robot=robot,env=SimpleNamespace(scene=SimpleNamespace(objects=objects)),_local_pose=lambda:(0.,0.,0.))
        with patch.dict(sys.modules,{'omnigibson':og,'omnigibson.lazy':lazy}):
            return PayloadSweep(backend,obj,payload,(0.,0.,0.)),backend,obj

    def test_instant_carry_does_not_require_clear_extraction(self):
        from manipulation_agent.executors.carry_geometry import plan_instant_carry
        for options in ({'ceiling':True},{'compound_support':True,'support_roof':True},{'extra_payload':True}):
            sweep,_,_=self.scene(**options)
            route,status=plan_instant_carry(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.destination_clear)
            self.assertEqual(status,'instant_pose_transfer')
            self.assertEqual(len(route),2)
            self.assertTrue(sweep.destination_clear(route[-1]))

    def test_instant_destination_checks_robot_and_entire_payload(self):
        from manipulation_agent.executors.carry_geometry import plan_instant_carry
        sweep,_,_=self.scene(extra_payload=True)
        self.assertFalse(sweep.destination_clear((.1,0.,.9)))
        self.assertTrue(any(k.startswith('robot_link:') for k in sweep.last_failure['blockers']))
        checks=[]
        route,status=plan_instant_carry(sweep.origin,(0.,0.,0.,0.),sweep.bounds,
                                      lambda end:checks.append(end) or False)
        self.assertIsNone(route);self.assertEqual(status,'carry_destination_blocked')
        self.assertGreater(len(checks),1)

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

    def test_low_ceiling_uses_clear_short_lift_and_horizontal_extraction(self):
        from manipulation_agent.executors.carry_geometry import plan_lift_and_compact
        sweep,_,_=self.scene(low_ceiling=True)
        route,status=plan_lift_and_compact(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.clear)
        self.assertEqual(status,'compact')
        self.assertAlmostEqual(route[1][2]-route[0][2],.08)
        self.assertLess(route[-1][0],.7)
        for i,(a,b) in enumerate(zip(route,route[1:])):
            self.assertTrue(sweep.clear(a,b,extraction=i==0))

    def test_separate_colliders_do_not_fill_empty_bracket_centre(self):
        from manipulation_agent.executors.carry_geometry import plan_lift_and_compact
        sweep,_,_=self.scene(split_payload=True)
        self.assertEqual(len(sweep.bounds),3)
        route,status=plan_lift_and_compact(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.clear)
        self.assertIsNotNone(route)
        self.assertTrue(sweep.clear(route[0],route[1],extraction=True))
        # A real side collider still cannot enter the central pedestal.
        start=(*sweep.origin[:2],sweep.origin[2]+.01)
        self.assertFalse(sweep.clear(start,(start[0]+.1,*start[1:])))
        self.assertIn('/table',sweep.last_failure['blockers'])

    def test_concave_support_aabb_does_not_block_free_vertical_extraction(self):
        from manipulation_agent.executors.carry_geometry import plan_lift_and_compact
        sweep,_,_=self.scene(compound_support=True)
        self.assertIn('/table',sweep.contacts(sweep.bounds))
        route,status=plan_lift_and_compact(sweep.origin,(0.,0.,0.,0.),sweep.bounds,sweep.clear)
        self.assertIsNotNone(route);self.assertEqual(status,'compact')
        # Execution rechecks small increments, with support clearance required
        # at the end of the lift rather than at each intermediate step.
        from manipulation_agent.executors.carry_posture import translation_samples
        previous=route[0]
        for point in translation_samples(route[:2],.01):
            self.assertTrue(sweep.clear(previous,point,extraction=True,
                                       require_clear=math.dist(point,route[1])<1e-8))
            previous=point

    def test_new_shape_of_existing_support_cannot_be_crossed_during_lift(self):
        sweep,_,_=self.scene(compound_support=True,support_roof=True)
        end=(*sweep.origin[:2],sweep.origin[2]+.18)
        # Both start and end clear the thin overhead part. The sweep must still
        # reject it, even though it belongs to the already-contacting body.
        self.assertFalse(sweep.clear(sweep.origin,end,extraction=True))
        self.assertEqual(sweep.last_failure['stage'],'extraction_entering_slab')
        self.assertIn('/table',sweep.last_failure['blockers'])

    def test_extraction_requires_clear_endpoint_and_cannot_move_sideways(self):
        sweep,_,_=self.scene(compound_support=True)
        end=(*sweep.origin[:2],sweep.origin[2]+.0001)
        self.assertTrue(sweep.clear(sweep.origin,end,extraction=True,require_clear=False))
        self.assertFalse(sweep.clear(sweep.origin,end,extraction=True))
        self.assertEqual(sweep.last_failure['stage'],'translation_endpoint')
        with self.assertRaises(ValueError):
            sweep.clear(sweep.origin,(sweep.origin[0]+.1,*sweep.origin[1:]),extraction=True)

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
