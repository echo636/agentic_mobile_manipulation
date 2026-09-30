import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.vision_policy import system_prompt


class MinimalLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'run'
        self.recorder=Recorder(self.root,{'backend':'mock','observation_mode':'rgb_only'})
        self.h=VisionHarness(MockRGBBackend(self.root),self.recorder,profile='minimal')

    def test_default_has_only_four_tools_and_no_plan_memory_or_skills(self):
        self.assertEqual({t['name'] for t in self.h.tool_specs()},{'observe','look','act','finish'})
        self.assertFalse(hasattr(self.h,'plan'));self.assertFalse(hasattr(self.h,'memory'))
        self.assertIsNone(self.h.skills);self.assertFalse((self.root/'skill_manifest.json').exists())
        for tool,args in [('remember',{'key':'x','text':'oracle','revision':0}),('read_skill',{'name':'x','resource':'SKILL.md'}),('update_plan',{})]:
            r=self.h.call(tool,args,tool)
            self.assertEqual(r['error']['code'],'unknown_tool')

    def test_direct_rgb_action_finish_needs_no_plan_or_memory(self):
        obs=self.h.call('observe',{},'observe')['observation']
        result=self.h.call('act',{'primitive':'toggle_on','revision':obs['revision'],
            'target':{'image_ref':obs['images'][0]['image_ref'],'point':[.5,.5]}},'action')
        self.assertTrue(result['ok']);self.assertEqual(result['observation']['revision'],1)
        done=self.h.call('finish',{'outcome':'achieved','reason':'RGB indicator changed'},'done')
        self.assertTrue(done['closed']);self.assertNotIn('evaluation',done)
        self.assertTrue(self.recorder.run['task_success'])
        self.assertEqual(self.recorder.run['config']['agent_profile'],'minimal')
