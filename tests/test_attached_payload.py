"""Attachment descendants must leave, travel and rest as one physical assembly."""
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
import torch
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.placement import CheckedPlacement
from manipulation_agent.contracts import SkillError

class Attached: pass
class Inside: pass
class OnTop: pass
class Touching: pass

class AttachedPayloadTests(unittest.TestCase):
 def setUp(self):
  self.states=types.ModuleType('omnigibson.object_states')
  for n,v in [('AttachedTo',Attached),('Inside',Inside),('OnTop',OnTop),('Touching',Touching)]:setattr(self.states,n,v)
  self.mock=patch.dict(sys.modules,{'omnigibson':types.ModuleType('omnigibson'),'omnigibson.object_states':self.states});self.mock.start();self.addCleanup(self.mock.stop)
  def obj(name,z):return NS(name=name,fixed_base=False,states={},aabb=(torch.tensor([0.,0.,z]),torch.tensor([1.,1.,z+.05])),points=np.array([[0.,0.,z],[1.,1.,z+.05]]))
  self.top=obj('top',.7);self.bracket=obj('bracket',.65);self.leg=obj('leg',0.);self.unrelated=obj('other',.2)
  self.bracket.states[Attached]=NS(get_value=lambda p:p is self.top)
  self.leg.states[Attached]=NS(get_value=lambda p:p is self.bracket)
  scene=NS(objects=[self.top,self.bracket,self.leg,self.unrelated])
  for o in scene.objects:o.scene=scene
  self.backend=NS(env=NS(scene=scene),robot=NS())

 def test_grasp_collects_attachment_grandchildren(self):
  edges=ControlledCarry._grasp_dependencies(self.backend,self.top)
  self.assertEqual([(a.name,b.name,k) for a,b,k in edges],[('bracket','top','AttachedTo'),('leg','bracket','AttachedTo')])

 def test_surface_anchor_uses_feet_and_excludes_unattached_objects(self):
  class Adapter(CheckedPlacement):
   def _object_collision_vertices(self,obj):return obj.points
  adapter=Adapter();bodies=adapter._attached_placement_bodies(self.top)
  self.assertEqual([o.name for o in bodies],['top','bracket','leg'])
  points=adapter._collision_vertices(self.top)
  self.assertEqual(float(points[:,2].min()),0.)
  self.assertEqual(len(points),6)

 def test_lost_attachment_fails_payload_verification(self):
  edges=ControlledCarry._grasp_dependencies(self.backend,self.top)
  ControlledCarry._verify_payload(self.backend,edges)
  self.leg.states[Attached]=NS(get_value=lambda parent:False)
  with self.assertRaisesRegex(SkillError,'attachment'):ControlledCarry._verify_payload(self.backend,edges)

if __name__=='__main__':unittest.main()
