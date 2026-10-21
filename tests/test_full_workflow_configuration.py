"""Complete custom scenes must retain furnishings outside the base task's room."""
import json,os,tempfile,unittest
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
from manipulation_agent.omnigibson_backend import OmniGibsonBackend

class FullWorkflowConfigurationTests(unittest.TestCase):
 def config(self,full,gpu_physics=False):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);data=root/'2026-challenge-task-instances';meta=data/'metadata';meta.mkdir(parents=True)
   task={'scene_model':'house','robot_start_position':[0,0,0],'robot_start_orientation':[0,0,0,1]}
   (meta/'available_tasks.yaml').write_text(json.dumps({'fixture':[task]}))
   base=data/'scene_test/public/house/json';base.mkdir(parents=True)
   inst=base/'house_task_fixture_instances';inst.mkdir();(inst/'house_task_fixture_0_301_template-tro_state.json').write_text(json.dumps({'cabinet.n.01_1':{}}))
   objects={'cabinet':{'class_name':'DatasetObject','args':{'category':'cabinet','in_rooms':['kitchen_0']}}}
   scene={'metadata':{'task':{'inst_to_name':{'cabinet.n.01_1':'cabinet'}}},'objects_info':{'init_info':objects},'state':{'registry':{'object_registry':{}}}}
   (base/'house_task_fixture_0_0_template-partial_rooms.json').write_text(json.dumps(scene))
   objects['dining_table']={'class_name':'DatasetObject','args':{'category':'breakfast_table','in_rooms':['dining_room_0']}}
   (base/'house_task_fixture_0_0_template.json').write_text(json.dumps(scene))
   package=root/'package';(package/'eval').mkdir(parents=True)
   (package/'eval/r1pro.yaml').write_text(json.dumps({'sensor_config':{'VisionSensor':{'sensor_kwargs':{}}},'controller_config':{}}))
   module=ModuleType('omnigibson.eval.utils.eval_utils')
   module.generate_basic_environment_config=lambda *a:{'env':{},'scene':{'load_room_instances':['kitchen_0']},'task':{'termination_config':{}}}
   b=OmniGibsonBackend.__new__(OmniGibsonBackend);b.og=SimpleNamespace(__file__=str(package/'__init__.py'));b.output=root;b.instance=301;b.deadline=SimpleNamespace(managed=False);b.use_gpu_dynamics=gpu_physics
   if full:b.custom_spec={'reference_plan':{'kind':'full_workflow'}}
   with patch.dict('sys.modules',{'omnigibson.eval.utils.eval_utils':module}),patch.dict(os.environ,{'OMNIGIBSON_DATA_PATH':str(root),'OMNIGIBSON_APPDATA_PATH':str(root/'cache')}):
    config=b._config('fixture',4,1000)
   return config,json.loads(Path(config['scene']['scene_file']).read_text())

 def test_full_multiroom_contract_keeps_native_dining_furniture(self):
  config,scene=self.config(True,True)
  self.assertIn('dining_table',scene['objects_info']['init_info'])
  self.assertIsNone(config['scene']['load_room_instances'])
  self.assertEqual(config['env']['device'],'cpu')

 def test_atomic_task_keeps_existing_room_scope_and_tensor_device(self):
  config,scene=self.config(False)
  self.assertNotIn('dining_table',scene['objects_info']['init_info'])
  self.assertEqual(config['scene']['load_room_instances'],['kitchen_0'])
  self.assertEqual(config['env']['device'],'cuda:4')
