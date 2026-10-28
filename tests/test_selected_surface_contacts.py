"""A ray through overlapping counters must not certify the wrong support."""
import importlib.util
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.placement import CheckedPlacement


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires geometry environment')
class SelectedSurfaceContactTests(unittest.TestCase):
    def check(self, contacts, *, require_contact=True, ray_body='/prep/base'):
        import torch

        def body(path):
            return SimpleNamespace(links={'base': SimpleNamespace(prim_path=path)})

        held, target, robot = body('/brush/base'), body('/prep/base'), body('/robot/base')
        held.get_linear_velocity = held.get_angular_velocity = lambda: torch.zeros(3)
        backend = CheckedPlacement()
        backend.torch, backend.robot = torch, robot
        backend._get_held = lambda: None
        # The recorded failure had a 3 mm overlap and a ray to the requested
        # counter, but its only physical contact was the adjacent counter.
        backend._collision_vertices = lambda obj: [[7.75, -.63, .884], [7.77, -.61, .884]]
        backend._placement_contacts = lambda *args: contacts
        backend._placement_contact_report = lambda *args, **kwargs: {"admissible": True}
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        sampling.raytest = lambda *args, **kwargs: {
            'hit': True, 'rigidBody': ray_body,
            'position': [7.76, -.62, .887], 'normal': [0., 0., 1.]}
        modules = {'omnigibson': ModuleType('omnigibson'),
                   'omnigibson.utils': ModuleType('omnigibson.utils'),
                   'omnigibson.utils.sampling_utils': sampling}
        with patch.dict(sys.modules, modules):
            return backend._selected_surface_support(
                held, target, torch.tensor([7.76, -.62, .887]), False,
                require_contact=require_contact)

    def test_overlapping_counter_contact_rejects_both_candidate_and_final(self):
        for required in (False, True):
            with self.subTest(require_contact=required):
                passed, evidence = self.check(['/adjacent/base'], require_contact=required)
                self.assertFalse(passed)
                self.assertTrue(evidence['checks']['support_ray_hits_selected_object'])
                self.assertFalse(evidence['checks']['selected_contact_consistent'])

    def test_actual_target_contact_passes_despite_stale_native_touching(self):
        passed, evidence = self.check(['/prep/base'])
        self.assertTrue(passed)
        self.assertTrue(evidence['selected_contact'])
        self.assertFalse(evidence['touching_selected_object'])

    def test_explicit_contact_requirement_still_rejects_missing_contacts(self):
        self.assertTrue(self.check([], require_contact=False)[0])
        self.assertFalse(self.check([], require_contact=True)[0])

    def test_target_contact_cannot_replace_selected_ray_or_robot_clearance(self):
        self.assertFalse(self.check(['/prep/base'], ray_body='/adjacent/base')[0])
        self.assertFalse(self.check(['/prep/base', '/robot/base'])[0])


if __name__ == '__main__':
    unittest.main()
