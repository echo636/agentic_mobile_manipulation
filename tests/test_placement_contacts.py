"""Signed-contact policy contracts; physical evidence is recorded separately."""
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from manipulation_agent.executors.placement_contacts import contact_report


class PlacementContactPolicy(unittest.TestCase):
    def report(self, separation, *, truncated=False, internal=False, sleeping=False,
               no_points=False, allow_sleeping=False):
        body = SimpleNamespace(scene=SimpleNamespace(idx=0),
            links={'body': SimpleNamespace(prim_path='/item', is_asleep=sleeping)})
        other = '/item' if internal else '/support'
        view = SimpleNamespace(get_contact_data=lambda **kw:(
            None, None, None, np.array([[separation]]),
            np.array([[0 if no_points else 2 if truncated else 1]]), np.array([[0]])))
        api = SimpleNamespace(get_contact_pairs=lambda *a, **kw:{('/item', other)},
            _CONTACT_VIEW={0:view}, _PATH_TO_ROW_IDX={0:{'/item':0}},
            _PATH_TO_COL_IDX={0:{other:0}})
        module = ModuleType('omnigibson.utils.usd_utils'); module.RigidContactAPI=api
        backend=SimpleNamespace(og=SimpleNamespace(sim=SimpleNamespace(get_physics_dt=lambda:1/120)))
        with patch.dict('sys.modules', {'omnigibson.utils.usd_utils':module}):
            return contact_report(backend, body, allow_sleeping=allow_sleeping)

    def test_touching_and_shallow_solver_overlap_are_admitted(self):
        for separation in (.001, 0., -.001, -.004):
            with self.subTest(separation=separation):
                self.assertTrue(self.report(separation)['admissible'])

    def test_deep_overlap_is_rejected(self):
        result=self.report(-.04)
        self.assertFalse(result['admissible'])
        self.assertEqual(result['reason'],'deep_penetration')

    def test_missing_or_nonfinite_depth_is_not_a_clearance_certificate(self):
        self.assertFalse(self.report(0.,truncated=True)['admissible'])
        self.assertFalse(self.report(float('nan'))['admissible'])

    def test_internal_assembly_contact_is_ignored(self):
        self.assertTrue(self.report(-.1,internal=True)['admissible'])

    def test_sleeping_cached_contact_is_only_allowed_at_final_support_check(self):
        self.assertFalse(self.report(0., sleeping=True, no_points=True)['admissible'])
        final = self.report(0., sleeping=True, no_points=True, allow_sleeping=True)
        self.assertTrue(final['admissible'])
        self.assertIsNone(final['minimum_separation_m'])
        self.assertEqual(len(final['sleeping_pairs_without_current_points']), 1)
        self.assertFalse(self.report(0., no_points=True, allow_sleeping=True)['admissible'])
        self.assertFalse(self.report(-.04, sleeping=True, allow_sleeping=True)['admissible'])
        self.assertFalse(self.report(0., truncated=True, sleeping=True, allow_sleeping=True)['admissible'])
