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








if __name__=='__main__':unittest.main()
