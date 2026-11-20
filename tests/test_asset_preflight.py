import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.asset_preflight import inspect_scene_assets, inspect_particle_system_assets


class AssetPreflightTests(unittest.TestCase):
    def test_scene_preflight_checks_metadata_child_models_not_only_initial_objects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template, instance = root/'template.json', root/'instance.json'
            template.write_text(json.dumps({'metadata':{'task':{'inst_to_name':{'onion.n.01_1':'onion'}}},
                'objects_info':{'init_info':{'onion':{'class_name':'DatasetObject',
                    'args':{'category':'vidalia_onion','model':'whole'}}}}}))
            instance.write_text(json.dumps({'onion.n.01_1':{}}))
            (root/'scenes/kitchen/layout').mkdir(parents=True)
            def asset(category, model, parts):
                base = root/'objects'/category/model
                (base/'usd').mkdir(parents=True)
                (base/'usd'/f'{model}.encrypted.usd').write_bytes(b'fixture')
                (base/'misc').mkdir()
                (base/'misc/metadata.json').write_text(json.dumps({'object_parts':parts}))
            asset('vidalia_onion','whole',[{'category':'half_vidalia_onion','model':'actual_part'}])
            result = inspect_scene_assets(template, instance, root, 'kitchen')
            child = 'objects/half_vidalia_onion/actual_part/usd/actual_part.encrypted.usd'
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['missing_model_usds'], [child])
            self.assertEqual(result['dynamic_model_usds'], [child])
            # Numeric dictionaries in metadata and cycles are handled too.
            asset('half_vidalia_onion','actual_part',{'0':{'category':'vidalia_onion','model':'whole'}})
            result = inspect_scene_assets(template, instance, root, 'kitchen')
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['model_usds_checked'], 2)

    def test_dicing_requires_selected_particle_asset_but_allows_procedural_system(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); system = root/'systems/diced__onion'
            system.mkdir(parents=True)
            (system/'metadata.json').write_text(json.dumps({'type':'granular'}))
            self.assertEqual(inspect_particle_system_assets('diced__onion',root)['missing_assets'], [])
            (system/'chosen/usd').mkdir(parents=True)
            self.assertEqual(inspect_particle_system_assets('diced__onion',root)['missing_assets'],
                ['systems/diced__onion/chosen/usd/chosen.encrypted.usd'])
            (system/'chosen/usd/chosen.encrypted.usd').write_bytes(b'fixture')
            self.assertEqual(inspect_particle_system_assets('diced__onion',root)['missing_assets'], [])

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
