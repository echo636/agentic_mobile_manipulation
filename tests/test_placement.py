import unittest
from types import SimpleNamespace
from manipulation_agent.executors.placement import placement_transaction, rotated_footprint_extents
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
