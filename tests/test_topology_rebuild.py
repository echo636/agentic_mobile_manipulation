import math
from types import SimpleNamespace
import unittest

from manipulation_agent.executors.topology_rebuild import _state_mismatches


class CutStateComparisonTests(unittest.TestCase):
    torch = SimpleNamespace(Tensor=())

    def state(self, value):
        return {0: {'registry': {'object_registry': {'cut_piece': {'non_kin': {
            'MaxTemperature': {'max_temperature': value}}}}}}}

    def test_native_initial_max_temperature_is_preserved(self):
        self.assertEqual(_state_mismatches(self.state(-math.inf), self.state(-math.inf), self.torch), [])

    def test_changed_or_invalid_temperature_still_fails(self):
        for before, after in [(-math.inf, 23.), (23., -math.inf),
                              (math.nan, math.nan), (math.inf, math.inf), (23., 24.), (-math.inf, None)]:
            with self.subTest(before=before, after=after):
                self.assertTrue(_state_mismatches(self.state(before), self.state(after), self.torch))

    def test_nonfinite_other_state_is_not_a_temperature_sentinel(self):
        self.assertTrue(_state_mismatches({'position': -math.inf}, {'position': -math.inf}, self.torch))
        self.assertTrue(_state_mismatches({'max_temperature': -math.inf}, {'max_temperature': -math.inf}, self.torch))
        self.assertEqual(_state_mismatches({'temperature': 23.}, {'temperature': 23.}, self.torch), [])
