import unittest

from manipulation_agent.executors.live_gt_map import navigation_height_edges


def cooccupies(edges, first, second):
    return any(first[0] <= high and first[1] >= low
               and second[0] <= high and second[1] >= low
               for low, high in zip(edges, edges[1:]))


class PayloadHeightBands(unittest.TestCase):
    def test_lifted_food_does_not_inherit_countertop_obstacles(self):
        counter, food = (.85, .90), (1.06, 1.13)
        self.assertTrue(cooccupies(navigation_height_edges(1.5), counter, food))
        refined = navigation_height_edges(1.5, [food])
        self.assertFalse(cooccupies(refined, counter, food))
        # A real shelf at the carried food's height remains blocking.
        self.assertTrue(cooccupies(refined, (1.08, 1.09), food))

    def test_low_payload_child_is_not_clipped_away(self):
        assembly = [(.83, .93), (1.06, 1.13)]
        edges = navigation_height_edges(1.5, assembly)
        self.assertTrue(cooccupies(edges, (.85, .90), assembly[0]))
        self.assertLessEqual(len(edges), 7)

    def test_empty_hand_and_micrometre_feedback_preserve_cache_bands(self):
        self.assertEqual(navigation_height_edges(1.5), [.03, .35, .8, 1.25, 1.52])
        self.assertEqual(navigation_height_edges(1.5, [(1.065, 1.135)]),
                         navigation_height_edges(1.5, [(1.065001, 1.134999)]))

    def test_refinement_cannot_remove_an_actual_vertical_overlap(self):
        for low in (.02, .31, .77, 1.05, 1.22, 1.6):
            payload = (low, low + .2)
            edges = navigation_height_edges(low + .7, [payload])
            self.assertEqual(edges, sorted(set(edges)))
            for fraction in (0., .1, .5, .9, 1.):
                z = low + fraction * .2
                if z >= .03:
                    self.assertTrue(cooccupies(edges, payload, (z, z)))


if __name__ == '__main__':
    unittest.main()
