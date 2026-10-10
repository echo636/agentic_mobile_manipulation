"""Placement regressions for narrow supports and noncentral object origins.

These CPU checks validate candidate construction only; actual collision and
settling acceptance is covered by the separately recorded simulator probes.
"""
import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Requires numpy geometry dependency')
class SelectedSurfaceGeometry(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from manipulation_agent.executors.surface_geometry import bottom_anchor, rotated_surface_geometry
        self.np = np
        self.bottom_anchor = bottom_anchor
        self.rotated_surface_geometry = rotated_surface_geometry

    def box_points(self, lo, hi):
        import itertools
        return self.np.array(list(itertools.product(*zip(lo, hi))), dtype=float)

    def test_pan_handle_does_not_move_actual_bottom_anchor(self):
        # The body rests on the stove; the raised long handle may overhang it.
        body = self.box_points([-.10, -.10, 0.], [.10, .10, .035])
        handle = self.box_points([.10, -.02, .025], [.40, .02, .04])
        points = self.np.concatenate([body, handle])
        anchor = self.bottom_anchor(points)
        self.np.testing.assert_allclose(anchor, [0., 0., 0.], atol=1e-8)
        # An AABB midpoint would shift 15 cm toward the handle and is wrong.
        self.assertAlmostEqual(float((points[:, 0].min()+points[:, 0].max())/2), .15)

    def test_yaw_preserves_slender_log_width_instead_of_diagonal_square(self):
        points = self.box_points([-.1495, -.0505, -.04955], [.1495, .0505, .04955])
        rotated, anchor = self.rotated_surface_geometry(points, self.np.zeros(3), 90.)
        extent = self.np.ptp(rotated, axis=0)
        self.np.testing.assert_allclose(extent, [.101, .299, .0991], atol=1e-7)
        self.np.testing.assert_allclose(anchor, [0., 0., -.04955], atol=1e-7)
        self.assertLess(float(extent[0]), .27)  # No fabricated 31.5 cm width.

    def test_yaw_45_uses_rotated_shape_instead_of_yaw_independent_square(self):
        points = self.box_points([-.1495, -.0505, -.04955], [.1495, .0505, .04955])
        rotated, _ = self.rotated_surface_geometry(points, self.np.zeros(3), 45.)
        expected = (.299+.101)/self.np.sqrt(2.)
        self.np.testing.assert_allclose(self.np.ptp(rotated, axis=0),
                                       [expected, expected, .0991], atol=1e-7)
        diagonal = self.np.hypot(.299, .101)
        self.assertLess(float(self.np.ptp(rotated, axis=0)[0]), diagonal)

    def test_rotation_uses_object_origin_and_preserves_noncentral_shape(self):
        # The object's root is displaced from its geometry center. Rotation
        # must use root-relative vertices, not rotate world translations.
        origin = self.np.array([7., -4., 2.])
        local = self.box_points([.10, -.10, -.05], [.30, .10, .05])
        rotated, anchor = self.rotated_surface_geometry(local+origin, origin, 90.)
        self.np.testing.assert_allclose(rotated.min(axis=0), [-.10, .10, -.05], atol=1e-7)
        self.np.testing.assert_allclose(rotated.max(axis=0), [.10, .30, .05], atol=1e-7)
        self.np.testing.assert_allclose(anchor, [0., .20, -.05], atol=1e-7)
        destination = self.np.array([1., 2., .8])
        new_root = destination-anchor
        self.np.testing.assert_allclose(new_root+anchor, destination, atol=1e-7)

    def test_no_requested_yaw_preserves_held_world_orientation(self):
        # The vertices already reflect a held object tilted in world space.
        origin = self.np.array([2., 3., 4.])
        local = self.np.array([[-.12, -.03, -.02], [.12, -.03, .02],
                              [-.12, .03, .01], [.12, .03, .05]])
        rotated, anchor = self.rotated_surface_geometry(local+origin, origin)
        self.np.testing.assert_allclose(rotated, local, atol=1e-7)
        self.assertAlmostEqual(float(anchor[2]), -.02)

    def test_curved_log_can_rest_on_narrow_bottom_contact(self):
        # The support strip is narrow; empty corners of its box need not hit.
        angles = self.np.arange(0., 2*self.np.pi, self.np.pi/8)
        points = self.np.array([[x, .05*self.np.cos(a), .05*self.np.sin(a)]
                               for x in [-.15, .15] for a in angles])
        anchor = self.bottom_anchor(points)
        self.np.testing.assert_allclose(anchor, [0., 0., -.05], atol=1e-7)

    def test_empty_geometry_is_rejected_explicitly(self):
        with self.assertRaises((ValueError, RuntimeError)):
            self.bottom_anchor(self.np.empty((0, 3)))

    def test_nonfinite_geometry_is_rejected_explicitly(self):
        with self.assertRaises((ValueError, RuntimeError)):
            self.rotated_surface_geometry(self.np.array([[float('nan'), 0., 0.]]), self.np.zeros(3))


class PlacementRejectionTransactions(unittest.TestCase):
    def test_missing_collision_geometry_has_explicit_error_without_box_fallback(self):
        from types import SimpleNamespace
        from manipulation_agent.contracts import SkillError
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        held = SimpleNamespace(links={'root': SimpleNamespace(collision_meshes={})})
        held.get_base_aligned_bbox = lambda: self.fail('Must not fall back to an inflated box')
        with self.assertRaises(SkillError) as error:
            backend._collision_vertices(held)
        self.assertEqual(error.exception.code, 'unsupported_geometry')

    def test_obstructed_candidates_restore_world_and_carry_without_advancing_metrics(self):
        import copy
        import sys
        from contextlib import nullcontext
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.contracts import SkillError
        from manipulation_agent.executors.carry import ControlledCarry
        from manipulation_agent.executors.placement import CheckedPlacement
        class Harness(CheckedPlacement, ControlledCarry):
            pass
        backend = Harness()
        world = {'position': [0., 0., 1.], 'neighbor': [3., 0., 0.]}
        initial = copy.deepcopy(world)
        records, loads, physics = [], [], []
        held = SimpleNamespace(name='pan')
        held.set_position_orientation = lambda p, q: world.update(position=list(p))
        held.keep_still = held.wake = lambda: None
        def load(state, **kwargs):
            loads.append(copy.deepcopy(state))
            world.clear(); world.update(copy.deepcopy(state))
        backend.og = SimpleNamespace(sim=SimpleNamespace(
            dump_state=lambda **kw: copy.deepcopy(world), load_state=load,
            get_physics_dt=lambda: .01))
        backend.ideal_carry = True
        backend._ideal_held = held
        relation = object()
        backend._carry_relative = relation
        backend._carry_contents = []
        dependencies = [('preserved payload dependency',)]
        backend._carry_dependencies = dependencies.copy()
        backend._anchored_operation = lambda *args: nullcontext()
        backend._carry_follow = lambda: world.update(position=initial['position'].copy())
        backend._placement_record = records.append
        backend._relocate_contents = lambda *args: None
        backend._surface_candidates = lambda *args: [
            {'index': i, 'pose': ([float(i+5), 0., 1.], [0.,0.,0.,1.]), 'clearance_m': .025}
            for i in range(2)]
        def step():
            physics.append(copy.deepcopy(world))
            world['neighbor'][0] += 1.  # Candidate collisions must not move other objects permanently.
        backend._placement_physics_step = step
        backend._placement_contacts = lambda *args: ['/obstacle/link']
        backend._step = lambda *args: self.fail('Rejected candidates must not advance task metrics')
        states = ModuleType('omnigibson.object_states')
        for name in ('OnTop', 'Touching', 'VerticalAdjacency'):
            setattr(states, name, type(name, (), {}))
        errors = ModuleType('omnigibson.action_primitives.action_primitive_set_base')
        errors.ActionPrimitiveError = RuntimeError
        with patch.dict(sys.modules, {'omnigibson.object_states': states,
                'omnigibson.action_primitives.action_primitive_set_base': errors}):
            with self.assertRaises(SkillError) as error:
                backend._try_place_on_top(held, SimpleNamespace(name='table'), 50, [0.,0.,1.])
        self.assertEqual(error.exception.code, 'sampling_error')
        self.assertEqual(len(physics), 2)
        self.assertEqual(world, initial)
        self.assertIs(backend._ideal_held, held)
        self.assertIs(backend._carry_relative, relation)
        self.assertEqual(backend._carry_dependencies, dependencies)
        self.assertEqual(len(loads), 2)  # Restore between trials, then outer transaction rollback.
        self.assertEqual([r['stage'] for r in records if r['status'] == 'candidate_rejected'],
                         ['raised_pose_collision', 'raised_pose_collision'])
        self.assertEqual(records[-1]['status'], 'rolled_back')


if __name__ == '__main__':
    unittest.main()
