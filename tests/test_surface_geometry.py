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

    def test_native_bowl_resting_pose_corrects_recorded_sideways_tilt(self):
        from scipy.spatial.transform import Rotation
        from manipulation_agent.executors.surface_geometry import native_resting_orientation
        # R502's frozen bowl starts on its side. Merely rotating about world Z
        # preserves that tilt and lets peas roll out after placement.
        current = self.np.array([.7035646439, -.0719344616, -.4848582447, .5145237446])
        original = Rotation.from_quat(current)
        quaternion, delta = native_resting_orientation(current, [])
        result = Rotation.from_quat(quaternion)
        self.np.testing.assert_allclose(result.apply([0., 0., 1.]), [0., 0., 1.], atol=1e-7)
        self.np.testing.assert_allclose(delta @ original.as_matrix(), result.as_matrix(), atol=1e-7)
        # The proposed root offset must use the reoriented real geometry.
        local = self.box_points([-.07, -.07, -.03], [.07, .07, .03])
        origin = self.np.array([7.6681, -1.9802, .5477])
        vertices = original.apply(local) + origin
        upright = (vertices-origin) @ delta.T + origin
        relative, anchor = self.rotated_surface_geometry(upright, origin, 90.)
        self.assertAlmostEqual(float(self.np.ptp(relative[:, 2])), .06)
        self.assertAlmostEqual(float(anchor[2]), -.03)
        # Already upright native poses remain unchanged.
        same, _ = native_resting_orientation(quaternion, [])
        self.assertAlmostEqual(abs(float(self.np.dot(same, quaternion))), 1.)

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
    def test_surface_ray_start_inside_collider_uses_higher_origin(self):
        import sys
        import numpy as np
        import torch
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        backend.torch = torch
        backend.og = SimpleNamespace(sim=SimpleNamespace(get_physics_dt=lambda: 1/120))
        backend.robot = SimpleNamespace(links={})
        held = SimpleNamespace(name='box', links={}, get_position_orientation=lambda:
            (torch.zeros(3), torch.tensor([0., 0., 0., 1.])))
        target = SimpleNamespace(name='oven', links={'door': SimpleNamespace(prim_path='/oven/door')})
        vertices = np.array([[x, y, z] for x in [-.12, .12] for y in [-.12, .12] for z in [0., .14]])
        backend._collision_vertices = lambda obj: vertices
        backend._placement_record = lambda record: None
        backend._open_rim_candidates = lambda *args, **kwargs: []
        origins = []
        def raytest(start, end, **kwargs):
            origins.append(float(start[2]))
            height = min(float(start[2]), .318)
            return dict(hit=True, position=[0., 0., height], normal=[0., 0., 1.],
                        distance=float(start[2])-height, rigidBody='/oven/door')
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils = ModuleType('transform_utils')
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        sampling.raytest = raytest
        with patch.dict(sys.modules, {'omnigibson.utils': utils,
                                      'omnigibson.utils.sampling_utils': sampling}):
            candidates = backend._surface_candidates(held, target, torch.tensor([0., 0., .2683]),
                                                     offsets=[(0., 0.)])
        self.assertEqual(len(origins), 2)
        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0]['support_position'][2], .318, places=6)
        self.assertGreater(float(candidates[0]['pose'][0][2]), .318)

    def test_missing_collision_geometry_has_explicit_error_without_box_fallback(self):
        from types import SimpleNamespace
        from manipulation_agent.contracts import SkillError
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        backend.og = SimpleNamespace(sim=SimpleNamespace(sync_physx_to_fabric=lambda: None))
        held = SimpleNamespace(links={'root': SimpleNamespace(collision_meshes={})})
        held.get_base_aligned_bbox = lambda: self.fail('Must not fall back to an inflated box')
        with self.assertRaises(SkillError) as error:
            backend._collision_vertices(held)
        self.assertEqual(error.exception.code, 'unsupported_geometry')

    def test_obstructed_candidates_restore_world_and_carry_without_advancing_metrics(self):
        import copy
        import sys
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
        backend._get_held = lambda: held
        backend._grasp_dependencies = lambda obj: []
        backend._carry_detach = lambda: None
        backend._placement_record = records.append
        backend._relocate_contents = lambda *args: None
        backend._surface_candidates = lambda *args: [
            {'index': i, 'pose': ([float(i+5), 0., 1.], [0.,0.,0.,1.]), 'clearance_m': .025}
            for i in range(2)]
        def fallback(*args):
            # Generate alternatives from the original scene, not a failed
            # collision trial. A repeated pose must not be simulated twice.
            self.assertEqual(world, initial)
            return [
                {'index': i, 'pose': ([x, 0., 1.], [0.,0.,0.,1.]), 'clearance_m': .025}
                for i, x in enumerate((5., 7.))]
        backend._surface_fallback_candidates = fallback
        def step():
            physics.append(copy.deepcopy(world))
            world['neighbor'][0] += 1.  # Candidate collisions must not move other objects permanently.
        backend._placement_physics_step = step
        backend._placement_contacts = lambda *args: ['/obstacle/link']
        backend._placement_contact_report = lambda *args, **kwargs: {'admissible': False, 'reason': 'deep_penetration'}
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
        self.assertEqual(len(physics), 3)
        self.assertEqual(world, initial)
        self.assertEqual(len(loads), 4)  # Two new trials, fallback generation, discard the final failed trial.
        self.assertEqual([r['stage'] for r in records if r['status'] == 'candidate_rejected'],
                         ['raised_pose_collision'] * 3)
        self.assertEqual(records[-1]['status'], 'rolled_back')


if __name__ == '__main__':
    unittest.main()
