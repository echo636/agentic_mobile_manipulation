"""Synthetic traces exercise the independent evaluator; no physical evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.manip_acceptance import evaluate


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name);(self.run/'env').mkdir()
        pose = lambda z: {'position_m': [0, 0, z], 'quaternion_xyzw': [0, 0, 0, 1]}
        hand = {'held_object': None}
        initial = {'capture': {'sim_step': 0}, 'oracle_3d': {
            'objects': [{'id': 'bottle', 'task_names': ['wine_bottle.n.01_1'], 'pose': pose(1)}],
            'robot': {'base_pose_world': pose(0), 'arms': {'left': dict(hand), 'right': dict(hand)},
                      'trunk': {'body_posture': {'upper_body_height_above_base_m': 1.14}}}}}
        final = copy.deepcopy(initial)
        final['oracle_3d']['robot']['arms']['right'] = {'held_object': 'bottle', 'native_grasp': {
            'object_id': 'bottle', 'target_link': 'base_link', 'constraint_type': 'fixture'}}
        self.events = [{'kind': 'episode_started', 'observation': initial}, {'kind': 'tool_call', 'name': 'grasp'},
                       {'kind': 'tool_result', 'name': 'wait', 'result': {'ok': True, 'observation': final}}]
        self.rows = [{'sim_step': n, 'phase': 'native_manipulation', 'base_pose_world': pose(0),
                      'body_posture': {'upper_body_tilt_deg': 0, 'upper_body_height_above_base_m': 1.14},
                      'arms': {'left': {'held_object': None}, 'right': {'held_object': 'bottle',
                               'held_object_pose_world': pose(1.1), 'actual': pose(1.1)}}} for n in range(1, 61)]

    def assess(self, goal=None):
        (self.run/'env/events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.events))
        (self.run/'env/waypoint_trace.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        return evaluate({'run_dir': str(self.run), 'goal': goal or {}, 'record': {'evaluation': {'official_task_success': False},
                                                            'controller_result': {'ok': True}}})

    def test_continuous_trace_can_pass_engineering_while_whole_task_is_false(self):
        result = self.assess()
        self.assertTrue(result['engineering_success'], result)
        self.assertFalse(result['official_whole_task_success'])

    def test_endpoint_success_cannot_hide_mid_window_failures(self):
        original = copy.deepcopy(self.rows)
        for failure in ('drop', 'tilt', 'drift', 'gap', 'lift'):
            with self.subTest(failure=failure):
                self.rows = copy.deepcopy(original)
                row = self.rows[29]
                if failure == 'drop':row['arms']['right']['held_object'] = None
                if failure == 'tilt':row['body_posture']['upper_body_tilt_deg'] = 6
                if failure == 'drift':row['arms']['right']['actual']['position_m'][0] = .04
                if failure == 'gap':row['sim_step'] += 1
                if failure == 'lift':row['arms']['right']['held_object_pose_world']['position_m'][2] = 1.01
                self.assertFalse(self.assess()['engineering_success'])

    def test_regrasp_release_held_navigation_and_failed_calls_are_rejected(self):
        for event in ({'kind': 'tool_call', 'name': 'grasp'}, {'kind': 'tool_call', 'name': 'release'},
                      {'kind': 'tool_result', 'name': 'move_joints', 'result': {'ok': False}}):
            with self.subTest(event=event):
                self.events.append(event)
                self.assertFalse(self.assess()['engineering_success'])
                self.events.pop()
        self.rows[0]['phase'] = 'legacy_navigation'
        self.assertFalse(self.assess()['engineering_success'])

    def test_missing_sample_or_final_native_retention_does_not_pass(self):
        self.rows.pop()
        self.assertFalse(self.assess()['engineering_success'])

    def test_geometry_pick_requires_ten_centimeters_and_actual_single_retention(self):
        self.events[1] = {'kind': 'tool_call', 'name': 'manip', 'arguments': {'operation': 'pick'}}
        goal = {'protocol': 'geometry_pick_v1', 'lift_m': .10}
        self.assertTrue(self.assess(goal)['engineering_success'])
        self.rows[20]['arms']['right']['held_object_pose_world']['position_m'][2] = 1.08
        self.assertFalse(self.assess(goal)['checks']['continuous_lift'])
        self.rows[20]['arms']['right']['held_object'] = None
        self.assertFalse(self.assess(goal)['checks']['one_native_grasp'])


if __name__ == '__main__':unittest.main()
