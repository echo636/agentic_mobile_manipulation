"""Regressions for drawer payload loss and unanchored internal physics ticks."""
import importlib.util
from contextlib import nullcontext
import types
import unittest
from unittest.mock import patch
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.placement import CheckedPlacement
from manipulation_agent.contracts import SkillError


@unittest.skipUnless(importlib.util.find_spec('torch'),'Requires geometry environment')
class ContainerStabilityTests(unittest.TestCase):
    def test_contents_follow_their_own_fillable_link_only(self):
        import torch
        class Obj:pass
        class Inside:pass
        def obj(x):
            o=Obj();o.position=torch.tensor([x,0.,0.]);o.fixed_base=False
            o.get_position_orientation=lambda:(o.position.clone(),torch.tensor([0.,0.,0.,1.]))
            o.set_position_orientation=lambda p,q:setattr(o,'position',p.clone())
            o.keep_still=lambda:None
            o.aabb=(o.position-.01,o.position+.01)
            return o
        drawer,fixed=obj(1.),obj(3.)
        for link in (drawer,fixed):
            link.is_meta_link=True;link.meta_link_type='fillable'
            link.check_points_in_volume=lambda points,link=link:torch.linalg.norm(points-link.position,dim=1)<.5
        container=obj(0.);container.aabb=(torch.tensor([-5.,-5.,-5.]),torch.tensor([5.,5.,5.]))
        container.links={'drawer':drawer,'fixed':fixed}
        moving,stationary,outside=obj(1.2),obj(3.2),obj(8.)
        for child in (moving,stationary,outside):
            child.states={Inside:types.SimpleNamespace(get_value=lambda parent,child=child:child is not outside)}
        b=ControlledCarry();b.robot=Obj();b.env=types.SimpleNamespace(scene=types.SimpleNamespace(objects=[container,moving,stationary,outside,b.robot]))
        states=types.ModuleType('omnigibson.object_states');states.Inside=Inside
        utils=types.ModuleType('omnigibson.utils');utils.transform_utils=types.SimpleNamespace(
            relative_pose_transform=lambda p,q,base,bq:(p-base,q),pose_transform=lambda base,bq,p,q:(base+p,q))
        with patch.dict('sys.modules',{'omnigibson.object_states':states,'omnigibson.utils':utils}):
            payload=b._container_payload(container)
            self.assertEqual([p[0] for p in payload],[moving,stationary])
            drawer.position[0]=0.
            b._relocate_container_payload(payload)
        self.assertAlmostEqual(float(moving.position[0]),.2,places=6)
        self.assertAlmostEqual(float(stationary.position[0]),3.2,places=6)
        self.assertEqual(float(outside.position[0]),8.)

    def test_internal_physics_anchors_container_and_restores_wrapper_after_failure(self):
        import torch
        b=ControlledCarry();b.torch=torch;b._base_target=None;b._object_anchor=None
        pos=torch.tensor([1.,2.,3.]);quat=torch.tensor([0.,0.,0.,1.]);base=torch.zeros(3)
        obj=types.SimpleNamespace(get_position_orientation=lambda:(pos.clone(),quat.clone()),
            set_position_orientation=lambda p,q:pos.copy_(p),keep_still=lambda:None)
        b.robot=types.SimpleNamespace(get_position_orientation=lambda:(base.clone(),quat.clone()),
            get_joint_positions=lambda:torch.zeros(4),base_idx=torch.tensor([0,1]))
        b._restore_base_target=lambda:base.copy_(b._base_target['position'])
        def physics():pos.add_(10);base.add_(10)
        b.og=types.SimpleNamespace(sim=types.SimpleNamespace(step_physics=physics))
        with self.assertRaisesRegex(ValueError,'sampler failed'):
            with b._anchored_operation(obj):
                b.og.sim.step_physics()
                self.assertTrue(torch.equal(pos,torch.tensor([1.,2.,3.])))
                self.assertTrue(torch.equal(base,torch.zeros(3)))
                raise ValueError('sampler failed')
        self.assertIs(b.og.sim.step_physics,physics)
        self.assertIsNone(b._object_anchor);self.assertIsNone(b._base_target)

    def test_second_placement_preserves_existing_content_during_sampling_and_settling(self):
        import torch
        class Inside:pass
        resident=types.SimpleNamespace(position=0.)
        resident.states={Inside:types.SimpleNamespace(get_value=lambda target:resident.position==0.)}
        link=types.SimpleNamespace(is_meta_link=True,meta_link_type='fillable')
        target=types.SimpleNamespace(links={'volume':link})
        held=types.SimpleNamespace(position=0.)
        held_state=types.SimpleNamespace(get_value=lambda target:held.position==0.)
        held.states={Inside:held_state}
        held.get_position_orientation=lambda:(torch.zeros(3),torch.tensor([0.,0.,0.,1.]))
        holder={'object':held}
        sampled={'done':False}
        def physics():
            resident.position=5.
            if sampled['done']:held.position=5.
        sim=types.SimpleNamespace(step_physics=physics)
        def sample(target,wanted):
            usd_utils.RigidContactAPI.is_in_contact(0,[held],None,None,True)
            sim.step_physics();sampled['done']=True;held.position=0.
            return True
        held_state.set_value=sample
        b=CheckedPlacement();b.og=types.SimpleNamespace(sim=sim);b.deadline=None
        b.sampling_physics_steps=0;b.frames_revision=0;b.ideal_carry=True
        b._carry_contents=[];b._carry_dependencies=[]
        b.robot=types.SimpleNamespace(get_joint_positions=lambda:torch.zeros(1),
                                       q_to_action=lambda q:q)
        b._get_held=lambda:holder['object']
        b._carry_detach=lambda:holder.update(object=None)
        b._container_payload=lambda container:([(resident,None,None),(held,None,None)] if sampled['done']
                                               else [(resident,None,None)])
        b._relocate_container_payload=lambda payload:[setattr(obj,'position',0.) for obj,_,_ in payload]
        b._placement_context=lambda container:nullcontext()
        b._relocate_contents=lambda held,contents:None
        # Environment.step advances physics without calling the patched
        # sim.step_physics method used by the official volume sampler.
        b._step=lambda action:physics()
        b._verify_container_payload=lambda container,residents:(
            None if resident.states[Inside].get_value(container) else
            (_ for _ in ()).throw(SkillError('postcondition_error','Existing item escaped')))
        b._verify_payload=lambda dependencies:None
        object_states=types.ModuleType('omnigibson.object_states');object_states.Inside=Inside
        usd_utils=types.ModuleType('omnigibson.utils.usd_utils')
        ignored=[]
        def contact(scene_idx,query_set,with_set,ignore_set,current_only):
            ignored.append(ignore_set)
            return False
        usd_utils.RigidContactAPI=types.SimpleNamespace(is_in_contact=contact)
        with patch.dict('sys.modules',{'omnigibson.object_states':object_states,
                                       'omnigibson.utils.usd_utils':usd_utils}):
            result=b._checked_place_inside(target,3)
        self.assertEqual(result['existing_containment_verified'],1)
        self.assertEqual(resident.position,0.)
        self.assertEqual(held.position,0.)
        self.assertIs(sim.step_physics,physics)
        self.assertTrue(any(b.robot in entry for entry in ignored if entry is not None))


