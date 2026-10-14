import unittest
from unittest.mock import Mock, patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.gt_navigation import (
    GridMap, NavigationError, PlanningBudget, SearchBudgetExhausted, plan_navigation,
)


class NavigationWorkBudgetTests(unittest.TestCase):
    def test_occluded_target_does_not_scan_every_cell_for_one_heading(self):
        grid = GridMap(61, 61, .05, (0., 0.), bytes([1]) * 61**2)
        visible = Mock(return_value=False)
        with self.assertRaises(NavigationError) as caught:
            plan_navigation(grid, (1., 1.), (2., 2.), max_snap=None,
                            candidate_filter=visible, max_candidate_checks=7)
        self.assertEqual(caught.exception.code, 'navigation_search_exhausted')
        self.assertEqual(visible.call_count, 7)

    def test_heading_searches_share_visibility_budget(self):
        budget = PlanningBudget(lambda: None, visibility_checks=3)
        for _ in range(3): budget.visibility()
        with self.assertRaises(SearchBudgetExhausted): budget.visibility()
        self.assertEqual(budget.visibility_checks, 3)

    def test_episode_cancellation_takes_precedence_over_local_budget(self):
        def cancelled(): raise SkillError('episode_cancelled', 'closed')
        budget = PlanningBudget(cancelled, seconds=0.)
        with self.assertRaises(SkillError) as caught: budget.check()
        self.assertEqual(caught.exception.code, 'episode_cancelled')

    def test_elapsed_action_budget_is_recoverable(self):
        with patch('manipulation_agent.executors.gt_navigation.time.monotonic', return_value=100.):
            budget = PlanningBudget(lambda: None, seconds=20.)
        with patch('manipulation_agent.executors.gt_navigation.time.monotonic', return_value=120.):
            with self.assertRaises(SearchBudgetExhausted) as caught: budget.check()
        self.assertEqual(caught.exception.code, 'navigation_search_exhausted')


if __name__ == '__main__': unittest.main()
