import copy,unittest
from types import SimpleNamespace as NS
from manipulation_agent.executors.custom_task import complete_native_articulation_state

class NativeConstructionStateTests(unittest.TestCase):
 def test_new_articulated_asset_gets_actual_defaults_without_overwriting_pose(self):
  state={'registry':{'object_registry':{'sprayer':{'root_link':{'pos':[2,3,4]},'non_kin':{}}}}};before=copy.deepcopy(state)
  obj=NS(n_joints=1,get_joint_positions=lambda:[.35],get_joint_velocities=lambda:[0.])
  actual,changes=complete_native_articulation_state(state,{'sprayer':obj},['sprayer'])
  self.assertEqual(actual['registry']['object_registry']['sprayer']['joint_pos'],[.35])
  self.assertEqual(actual['registry']['object_registry']['sprayer']['root_link'],before['registry']['object_registry']['sprayer']['root_link'])
  self.assertEqual(state,before);self.assertEqual(changes,[{'name':'sprayer','n_joints':1}])
 def test_saved_articulation_is_preserved_and_non_native_object_is_untouched(self):
  state={'registry':{'object_registry':{'native':{'joint_pos':[.8],'joint_vel':[.1]},'other':{}}}}
  obj=NS(n_joints=1,get_joint_positions=lambda:self.fail('Saved state must not be resampled'))
  actual,changes=complete_native_articulation_state(state,{'native':obj},['native'])
  self.assertEqual(actual,state);self.assertEqual(changes,[])
 def test_unarticulated_asset_needs_no_joint_state(self):
  state={'registry':{'object_registry':{'apple':{}}}}
  actual,changes=complete_native_articulation_state(state,{'apple':NS(n_joints=0)},['apple'])
  self.assertEqual(actual,state);self.assertEqual(changes,[])

 def test_construction_sampling_failure_restores_runtime_search_limits(self):
  from contextlib import nullcontext
  from manipulation_agent.executors.custom_task import construction_native_sampling_limit
  macros=NS(DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS=10,DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS=100,unlocked=nullcontext)
  with self.assertRaisesRegex(RuntimeError,'construction rejected'):
   with construction_native_sampling_limit(macros,30):
    self.assertEqual(macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS*macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS,30)
    raise RuntimeError('construction rejected')
  self.assertEqual((macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS,macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS),(10,100))
