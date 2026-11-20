import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.pose_replay import enrich_pose_parameters


class PoseReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = dict(steps=[dict(index=3, request_id='native', tool='manip',
                                    arguments=dict(mode='program', program='bottle_grasps'))],
                         model_transcript=[dict(kind='tool_call', tool='manip', step=3,
                                                call_id='different-model-id',
                                                arguments=dict(mode='program', program='bottle_grasps'))])

    def save(self, events):
        path = self.root / 'model_pose.jsonl'
        path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        return path

    def goal(self, name='pregrasp', goal=None):
        return dict(kind='pose_phase_goal', request_id='native', phase_name=name,
                    phase='candidate-A/' + name + '/plan', sim_step=12,
                    pose_operation=dict(mode='pose', arm='both' if goal else 'right',
                                        goal=goal or dict(position_m=[1, 2, 3], quaternion_xyzw=[0, 0, 0, 1]),
                                        gripper='hold'))

    def test_preserves_arguments_calls_and_raw_evidence_with_different_id_namespaces(self):
        raw = copy.deepcopy(self.data)
        path = self.save([self.goal()])
        before = path.read_bytes()
        result = enrich_pose_parameters(self.data, self.root)
        self.assertEqual(self.data, raw)
        self.assertEqual(path.read_bytes(), before)
        entry = result['model_transcript'][0]
        self.assertEqual(entry['arguments'], raw['model_transcript'][0]['arguments'])
        self.assertEqual(len(result['model_transcript']), 1)
        self.assertEqual(entry['resolved_pose_phases'][0]['source_line'], 1)
        self.assertIsNone(entry['resolved_pose_phases'][0]['outcome'])
        self.assertEqual(enrich_pose_parameters(result, self.root), result)

    def test_repeated_candidates_keep_failure_order_and_missing_result(self):
        outcome = dict(kind='pose_phase_result', request_id='native', phase_name='pregrasp',
                       outcome=dict(verified=False, reason='collision'))
        self.save([self.goal(), outcome, self.goal()])
        phases = enrich_pose_parameters(self.data, self.root)['steps'][0]['resolved_pose_phases']
        self.assertEqual(len(phases), 2)
        self.assertFalse(phases[0]['outcome']['verified'])
        self.assertIsNone(phases[1]['outcome'])
        self.assertEqual(phases[0]['result_source_line'], 2)
        self.assertEqual(phases[1]['source_line'], 3)

    def test_whole_body_goals_and_invalid_or_unmatched_logs(self):
        body = dict(joint_positions_rad={f'joint{i}': i / 10 for i in range(18)})
        path = self.save([self.goal('recover_body', body), [],
                          dict(kind='pose_phase_result', request_id='unknown', phase_name='pregrasp'),
                          dict(kind='pose_phase_result', request_id='native', phase_name='unmatched')])
        with path.open('a') as stream:
            stream.write('{invalid\n')
        result = enrich_pose_parameters(self.data, self.root)
        self.assertEqual(result['model_transcript'][0]['resolved_pose_phases'][0]['pose_operation']['goal'], body)
        self.assertEqual([d['code'] for d in result['pose_parameter_source']['diagnostics']],
                         ['unmatched_pose_event', 'unmatched_pose_result', 'malformed_pose_event'])

    def test_missing_logs_leave_old_replays_usable(self):
        self.assertIs(enrich_pose_parameters(self.data, self.root), self.data)
        self.assertIs(enrich_pose_parameters(self.data, None), self.data)


if __name__ == '__main__':
    unittest.main()
