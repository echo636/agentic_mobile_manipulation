import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('prepare_goals', Path(__file__).resolve().parents[1] / 'scripts/prepare_behavior100.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CoarseInstructionTests(unittest.TestCase):
    def test_official_inputs_are_preserved_in_both_modes(self):
        task = {'id': 'turning_on_radio', 'instruction': 'An exact official instruction.'}
        for mode in ('existing', 'mixed-coarse'):
            self.assertEqual(prepare.task_instruction(task, mode), (task['instruction'], 'official_gallery'))

    def test_all_50_missing_instructions_have_separate_coarse_inputs(self):
        self.assertEqual(len(prepare.COARSE_GOALS), 50)
        self.assertEqual(set(prepare.COARSE_GOALS), set(prepare.DERIVED))
        for task, detailed in prepare.DERIVED.items():
            coarse, source = prepare.task_instruction({'id': task}, 'mixed-coarse')
            self.assertEqual(source, 'project_coarse_task_goal_v1')
            self.assertNotEqual(coarse, detailed)
            self.assertLess(len(coarse), len(detailed))
            self.assertEqual(prepare.task_instruction({'id': task}), (detailed, 'project_translation_of_static_bddl_goal'))