class NestedStabilizationTests(unittest.TestCase):
    def test_grasp_and_release_nested_container_does_not_restore_old_contents(self):
        import numpy as np
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        class Vector(np.ndarray):
            def __new__(cls, values):return np.asarray(values,dtype=float).view(cls)
            def clone(self):return self.copy()
        class Inside:pass
        class OnTop:pass
        class Touching:pass
        class Body:
            fixed_base=False
            def __init__(self,name,position,extent):
                self.name,self.position,self.extent=name,Vector(position),extent
                self.states={Inside:types.SimpleNamespace(get_value=self.inside)}
            @property
            def aabb(self):return self.position-self.extent,self.position+self.extent
            def inside(self,parent):
                lo,hi=self.aabb;other_lo,other_hi=parent.aabb
                return bool(((lo>=other_lo)&(hi<=other_hi)).all())
            def get_position_orientation(self):return self.position.clone(),Vector([0,0,0,1])
            def set_position_orientation(self,position,orientation):self.position=Vector(position)
            def keep_still(self):pass
        outer=Body('outer B',[0,0,0],2.)
        inner=Body('inner A',[0,0,0],.4)
        child=Body('nested C',[.1,0,0],.05)
        unrelated=Body('other B content',[1.5,0,0],.05)
        robot=Body('robot',[-4,0,0],.1)
        robot.get_joint_positions=lambda:Vector([0])
        robot.q_to_action=lambda q:q
        transforms=types.SimpleNamespace(
            relative_pose_transform=lambda p,q,base,bq:(p-base,q),
            pose_transform=lambda base,bq,p,q:(base+p,q))
        b=RGBBackend.__new__(RGBBackend)
        b.robot,b.torch,b.ideal_carry=robot,types.SimpleNamespace(isfinite=np.isfinite),True
        b._ideal_held=None;b._carry_contents=[];b._carry_dependencies=[]
        b._carry_record=lambda **kwargs:None
        b._anchored_operation=lambda:nullcontext()
        b.env=types.SimpleNamespace(scene=types.SimpleNamespace(objects=[outer,inner,child,unrelated,robot]))
        b._stabilized_containers={outer:outer.get_position_orientation(),inner:inner.get_position_orientation()}
        b._stabilized_container_payloads={
            outer:[(obj,outer,transforms.relative_pose_transform(
                *obj.get_position_orientation(),*outer.get_position_orientation())) for obj in (inner,child,unrelated)],
            inner:[(child,inner,transforms.relative_pose_transform(
                *child.get_position_orientation(),*inner.get_position_orientation()))]}
        b._step=lambda action:(b._restore_stabilized_containers(),b._carry_follow())
        states=types.ModuleType('omnigibson.object_states')
        states.Inside,states.OnTop,states.Touching=Inside,OnTop,Touching
        utils=types.ModuleType('omnigibson.utils');utils.transform_utils=transforms
        with patch.dict('sys.modules',{'omnigibson.object_states':states,'omnigibson.utils':utils}):
            b._ideal_grasp(inner,1)
            self.assertEqual([obj for obj,_ in b._carry_contents],[child])
            self.assertNotIn(inner,b._stabilized_containers)
            self.assertNotIn(inner,b._stabilized_container_payloads)
            self.assertEqual([record[0] for record in b._stabilized_container_payloads[outer]],[unrelated])
            # Place the carried assembly outside B, then exercise the real
            # later-step stabilization that formerly pulled C back into B.
            contents=list(b._carry_contents)
            b._carry_detach()
            inner.set_position_orientation(Vector([3,0,0]),Vector([0,0,0,1]))
            b._relocate_contents(inner,contents)
            b._restore_stabilized_containers()
        np.testing.assert_allclose(inner.position,[3,0,0])
        np.testing.assert_allclose(child.position,[3.1,0,0])
        np.testing.assert_allclose(outer.position,[0,0,0])
        np.testing.assert_allclose(unrelated.position,[1.5,0,0])


if __name__=='__main__':unittest.main()
