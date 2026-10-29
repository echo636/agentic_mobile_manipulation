import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.executors.base_motion import trajectory
from manipulation_agent.skill_runtime import SkillLibrary
from manipulation_agent.vision_policy import system_prompt

class SkillsLoopTests(unittest.TestCase):
    def test_unified_planning_skill_is_the_only_decomposition_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'MAS_LONG_HORIZON_PLANNING_SKILL': '1'}):
                library = SkillLibrary(Path(tmp))
                names = {entry['name'] for entry in library.catalog()}
                self.assertNotIn('task-decomposition', names)
                self.assertIn('long-horizon-planning', names)
                self.assertIn('pick-and-place', names)
                self.assertNotIn('task-decomposition/SKILL.md', system_prompt('skills'))
                bddl = library.read('long-horizon-planning','references/bddl-goals.md')
                self.assertIn('forpairs', bddl['text'])

    def test_skills_without_forced_summary_plan_or_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run';r=Recorder(root,{'backend':'mock','observation_mode':'rgb_only'})
            h=VisionHarness(MockRGBBackend(root),r)
            self.assertEqual({t['name'] for t in h.tool_specs()},{'initialize','look','act','finish','list_skills','read_skill'})
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

    def test_kinematic_path_preserves_corners_and_speed_bounds(self):
        path=trajectory([(0,0),(1,0),(1,1)],math.radians(170),math.radians(-170),1/30)
        previous=(0,0,math.radians(170))
        for x,y,yaw in path:
            self.assertLessEqual(math.dist(previous[:2],(x,y)),.5/30+1e-8)
            self.assertLessEqual(abs(yaw-previous[2]),math.pi/3/30+1e-8)
            self.assertTrue(abs(y)<1e-8 or abs(x-1)<1e-8)
            previous=(x,y,yaw)
        self.assertEqual(path[-1][:2],(1,1))
        self.assertAlmostEqual(path[-1][2],math.radians(190))
        turn=trajectory([(1,1)],0,math.pi/2,1/30)
        self.assertGreaterEqual(len(turn),45)
        self.assertTrue(all(p[:2]==(1,1) for p in turn))
