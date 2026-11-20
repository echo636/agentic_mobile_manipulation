import json
from pathlib import Path
import tempfile
import time
import unittest

from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.contracts import SkillError


class MinimalLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'run'
        self.recorder=Recorder(self.root,{'backend':'mock','observation_mode':'rgb_only'})
        self.h=VisionHarness(MockRGBBackend(self.root),self.recorder,profile='minimal')

    def test_default_has_only_four_tools_and_no_plan_memory_or_skills(self):
        self.assertEqual({t['name'] for t in self.h.tool_specs()},{'initialize','look','act','finish'})
        self.assertFalse(hasattr(self.h,'plan'));self.assertFalse(hasattr(self.h,'memory'))
        self.assertIsNone(self.h.skills);self.assertFalse((self.root/'skill_manifest.json').exists())
        for tool,args in [('remember',{'key':'x','text':'oracle','revision':0}),('read_skill',{'name':'x','resource':'SKILL.md'}),('update_plan',{})]:
            r=self.h.call(tool,args,tool)
            self.assertEqual(r['error']['code'],'unknown_tool')

    def test_direct_rgb_action_finish_needs_no_plan_or_memory(self):
        obs=self.h.call('initialize',{},'initialize')['observation']
        result=self.h.call('act',{'primitive':'toggle_on','revision':obs['revision'],
            'target':{'image_ref':obs['images'][0]['image_ref'],'point':[.5,.5]}},'action')
        self.assertTrue(result['ok']);self.assertEqual(result['observation']['revision'],1)
        done=self.h.call('finish',{'outcome':'achieved','reason':'RGB indicator changed'},'done')
        self.assertTrue(done['closed']);self.assertNotIn('evaluation',done)
        self.assertTrue(self.recorder.run['task_success'])
        self.assertEqual(self.recorder.run['config']['agent_profile'],'minimal')

    def test_initialize_delivers_startup_capture_without_render_or_reset(self):
        backend = self.h.backend
        self.assertEqual(backend.capture, 1)
        original = self.h.refresh  # initialize must not request any fresh render
        def no_refresh(): raise AssertionError('initialize rendered again')
        self.h.refresh = no_refresh
        result = self.h.call('initialize', {}, 'initial')
        repeated = self.h.call('initialize', {}, 'initial-again')
        self.h.refresh = original
        self.assertTrue(result['ok']); self.assertTrue(result['initialized'])
        self.assertEqual(result['observation'], repeated['observation'])
        self.assertEqual(result['observation']['revision'], 0)
        self.assertEqual({i['view'] for i in result['observation']['images']}, {'front','back','left','right'})
        self.assertEqual((backend.capture, backend.steps, self.h.actions), (1, 0, 0))
        # The caller cannot mutate the current snapshot or cached initial reply.
        result['observation']['images'][0]['image_ref'] = 'tampered'
        result['observation']['capture']['sim_step'] = 123
        self.assertNotEqual(self.h.snapshot['images'][0]['image_ref'], 'tampered')
        self.assertEqual(self.h.snapshot['capture']['sim_step'], 0)
        cached = self.h.call('initialize', {}, 'initial')
        self.assertEqual(cached['observation'], repeated['observation'])

    def test_actions_return_next_capture_and_initialize_can_read_current_snapshot(self):
        initial = self.h.call('initialize', {}, 'initial')['observation']
        looked = self.h.call('look', {'yaw_degrees':45, 'revision':initial['revision']}, 'look')
        self.assertTrue(looked['ok'])
        next_obs = looked['observation']
        self.assertEqual((next_obs['revision'], self.h.backend.capture), (1, 2))
        self.assertNotEqual(initial['capture']['capture_id'], next_obs['capture']['capture_id'])
        result = self.h.call('act', {'primitive':'toggle_on', 'revision':next_obs['revision'],
              'target':{'image_ref':next_obs['images'][0]['image_ref'], 'point':[.5,.5]}}, 'act')
        self.assertTrue(result['ok'])
        self.assertEqual((result['observation']['revision'], self.h.backend.capture), (2, 3))
        current = self.h.call('initialize', {}, 'current')
        self.assertEqual(current['observation'], result['observation'])
        self.assertEqual(self.h.backend.capture, 3)
        self.assertEqual(len(self.h.surround.jobs), 0)

    def test_failed_action_returns_images_of_its_partial_world_change(self):
        before = self.h.call('initialize', {}, 'initial')['observation']
        backend = self.h.backend
        def partial_failure(*args, **kwargs):
            backend.steps += 1; backend.on = True
            raise SkillError('execution_error', 'PRIVATE_OBJECT_NAME changed', changed=True)
        backend.execute_visual = partial_failure
        result = self.h.call('act', {'primitive':'toggle_on', 'revision':0,
                    'target':{'image_ref':before['images'][0]['image_ref'], 'point':[.5,.5]}}, 'fail')
        self.assertFalse(result['ok']); self.assertTrue(result['error']['world_may_have_changed'])
        after = result['observation']
        self.assertEqual((after['revision'], after['capture']['sim_step'], backend.capture), (1, 1, 2))
        self.assertEqual(len(after['images']), 4)
        self.assertNotEqual(before['images'][0]['sha256'], after['images'][0]['sha256'])
        self.assertNotIn('PRIVATE_OBJECT_NAME', json.dumps(result))
        self.assertNotIn('evaluation', result)

    def test_expired_action_invalidates_revision_without_spending_more_time_rendering(self):
        before = self.h.call('initialize', {}, 'initial')['observation']
        def expire(*args, **kwargs):
            self.h.backend.steps += 1
            self.h.deadline._unix = time.time() - 1
            raise SkillError('episode_timeout', 'expired during execution', changed=True)
        self.h.backend.execute_visual = expire
        result = self.h.call('act', {'primitive':'wait', 'revision':0, 'target':None}, 'expire')
        self.assertEqual(result['error']['code'], 'episode_timeout')
        self.assertEqual((self.h.revision, self.h.backend.capture), (1, 1))
        self.assertEqual(result['observation'], before)

    def test_legacy_observation_tools_are_not_in_default_action_loop(self):
        for name in ('observe', 'start_observation', 'get_observation', 'cancel_observation'):
            self.assertEqual(self.h.call(name, {}, name)['error']['code'], 'unknown_tool')
        self.assertEqual(self.h.backend.capture, 1)
        self.assertEqual(len(self.h.surround.jobs), 0)

    def test_initialize_schema_and_metadata_do_not_add_an_action_gate(self):
        tool = next(t for t in self.h.tool_specs() if t['name']=='initialize')
        self.assertTrue(tool['annotations']['readOnlyHint'])
        self.assertTrue(tool['annotations']['idempotentHint'])
        self.assertEqual(self.h.call('initialize', {'reset':True}, 'bad')['error']['code'], 'invalid_arguments')
        # Preparing the harness already captured the first RGB; no mandatory-init gate.
        result = self.h.call('act', {'primitive':'wait', 'target':None, 'revision':0}, 'direct')
        self.assertTrue(result['ok']); self.assertEqual(self.h.backend.capture, 2)
