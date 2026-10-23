"""An automatic slice during failed placement must leave usable replacements."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class TransitionReadinessTests(unittest.TestCase):
    def test_changed_target_keeps_original_error_after_native_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            halves=[NS(name='half_'+str(i),initialized=False) for i in range(2)]
            sim=NS(_objects_to_initialize=[])
            backend=NS(og=NS(sim=sim),steps=17,output=Path(directory),
                       deadline=NS(check=lambda **kw:None),
                       robot=NS(get_joint_positions=lambda:[],q_to_action=lambda q:'hold'))
            backend._complete_pending_object_initialization=lambda remaining=8:RGBBackend._complete_pending_object_initialization(backend,remaining)
            unwound=[]
            def placement(*args,**kwargs):
                try:
                    sim._objects_to_initialize.extend(halves)
                    raise SkillError('target_changed','original replaced',changed=True)
                finally:unwound.append(True)
            def native_step(action):
                self.assertTrue(unwound,'Initialization ran inside the failed placement transaction')
                self.assertEqual(action,'hold')
                for obj in sim._objects_to_initialize:obj.initialized=True
                sim._objects_to_initialize=[]
                backend.steps+=1
            backend._step=native_step
            with patch.object(RGBBackend,'_execute_visual',side_effect=placement):
                with self.assertRaises(SkillError) as error:
                    RGBBackend.execute_visual.__wrapped__(backend,'place_on_top',object(),500)
            self.assertEqual(error.exception.code,'target_changed')
            self.assertTrue(error.exception.changed)
            self.assertTrue(all(obj.initialized for obj in halves))
            self.assertEqual(backend.steps,18)
            record=json.loads((backend.output/'transition_initialization.jsonl').read_text())
            self.assertEqual(record['end_step']-record['start_step'],1)
            self.assertEqual(len(record['objects']),2)
            sim._objects_to_initialize=halves
            backend._step=lambda action:self.fail('Unexpected native failure must not start recovery physics')
            with patch.object(RGBBackend,'_execute_visual',side_effect=RuntimeError('native failure')):
                with self.assertRaisesRegex(RuntimeError,'native failure'):
                    RGBBackend.execute_visual.__wrapped__(backend,'place_on_top',object(),500)

    def test_ready_scene_does_not_advance_and_stuck_initialization_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            sim=NS(_objects_to_initialize=[])
            backend=NS(og=NS(sim=sim),steps=0,output=Path(directory),
                       deadline=NS(check=lambda **kw:None),
                       robot=NS(get_joint_positions=lambda:[],q_to_action=lambda q:None))
            backend._step=lambda action:self.fail('Ready scene advanced physics')
            RGBBackend._complete_pending_object_initialization(backend)
            sim._objects_to_initialize=[NS(name='pending',initialized=False)]
            def native_step(action):backend.steps+=1
            backend._step=native_step
            with self.assertRaises(SkillError) as error:
                RGBBackend._complete_pending_object_initialization(backend)
            self.assertEqual(error.exception.code,'scene_initialization_pending')
            self.assertEqual(backend.steps,8)
            self.assertFalse(sim._objects_to_initialize[0].initialized)


if __name__=='__main__':unittest.main()
