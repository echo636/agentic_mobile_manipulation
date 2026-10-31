import unittest
from types import SimpleNamespace
from manipulation_agent.executors.placement import placement_transaction, rotated_footprint_extents, project_before_post_step
import torch


class PlacementFootprint(unittest.TestCase):
 def test_quarter_turn_swaps_long_and_short_sides(self):
  original=torch.tensor([.4,.2,.1])
  rotated=rotated_footprint_extents(original,90)
  self.assertTrue(torch.allclose(rotated,torch.tensor([.2,.4,.1])))
  self.assertTrue(torch.equal(original,torch.tensor([.4,.2,.1])))

 def test_diagonal_turn_uses_projected_axes(self):
  rotated=rotated_footprint_extents(torch.tensor([.4,.2,.1]),45)
  self.assertTrue(torch.allclose(rotated,torch.tensor([.42426407,.42426407,.1])))

class PlacementTransactions(unittest.TestCase):
 def test_later_control_step_projects_committed_residents_before_metrics(self):
  import os
  from unittest.mock import patch
  from manipulation_agent.executors.omnigibson_rgb import RGBBackend
  from manipulation_agent.omnigibson_backend import OmniGibsonBackend
  world={'resident':1.0};reads=[]
  def metric(action):
   self.assertTrue(torch.isfinite(torch.tensor(world['resident'])))
   reads.append(action)
  backend=RGBBackend.__new__(RGBBackend)
  backend.deadline=SimpleNamespace(check=lambda **kw:None)
  backend.env=SimpleNamespace(_post_step=metric)
  backend._base_target=None
  backend._restore_object_anchor=lambda:None
  backend._restore_stabilized_containers=lambda:world.update(resident=1.0)
  backend._carry_follow=lambda:None
  backend._stabilize_high_level_base_height=lambda:None
  backend._video_frame=lambda *args:None
  def physics_then_metric(self,action):
   world['resident']=float('nan')
   self.env._post_step(action)
  with patch.dict(os.environ,{},clear=True),patch.object(OmniGibsonBackend,'_step',physics_then_metric):
   backend._step('later_grasp')
  self.assertEqual(reads,['later_grasp'])
  self.assertIs(backend.env._post_step,metric)

 def test_ideal_residents_are_restored_before_independent_metric_reads(self):
  state={'pose':float('nan')};seen=[]
  def evaluate(action):
   self.assertTrue(torch.isfinite(torch.tensor(state['pose'])))
   seen.append(action)
   return 'native_result'
  env=SimpleNamespace(_post_step=evaluate)
  original=env._post_step
  with self.assertRaises(ValueError):
   with project_before_post_step(env,lambda:state.update(pose=1.0)):
    self.assertEqual(env._post_step('selected_action'),'native_result')
    raise ValueError('later action failure')
  self.assertEqual(seen,['selected_action'])
  self.assertIs(env._post_step,original)

 def test_failed_sampling_restores_world_and_grasp(self):
  world={'held':'shoe','position':[1,2,3]};saved=[];records=[]
  def dump(**kw):return dict(world)
  def load(state,**kw):world.clear();world.update(state);saved.append(state)
  sim=SimpleNamespace(dump_state=dump,load_state=load)
  with self.assertRaises(ValueError),placement_transaction(sim,records.append):
   world.update(held=None,position=[7,8,9]);raise ValueError('sampling rejected')
  self.assertEqual(world,{'held':'shoe','position':[1,2,3]});self.assertEqual(len(saved),1)
  self.assertEqual(records[0]['status'],'rolled_back')
 def test_successful_placement_is_not_rewound(self):
  saved=[];records=[];sim=SimpleNamespace(dump_state=lambda **kw:{},load_state=lambda *a,**kw:saved.append(a))
  with placement_transaction(sim,records.append):pass
  self.assertFalse(saved);self.assertEqual(records,[{'status':'committed'}])
