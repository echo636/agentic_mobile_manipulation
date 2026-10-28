import importlib.util
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from manipulation_agent.executors.material_transfer import free_volume_positions


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires geometry environment')
class MaterialVolumeSamplingTests(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        state = ModuleType('omnigibson.object_states')
        state.ContainedParticles = type('ContainedParticles', (), {})
        override = patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'),
                                           'omnigibson.object_states': state})
        override.start()
        self.addCleanup(override.stop)
        self.destination = SimpleNamespace(states={state.ContainedParticles: SimpleNamespace(
            link=SimpleNamespace(visual_aabb=(torch.zeros(3), torch.tensor([.12, .12, .08])),
                                 check_points_in_volume=lambda points: torch.ones(len(points), dtype=torch.bool)))})
        self.scene = SimpleNamespace(active_systems={})
        # A 36 mm gap around a 30 mm particle leaves a narrow range of centers.
        # The original 30 mm lattice misses it; a finer search must find it.
        self.system = SimpleNamespace(particle_radius=.015, particle_particle_rest_distance=.03,
            check_in_contact=lambda positions: (positions[:, 2] - .03).abs() > .003)

    def test_free_layer_between_lattice_planes_is_found_without_overlap(self):
        torch = self.torch
        report = {}
        points = free_volume_positions(self.scene, self.destination, self.system, 6, diagnostics=report)
        self.assertEqual(len(points), 6)
        self.assertEqual(report['passes'][0]['rigid_clear'], 0)
        self.assertGreater(report['passes'][1]['rigid_clear'], 0)
        self.assertTrue(bool(((points[:, 2] - .03).abs() <= .003).all()))
        distances = torch.pdist(points)
        self.assertTrue(bool((distances >= .03 - 1e-7).all()))

    def test_refinement_keeps_rejecting_genuinely_occupied_space(self):
        self.system.check_in_contact = lambda positions: self.torch.ones(len(positions), dtype=self.torch.bool)
        report = {}
        points = free_volume_positions(self.scene, self.destination, self.system, 1, diagnostics=report)
        self.assertEqual(len(points), 0)
        self.assertEqual(report['termination'], 'candidate_grids_exhausted')
