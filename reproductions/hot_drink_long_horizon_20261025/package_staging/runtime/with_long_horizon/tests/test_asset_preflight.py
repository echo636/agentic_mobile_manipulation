import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.asset_preflight import inspect_scene_assets


class AssetPreflightTests(unittest.TestCase):
    def test_task_json_presence_does_not_mask_missing_models_or_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);template=root/'template.json';instance=root/'instance.json'
            template.write_text(json.dumps({'metadata':{'task':{'inst_to_name':{'cup.n.01_1':'cup'}}},
                'objects_info':{'init_info':{'cup':{'class_name':'DatasetObject','args':{'category':'cup','model':'abcdef'}}}}}))
            instance.write_text(json.dumps({'cup.n.01_1':{}}))
            result=inspect_scene_assets(template,instance,root,'kitchen')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['missing_model_usds'],['objects/cup/abcdef/usd/abcdef.encrypted.usd'])
            usd=root/result['missing_model_usds'][0];usd.parent.mkdir(parents=True);usd.write_bytes(b'fixture')
            self.assertEqual(inspect_scene_assets(template,instance,root,'kitchen')['status'],'failed')
            (root/'scenes/kitchen/layout').mkdir(parents=True)
            result=inspect_scene_assets(template,instance,root,'kitchen')
            self.assertEqual(result['status'],'passed')
            self.assertFalse(result['full_simulator_load_verified'])
