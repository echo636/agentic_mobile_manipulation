import unittest
import copy

from manipulation_agent.inspection_video import CAMERA_BOXES, decision_text, paginate, source_markers


class InspectionEvidenceTests(unittest.TestCase):
    def test_post_close_finish_uses_explicit_last_frame_without_changing_raw_markers(self):
        observation = {'capture': {'sim_step': 1072}, 'images': [{'env_steps': 1072}]}
        steps = [
            {'index': 12, 'request_id': 'finish', 'tool': 'finish', 'result': {'closed': True}},
            {'index': 13, 'request_id': 'retry', 'tool': 'finish',
             'at': '2026-10-03T03:33:26+00:00', 'result': {'error': {'code': 'episode_closed'}},
             'before': observation, 'after': copy.deepcopy(observation)},
        ]
        raw = {'markers': [{'request_id': 'finish', 'frame_index': 1084}],
               'frame_count': 1084, 'final_env_step': 1072, 'finished_at': '2026-10-03T03:27:15+00:00'}
        original = copy.deepcopy((steps, raw))
        markers, holds = source_markers(steps, raw)
        self.assertEqual(markers['retry'], 1084)
        self.assertEqual(holds[0]['source_frame'], 1083)
        self.assertFalse(holds[0]['synthetic_motion'])
        self.assertEqual((steps, raw), original)
        for change in [
            {'tool': 'act'}, {'at': '2026-10-03T03:26:00+00:00'},
            {'result': {'error': {'code': 'action_timeout'}}},
            {'after': {'capture': {'sim_step': 1073}}},
            {'before': None, 'after': None},
        ]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Missing source video marker'):
                source_markers([steps[0], {**steps[1], **change}], raw)
        with self.assertRaisesRegex(ValueError, 'Missing source video marker'):
            source_markers([{**steps[0], 'result': {}}, steps[1]], raw)

    def test_long_multilingual_text_is_preserved_across_pages(self):
        text = ('Original  spaces\n原始文本🙂\n\n' * 350) + 'Final character!'
        pages = paginate(text, 30, lambda c: 1, lines_per_page=7)
        self.assertGreater(len(pages), 10)
        self.assertEqual(''.join(''.join(p) for p in pages), text)
        self.assertTrue(all(len(p) <= 7 for p in pages))

    def test_full_arguments_and_separate_provider_and_assistant_text(self):
        step = {'tool': 'act', 'arguments': {'primitive': 'grasp', 'target': {'point': [.2, .4]}},
                'model_messages': [{'text': 'Public message\nline two.'}],
                'model_reasoning_summaries': [{'text': 'Returned summary.'}]}
        text = decision_text(step)
        self.assertIn('Public message\nline two.', text)
        self.assertIn('Returned summary.', text)
        self.assertIn('"grasp"', text)
        self.assertLess(text.index('Returned summary.'), text.index('Public message'))

    def test_front_is_largest_and_all_five_views_are_present(self):
        self.assertEqual(set(CAMERA_BOXES), {'front', 'back', 'left', 'right', 'spectator'})
        self.assertTrue(all(CAMERA_BOXES['front'][2] > v[2]
                            for k, v in CAMERA_BOXES.items() if k != 'front'))


if __name__ == '__main__':
    unittest.main()
