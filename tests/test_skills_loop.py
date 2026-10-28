from pathlib import Path
import tempfile
import unittest
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness

class SkillsLoopTests(unittest.TestCase):
    def test_skills_without_forced_summary_plan_or_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run';r=Recorder(root,{'backend':'mock','observation_mode':'rgb_only'})
            h=VisionHarness(MockRGBBackend(root),r)
            self.assertEqual({t['name'] for t in h.tool_specs()},{'initialize','look','act','finish','list_skills','read_skill','task_notes'})
            self.assertFalse(hasattr(h,'plan'));self.assertFalse(hasattr(h,'memory'))
            self.assertTrue(h.call('list_skills',{},'ls')['skills'])
            self.assertTrue(h.call('read_skill',{'name':'pick-and-place','resource':'SKILL.md'},'read')['text'])
            for name in ['update_plan','remember','recall','observe','start_observation','get_observation','cancel_observation']:
                self.assertEqual(h.call(name,{},name)['error']['code'],'unknown_tool')
            obs=h.call('initialize',{},'obs')['observation']
            args={'primitive':'toggle_on','revision':0,'target':{'image_ref':obs['images'][0]['image_ref'],'point':[.5,.5]}}
            self.assertTrue(h.call('act',args,'valid')['ok'])
            self.assertTrue(h.call('finish',{'outcome':'achieved','reason':'验证灯光'},'done')['closed'])
            from manipulation_agent.replay import build_replay
            steps=build_replay(root)['steps']
            self.assertIsNone(next(s for s in steps if s['tool']=='act')['decision'])
