import copy,math,unittest
from types import SimpleNamespace as NS
from manipulation_agent.executors.custom_task import complete_native_articulation_state

class NativeConstructionStateTests(unittest.TestCase):
 def test_initial_opening_respects_native_direction_and_keeps_limit_margin(self):
  from manipulation_agent.executors.custom_task import initial_open_joint_targets
  forward=NS(lower_limit=0.,upper_limit=math.pi/2)
  reverse=NS(lower_limit=-1.,upper_limit=2.)
  state=NS(relevant_joints_info=(False,[forward,reverse],[1,-1]))
  targets=initial_open_joint_targets(state,85./90.)
  self.assertAlmostEqual(math.degrees(targets[0][1]),85.)
  self.assertAlmostEqual(targets[1][1],-5./6.)
  for joint,position in targets:self.assertTrue(joint.lower_limit<position<joint.upper_limit)
  for invalid in [True,0.,.05,1.,float('nan')]:
   with self.assertRaises(ValueError):initial_open_joint_targets(state,invalid)

 def test_initial_opening_cannot_guess_both_sided_or_unbounded_joints(self):
  from manipulation_agent.executors.custom_task import initial_open_joint_targets
  for info in [(True,[NS(lower_limit=0.,upper_limit=1.)],[1]),
               (False,[NS(lower_limit=0.,upper_limit=float('inf'))],[1])]:
   with self.assertRaises(ValueError):initial_open_joint_targets(NS(relevant_joints_info=info),.9)

 def test_post_settle_validation_rejects_limit_bounce_without_repairing_it(self):
  import json,sys
  from pathlib import Path
  from tempfile import TemporaryDirectory
  from types import ModuleType
  from unittest.mock import patch
  from manipulation_agent.executors.custom_task import CustomTaskBackend
  Open=type('Open',(),{});module=ModuleType('omnigibson');module.object_states=NS(Open=Open)
  joint=NS(name='hinge',lower_limit=0.,upper_limit=math.pi/2,get_state=lambda:(math.radians(97.),7.2,0.))
  obj=NS(name='door',states={Open:NS(relevant_joints_info=(False,[joint],[1]),get_value=lambda:True)})
  with TemporaryDirectory() as directory,patch.dict(sys.modules,{'omnigibson':module}):
   backend=NS(output=Path(directory),custom_spec={'initial_layout':[{'object':'door','initial_open_fraction':85./90.}]})
   with self.assertRaisesRegex(RuntimeError,'settle open within'):
    CustomTaskBackend._validate_initial_articulations(backend,{'door':obj})
   record=json.loads((Path(directory)/'construction_articulation_validation.json').read_text())
   self.assertEqual(record['status'],'failed')
   self.assertAlmostEqual(math.degrees(record['joints'][0]['position']),97.)
   joint.get_state=lambda:(math.radians(85.),0.,0.)
   CustomTaskBackend._validate_initial_articulations(backend,{'door':obj})
   self.assertEqual(json.loads((Path(directory)/'construction_articulation_validation.json').read_text())['status'],'passed')

 def test_contact_refresh_wakes_goal_supports_and_steps_whole_scene(self):
  from contextlib import nullcontext
  from pathlib import Path
  from tempfile import TemporaryDirectory
  from manipulation_agent.executors.custom_task import CustomTaskBackend
  events=[]
  def obj(name):
   return NS(name=name,wake=lambda:events.append(('wake',name)),
             states={'contact':NS(clear_cache=lambda:events.append(('clear',name)))})
  objects=[obj(n) for n in ['item','support','background','robot']]
  with TemporaryDirectory() as directory:
   backend=NS(custom_spec={'roles':{'load':'item','destination':'support'}},robot=objects[-1],
              env=NS(scene=NS(objects=objects)),output=Path(directory),
              og=NS(sim=NS(step=lambda:events.append(('step','whole_scene')))),
              _startup_stage=lambda stage:nullcontext())
   CustomTaskBackend._refresh_custom_contacts(backend,'test')
  self.assertEqual([name for kind,name in events if kind=='wake'],['item','support','robot'])
  self.assertEqual(events.count(('step','whole_scene')),5)
  self.assertEqual([name for kind,name in events if kind=='clear'],['item','support','background','robot'])

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

 def test_import_time_attachment_is_not_retained_when_state_was_omitted(self):
  AttachedTo=type('AttachedTo',(),{})
  state={'registry':{'object_registry':{'bracket':{'non_kin':{}}}}}
  obj=NS(n_joints=0,states={AttachedTo:object()})
  actual,changes=complete_native_articulation_state(state,{'bracket':obj},['bracket'])
  self.assertEqual(actual['registry']['object_registry']['bracket']['non_kin']['AttachedTo'],{'attached_obj_uuid':-1})
  self.assertEqual(state['registry']['object_registry']['bracket']['non_kin'],{})
  state['registry']['object_registry']['bracket']['non_kin']['AttachedTo']={'attached_obj_uuid':123}
  actual,_=complete_native_articulation_state(state,{'bracket':obj},['bracket'])
  self.assertEqual(actual,state)

 def test_construction_sampling_failure_restores_runtime_search_limits(self):
  from contextlib import nullcontext
  from manipulation_agent.executors.custom_task import construction_native_sampling_limit
  macros=NS(DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS=10,DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS=100,unlocked=nullcontext)
  with self.assertRaisesRegex(RuntimeError,'construction rejected'):
   with construction_native_sampling_limit(macros,30):
    self.assertEqual(macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS*macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS,30)
    raise RuntimeError('construction rejected')
  self.assertEqual((macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS,macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS),(10,100))
