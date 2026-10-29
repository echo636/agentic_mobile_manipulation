import unittest

from manipulation_agent.executors.omnigibson_rgb import stable_spectator_candidate


class SpectatorSelectionTests(unittest.TestCase):
    def test_same_action_keeps_usable_camera_when_other_scores_fluctuate(self):
        prior=(1.2,'prior_camera',1.2,(0,0))
        challenger=(2.0,'challenger_camera',2.0,(1,0))
        self.assertIs(stable_spectator_candidate(
            [prior,challenger],(0,0),same_request=True),prior)
        self.assertIs(stable_spectator_candidate(
            [prior,challenger],(0,0),same_request=False),challenger)

    def test_occluded_previous_camera_can_change_during_action(self):
        prior=(.5,'prior_camera',.4,(0,0))
        clear=(1.5,'clear_camera',1.5,(2,1))
        self.assertIs(stable_spectator_candidate(
            [prior,clear],(0,0),same_request=True),clear)


if __name__ == '__main__':
    unittest.main()
