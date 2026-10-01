import unittest

from manipulation_agent.inspection_video import CAMERA_BOXES, decision_text, paginate


class InspectionEvidenceTests(unittest.TestCase):
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
