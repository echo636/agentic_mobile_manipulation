"""Optional route improvement must not corrupt the executable fallback."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.gt_navigation import NavigationError
from manipulation_agent.executors.navigation_refinement import refine_navigation_detour


class DetourFallbackTests(unittest.TestCase):
    def test_failed_fine_search_restores_all_execution_geometry(self):
        self.check_failed_refinement(SimpleNamespace(distance_m=20., estimated_motion_seconds=45.))

    def test_failed_refinement_before_certification_returns_uncertified_guide(self):
        self.check_failed_refinement(None)

    def check_failed_refinement(self, route):
        original = {name: object() for name in ('_navigation_layers', '_navigation_support',
                    '_navigation_clearance', '_navigation_layer_yaw')}
        backend = SimpleNamespace(**original, robot=SimpleNamespace(is_holonomic_base=False),
                                  deadline=SimpleNamespace(check=lambda: None), output=Path('/unused'))
        coarse = SimpleNamespace(resolution=.05)
        detail = {'coarse': True}
        plan = SimpleNamespace(goal=(2., 0.), final_yaw=0., score=0., geodesic_m=20.)
        collision, selection = object(), object()
        def build(*args, **kwargs):
            for name in original:
                setattr(backend, name, object())
            return SimpleNamespace(resolution=kwargs['sampling']['resolution']), {}
        fine_checker = SimpleNamespace(motion_free=lambda *args: True, check_cancelled=None)
        with patch('manipulation_agent.executors.live_gt_map.build_navigation_grid', side_effect=build), \
                patch('manipulation_agent.executors.navigation_refinement.HeadingCollisionChecker', return_value=fine_checker), \
                patch('manipulation_agent.executors.navigation_refinement.local_exit_certificate',
                      side_effect=NavigationError('No fine route')):
            result = refine_navigation_detour(backend, 0, coarse, detail, (0., 0., 0.),
                                               [plan], plan, route, collision, selection)
        for actual, expected in zip(result[:6], (coarse, detail, plan, route, collision, selection)):
            self.assertIs(actual, expected)
        self.assertEqual(result[-1]['status'], 'kept_coarse_route')
        self.assertEqual(len(result[-1]['attempts']), 2)
        for name, value in original.items():
            self.assertIs(getattr(backend, name), value)


if __name__ == '__main__':
    unittest.main()
