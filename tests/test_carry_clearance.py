"""Ceiling, payload and failed-query regressions for optional carry lifting."""
from copy import deepcopy
import importlib.util
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.carry_clearance import choose_lift, assembly_lift


class LiftClearanceTests(unittest.TestCase):
    def test_low_ceiling_limits_lift_and_leaves_input_untouched(self):
        bounds = [([0., 0., .9], [.2, .2, 1.05])]
        before = deepcopy(bounds)
        self.assertEqual(choose_lift(bounds, lambda low, high: high[2] >= 1.12), .045)
        self.assertEqual(bounds, before)

    def test_payload_ceiling_is_checked_even_when_root_is_clear(self):
        bounds = [([0., 0., .9], [.2, .2, 1.]), ([0., 0., 1.], [.1, .1, 1.1])]
        self.assertEqual(choose_lift(bounds, lambda low, high: high[2] >= 1.12), .01)

    def test_narrow_enclosure_keeps_original_pose(self):
        self.assertEqual(choose_lift([([0, 0, 0], [1, 1, 1])], lambda *args: True), 0.)

    def test_clearance_query_failure_never_becomes_an_accepted_lift(self):
        def failure(*args):
            raise RuntimeError('unavailable query')
        with self.assertRaisesRegex(RuntimeError, 'unavailable query'):
            choose_lift([([0, 0, 0], [1, 1, 1])], failure)

    def test_nonfinite_or_missing_geometry_cannot_be_treated_as_free(self):
        for bounds in ([], [([0, 0, 0], [1, 1, float('nan')])], [([0, 0, 0], [1, 1, 0])]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                choose_lift(bounds, lambda *args: False)

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires geometry environment')
    def test_live_adapter_keeps_foreign_bodies_and_captured_particle_extent(self):
        import torch
        own = SimpleNamespace(prim_path='/bag/base', is_meta_link=False)
        helper = SimpleNamespace(prim_path='/oven/fill', is_meta_link=True)
        obj = SimpleNamespace(name='bag', links={'base': own},
                              aabb=(torch.tensor([0., 0., .9]), torch.tensor([.2, .2, 1.])))
        oven = SimpleNamespace(links={'fill': helper})
        system = SimpleNamespace(name='material', particles_view=SimpleNamespace(prim_paths=['/grain']),
            particle_contact_radius=.01,
            get_particles_position_orientation=lambda: (torch.tensor([[.1, .1, 1.08]]), None))
        particle = SimpleNamespace(system=system, paths=('/grain',))
        backend = SimpleNamespace(env=SimpleNamespace(scene=SimpleNamespace(
            objects=[obj, oven], active_systems={'material': system})))
        records = []
        backend._carry_record = lambda **row: records.append(row)

        def overlap(half, center, orientation, callback, any_hit):
            for path in ('/bag/base', '/oven/fill', '/grain'):
                callback(SimpleNamespace(rigid_body=path))
            if center[2] + half[2] > 1.12:
                callback(SimpleNamespace(rigid_body='/oven/ceiling'))
        lazy = ModuleType('omnigibson.lazy')
        lazy.omni = SimpleNamespace(physx=SimpleNamespace(
            get_physx_scene_query_interface=lambda: SimpleNamespace(overlap_box=overlap)))
        with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'), 'omnigibson.lazy': lazy}):
            self.assertEqual(assembly_lift(backend, obj, [], [particle]), .02)
        self.assertTrue(records[-1]['rejected_sweeps'])
        self.assertEqual(records[-1]['rejected_sweeps'][0]['bodies'], ['/oven/ceiling'])
        self.assertFalse(records[-1]['extraction_path_certified'])


if __name__ == '__main__':
    unittest.main()
