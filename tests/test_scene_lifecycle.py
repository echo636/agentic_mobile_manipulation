"""A transition error must not expose uninitialized relation states to the next act."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class SceneLifecycleTests(unittest.TestCase):
    def fixture(self, folder):
        product=SimpleNamespace(name='transition_product',initialized=False)
        sim=SimpleNamespace(_objects_to_initialize=[product])
        b=SimpleNamespace(output=Path(folder),steps=0,frames_revision=0,
            og=SimpleNamespace(sim=sim),env=SimpleNamespace(scene=SimpleNamespace(objects=[product])),
            deadline=SimpleNamespace(check=lambda **kw:None),
            robot=SimpleNamespace(get_joint_positions=lambda:[],q_to_action=lambda q:q))
        def step(action):
            b.steps+=1;product.initialized=True;sim._objects_to_initialize=[]
        b._step=step
        def execute(backend,primitive,*args,**kwargs):
            if primitive=='place_on_top':raise SkillError('target_changed','replaced',changed=True)
            assert product.initialized
            return {'primitive':primitive}
        b._complete_pending_object_initialization=lambda remaining=8:RGBBackend._complete_pending_object_initialization(b,remaining)
        self.execute=execute
        return b,product

    def test_failed_transition_then_relation_read_without_crashing(self):
        with TemporaryDirectory() as folder:
            b,product=self.fixture(folder)
            with patch.object(RGBBackend,'_execute_visual',side_effect=self.execute):
                with self.assertRaises(SkillError) as caught:
                    RGBBackend.execute_visual(b,'place_on_top',None,10)
            self.assertEqual(caught.exception.code,'target_changed')
            self.assertTrue(product.initialized)
            self.assertEqual(b.steps,1)
            with patch.object(RGBBackend,'_execute_visual',side_effect=self.execute):
                self.assertEqual(RGBBackend.execute_visual(b,'grasp',None,10),{'primitive':'grasp'})
            self.assertEqual(b.steps,1)

    def test_external_cancellation_prevents_cleanup_physics(self):
        with TemporaryDirectory() as folder:
            b,product=self.fixture(folder)
            def cancelled(**kwargs):raise SkillError('episode_cancelled','cancelled')
            b.deadline.check=cancelled
            with patch.object(RGBBackend,'_execute_visual',side_effect=self.execute):
                with self.assertRaises(SkillError) as caught:
                    RGBBackend.execute_visual(b,'place_on_top',None,10)
            self.assertEqual(caught.exception.code,'episode_cancelled')
            self.assertFalse(product.initialized)
            self.assertEqual(b.steps,0)

    def test_exhausted_action_budget_does_not_run_extra_physics(self):
        with TemporaryDirectory() as folder:
            b,product=self.fixture(folder)
            with patch.object(RGBBackend,'_execute_visual',side_effect=self.execute):
                with self.assertRaises(SkillError) as caught:
                    RGBBackend.execute_visual(b,'place_on_top',None,0)
            self.assertEqual(caught.exception.code,'scene_initialization_pending')
            self.assertFalse(product.initialized)
            self.assertEqual(b.steps,0)
