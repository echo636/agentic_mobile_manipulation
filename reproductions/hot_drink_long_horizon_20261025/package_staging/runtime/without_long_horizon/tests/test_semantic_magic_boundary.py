import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.observations.boundary import public_observation
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.tools import tool_specs
from manipulation_agent.vision_harness import VisionHarness


class SemanticMock(MockRGBBackend):
    mode = 'rgb_semantic_magic'

    def observe(self):
        result = super().observe()
        result.update(observation_mode=self.mode,
                      semantic_targets=['kettle', 'coffee_table'], held_target=None)
        return result

    def execute_semantic(self, primitive, target, max_steps, **kwargs):
        self.steps += 1
        return {'primitive': primitive, 'semantic_target': target,
                'grounded_category': 'kettle' if primitive == 'grasp' else None}


class SemanticMagicBoundary(unittest.TestCase):
    def test_semantic_profile_exposes_only_named_magic_actions(self):
        self.assertEqual({row['name'] for row in tool_specs('semantic')},
                         {'initialize', 'act_semantic', 'finish'})
        self.assertNotIn('act_semantic', {row['name'] for row in tool_specs('skills')})

    def test_named_action_is_bounded_and_goal_truth_stays_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'run'
            recorder = Recorder(root, {'backend': 'mock', 'observation_mode': 'rgb_semantic_magic'})
            harness = VisionHarness(SemanticMock(root), recorder, profile='semantic')
            observation = harness.initialize()['observation']
            self.assertEqual(observation['semantic_targets'], ['kettle', 'coffee_table'])
            self.assertNotIn('goal', json.dumps(observation))
            response = harness.call('act_semantic',
                                    {'primitive': 'grasp', 'target': 'kettle',
                                     'revision': 0, 'wait_seconds': None}, 'a1')
            self.assertTrue(response['ok'])
            self.assertEqual(response['effect']['grasped_category'], 'kettle')
            self.assertEqual(response['actions_remaining'], 79)
            missing = harness.call('act_semantic',
                                   {'primitive': 'grasp', 'target': 'hidden_id',
                                    'revision': 1, 'wait_seconds': None}, 'a2')
            self.assertEqual(missing['error']['code'], 'unknown_semantic_target')

    def test_semantic_observation_rejects_evaluator_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            observation = SemanticMock(Path(directory)).observe()
            observation['goal_success'] = True
            with self.assertRaises(RuntimeError):
                public_observation(observation, 0)


if __name__ == '__main__':
    unittest.main()
