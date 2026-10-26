"""Container identity visibility must never admit an unrelated scene occluder."""
import importlib.util, unittest
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
from manipulation_agent.executors.omnigibson_rgb import RGBBackend

@unittest.skipUnless(importlib.util.find_spec('torch'),'requires torch')
class ContainerVisibilityTests(unittest.TestCase):
 def setUp(self):
  import torch
  import numpy
  self.torch=torch
  self.b=b=RGBBackend.__new__(RGBBackend);b.torch=torch;b.rig_height=1.5;b.rig_radius=.35;b.rig_pitch_degrees=35
  b.robot=SimpleNamespace(links={},get_position_orientation=lambda:(torch.tensor([0.,0.,0.]),None));b._get_held=lambda:None
  self.target=SimpleNamespace(links={'base':SimpleNamespace(prim_path='/container/base')})
  self.point=torch.tensor([1.,0.,1.]);self.sampling=ModuleType('omnigibson.utils.sampling_utils')
 def visible(self,body,*,container=False,self_blocked=False,in_frame=True):
  self.sampling.raytest=lambda *a,**kw:{'hit':True,'rigidBody':body,'position':self.torch.tensor([.5,0.,1.])}
  with patch.dict('sys.modules',{'omnigibson.utils.sampling_utils':self.sampling}),patch('manipulation_agent.observations.rig.visible_rig_rays',return_value=[('front',[0.,0.,1.],(.5,.5))] if in_frame else []),patch('manipulation_agent.executors.self_depth_filter.segment_hits_self_hull',return_value=self_blocked):
   return self.b._approach_visible((0.,0.),self.point,self.target,base_yaw=0.,selected_container=container)
 def test_container_surface_is_allowed_only_for_container_identity_action(self):
  self.assertFalse(self.visible('/container/base'))
  self.assertTrue(self.visible('/container/base',container=True))
 def test_wall_or_other_object_cannot_stand_in_for_selected_container(self):
  self.assertFalse(self.visible('/wall/base',container=True))
  self.assertFalse(self.visible('/other_container/base',container=True))
 def test_self_occlusion_and_frustum_checks_remain_required(self):
  self.assertFalse(self.visible('/container/base',container=True,self_blocked=True))
  self.assertFalse(self.visible('/container/base',container=True,in_frame=False))
