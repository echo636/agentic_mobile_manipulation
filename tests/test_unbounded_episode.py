"""Explicit unlimited episodes retain counters and formal finish, without hidden caps."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from manipulation_agent.contracts import Budget
from manipulation_agent.deadline import EpisodeDeadline, write_execution_clock, validate_execution_clock
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder, read_run
from manipulation_agent.vision_harness import VisionHarness


class UnboundedEpisodeTests(unittest.TestCase):
    def test_no_action_call_physics_or_wall_cutoff_and_counts_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'episode';recorder=Recorder(root,{'backend':'mock'})
            backend=MockRGBBackend(root);backend.deadline=EpisodeDeadline()
            harness=VisionHarness(backend,recorder,
                Budget(max_actions=1,max_calls=1,max_sim_steps=1,wall_seconds=None),unbounded_episode=True)
            harness.start_standalone_clock()
            for n in range(53):
                args={'primitive':'toggle_on','revision':harness.revision,
                      'target':{'image_ref':harness.snapshot['images'][0]['image_ref'],'point':[.5,.5]}}
                with patch('manipulation_agent.deadline.time.time',return_value=time.time()+86400):
                    reply=harness.call('act',args,str(n))
                self.assertTrue(reply['ok']);self.assertIsNone(reply['execution_budget']['max_steps'])
            self.assertEqual(harness.agent_steps,53)
            self.assertFalse(harness.agent_step_closure_due())
            self.assertTrue(harness.call('finish',{'outcome':'achieved','reason':'fixture'},'end')['closed'])
            result=read_run(root)
            self.assertEqual(result['agent_steps'],53);self.assertIsNone(result['max_agent_steps'])
            self.assertFalse(result['agent_step_budget_exhausted']);self.assertTrue(result['task_success'])

    def test_unbounded_clock_is_immutable_and_rejects_hidden_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'clock.json';clock=write_execution_clock(path,None,unbounded=True)
            self.assertEqual(write_execution_clock(path,None,unbounded=True),clock)
            for extra in ({'max_agent_steps':50},{'episode_deadline_unix':time.time()+100},
                          {'execution_budget_seconds':100}):
                with self.subTest(extra=extra),self.assertRaises(ValueError):
                    validate_execution_clock({**clock,**extra})
            with self.assertRaises(ValueError):write_execution_clock(path,60,unbounded=True)
            with self.assertRaises(ValueError):write_execution_clock(path,None,max_agent_steps=50,unbounded=True)
