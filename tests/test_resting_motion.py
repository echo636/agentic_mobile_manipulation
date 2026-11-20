import math
import unittest

from manipulation_agent.executors.resting_motion import RestingMotionWindow


class RestingMotionTests(unittest.TestCase):
    def test_stable_contact_jitter_requires_a_complete_window(self):
        window = RestingMotionWindow(1/30)
        for i in range(window.size-1):
            window.add((i % 2 * .00015, 0., 0.), (0., 0., 0., (-1.)**i))
        self.assertFalse(window.report()['stable'])
        window.add((0., 0., 0.), (0., 0., 0., 1.))
        self.assertTrue(window.report()['stable'])

    def test_translation_or_rocking_is_not_resting_even_if_it_returns_to_start(self):
        for rotating in (False, True):
            with self.subTest(rotating=rotating):
                window = RestingMotionWindow(1/30)
                for i in range(window.size):
                    excursion = math.sin(math.pi*i/(window.size-1))
                    angle = math.radians(4)*excursion if rotating else 0.
                    window.add((0. if rotating else .01*excursion, 0., 0.),
                               (math.sin(angle/2), 0., 0., math.cos(angle/2)))
                self.assertFalse(window.report()['stable'])

    def test_nonfinite_pose_discards_previous_stability_evidence(self):
        window = RestingMotionWindow(1/120)
        for _ in range(window.size):window.add((0., 0., 0.), (0., 0., 0., 1.))
        self.assertTrue(window.report()['stable'])
        window.add((float('nan'), 0., 0.), (0., 0., 0., 1.))
        self.assertFalse(window.report()['stable'])


if __name__ == '__main__':unittest.main()
