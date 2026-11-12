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

    def test_initial_surface_rays_cover_thin_rim_and_exclude_vertical_faces(self):
        from manipulation_agent.executors.surface_geometry import upward_surface_points
        # Neither the interior nor 5%-inset AABB grid hits this narrow rim.
        rim = self.np.array([[[.97, -.2, .1], [.99, -.2, .1], [.99, .2, .1]],
                             [[-.99, -.2, .1], [-.97, -.2, .1], [-.97, .2, .1]]])
        wall = self.np.array([[[1., -.2, 0.], [1., .2, 0.], [1., .2, .1]]])
        points = upward_surface_points(self.np.concatenate([rim, wall]))
        self.assertEqual(len(points), 2)
        self.assertTrue(self.np.all(self.np.abs(points[:, 0]) > .97))
        self.np.testing.assert_allclose(points[:, 2], .1)

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

    def test_curled_cloth_centers_footprint_instead_of_its_low_edge(self):
        # R909: a slightly warped 30 cm cloth had its low edge 13 cm from
        # its center. Aligning that edge near a table rim put its center off it.
        origin = self.np.array([2., 3., 1.])
        local = self.np.array([[-.15, -.15, -.02], [.15, -.15, -.02],
                              [-.15, .15, .02], [.15, .15, .02]])
        relative, old_anchor = self.rotated_surface_geometry(local + origin, origin)
        relative, anchor = self.rotated_surface_geometry(local + origin, origin,
                                                        center_footprint=True)
        support = self.np.array([0., .30, .75])
        old_landing = relative + support - old_anchor
        landing = relative + support - anchor
        self.assertGreater(float(old_landing[:, 1].mean()), .40)  # Beyond table rim.
        self.np.testing.assert_allclose(landing[:, :2].mean(axis=0), support[:2])
        self.assertAlmostEqual(float(landing[:, 2].min()), support[2])

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

    def test_adjacent_footprints_do_not_overlap_for_wide_cloth_and_offset_root(self):
        from manipulation_agent.executors.surface_geometry import adjacent_support_points
        relative = self.box_points([.1, -.2, -.01], [.4, .1, .01])
        reference = self.box_points([2., 3., .8], [2.3, 3.3, .82])
        anchor = self.bottom_anchor(relative)
        selected = self.np.array([2.15, 3.15, .82])
        points = adjacent_support_points(relative, anchor, reference, selected)
        self.assertEqual(len(points), 4)
        for xy in points:
            low = relative.min(0)[:2] + xy - anchor[:2]
            high = relative.max(0)[:2] + xy - anchor[:2]
            gap = self.np.maximum(low, reference.min(0)[:2]) - self.np.minimum(high, reference.max(0)[:2])
            self.assertGreater(float(gap.max()), 0.)
            self.assertLessEqual(float(gap.max()), .010001)
            self.assertLess(float(gap.min()), 0.)

    def test_no_requested_yaw_preserves_held_world_orientation(self):
        # The vertices already reflect a held object tilted in world space.
        origin = self.np.array([2., 3., 4.])
        local = self.np.array([[-.12, -.03, -.02], [.12, -.03, .02],
                              [-.12, .03, .01], [.12, .03, .05]])
        rotated, anchor = self.rotated_surface_geometry(local+origin, origin)
        self.np.testing.assert_allclose(rotated, local, atol=1e-7)
        self.assertAlmostEqual(float(anchor[2]), -.02)

    def test_flip_uses_new_bottom_and_applies_yaw_after_roll(self):
        from scipy.spatial.transform import Rotation
        origin = self.np.array([7., -4., 2.])
        local = self.box_points([.1, -.2, -.05], [.3, .1, .02])
        rotated, anchor = self.rotated_surface_geometry(local + origin, origin, 90., 180.)
        expected = Rotation.from_euler('xyz', [180., 0., 90.], degrees=True).apply(local)
        self.np.testing.assert_allclose(rotated, expected, atol=1e-8)
        self.assertAlmostEqual(float(anchor[2]), -.02)
        landed = rotated + self.np.array([1., 2., .6]) - anchor
        self.assertAlmostEqual(float(landed[:, 2].min()), .6)

    def test_authored_resting_pose_corrects_sideways_tilt(self):
        from scipy.spatial.transform import Rotation
        from manipulation_agent.executors.surface_geometry import native_resting_orientation
        # An explicitly authored upright pose can correct a sideways tilt.
        current = self.np.array([.7035646439, -.0719344616, -.4848582447, .5145237446])
        original = Rotation.from_quat(current)
        annotations = {'upright': {'rotation': [0., 0., 0., 1.], 'prob': 1.}}
        quaternion, delta = native_resting_orientation(current, annotations)
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

    def test_unannotated_loaded_tray_preserves_its_support_side(self):
        from scipy.spatial.transform import Rotation
        from manipulation_agent.executors.surface_geometry import native_resting_orientation
        # Native carry reproduction: this model's supporting side faces local
        # -Z. Identity would turn the cup underneath the tray during release.
        current = self.np.array([.2750862241, .9613850117, -.0078428742, .0022020815])
        payload = self.np.array([.0010135174, .0000240803, -.0437791348])
        quaternion, delta = native_resting_orientation(current, {})
        before = Rotation.from_quat(current).apply(payload)
        after = Rotation.from_quat(quaternion).apply(payload)
        self.assertGreater(after[2], .04)
        self.np.testing.assert_allclose(after, before, atol=1e-8)
        self.np.testing.assert_allclose(delta, self.np.eye(3), atol=1e-8)

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
    def test_tilted_board_release_centers_its_authored_resting_base(self):
        import sys
        import numpy as np
        import torch
        from scipy.spatial.transform import Rotation
        from types import SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        backend.torch = torch
        backend.og = SimpleNamespace(sim=SimpleNamespace(get_physics_dt=lambda: 1/120))
        backend.robot = SimpleNamespace(links={})
        tilt = Rotation.from_euler('xyz', [2., 2., 139.], degrees=True)
        origin = torch.tensor([1., 2., 1.])
        shape = np.array([[x, y, z] for x in (-.2, .2) for y in (-.14, .14)
                          for z in (-.01, .01)])
        held = SimpleNamespace(name='board', links={}, abilities={},
            orientations={'flat': {'rotation': [0., 0., 0., 1.], 'prob': 1.}},
            get_position_orientation=lambda: (origin, torch.tensor(tilt.as_quat())))
        target = SimpleNamespace(name='counter', links={
            'base': SimpleNamespace(prim_path='/counter/base')})
        backend._collision_vertices = lambda obj: tilt.apply(shape) + origin.numpy()
        backend._placement_record = lambda row: None
        backend._open_rim_candidates = lambda *args, **kwargs: []
        point = torch.tensor([0., 0., .87])
        def raytest(start, end, **kwargs):
            return dict(hit=True, position=point.tolist(), normal=[0., 0., 1.],
                        distance=float(start[2]-point[2]), rigidBody='/counter/base')
        with patch.dict(sys.modules, {
                'omnigibson.utils': SimpleNamespace(transform_utils=SimpleNamespace()),
                'omnigibson.utils.sampling_utils': SimpleNamespace(raytest=raytest)}):
            candidate = backend._surface_candidates(held, target, point, offsets=[(0., 0.)])[0]
        position, quaternion = candidate['pose']
        landed = Rotation.from_quat(quaternion.numpy()).apply(shape) + position.numpy()
        np.testing.assert_allclose(landed[:, :2].mean(axis=0), point[:2].numpy(), atol=1e-6)
        self.assertAlmostEqual(float(np.ptp(landed[:, 2])), .02, places=6)
        self.assertGreater(float(landed[:, 2].min()), float(point[2]))

    def test_shallow_rim_click_proposes_inward_placement_and_keeps_floor_clicks(self):
        import sys
        import numpy as np
        import torch
        from types import SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        backend.torch = torch
        backend.robot = SimpleNamespace(links={})
        backend._placement_record = lambda row: None
        low, high = torch.tensor([-1., -1., 0.]), torch.tensor([1., 1., .03])
        volume = SimpleNamespace(is_meta_link=True, meta_link_type='openfillable',
            prim_path='/tray/volume', visual_aabb=(low, high),
            check_points_in_volume=lambda points: ((points >= low) & (points <= high)).all(dim=1))
        target = SimpleNamespace(name='tray', links={'volume': volume,
            'base': SimpleNamespace(prim_path='/tray/base', is_meta_link=False)})
        held = SimpleNamespace(name='item', links={})
        vertices = np.array([[x, y, 0.] for x in (-.05, .05) for y in (-.03, .03)])

        def raytest(start, end, **kwargs):
            x, y, z = map(float, start)
            if max(abs(x), abs(y)) > 1.04 or float(end[2]) >= z:
                return {'hit': False}
            height = .03 if max(abs(x), abs(y)) >= 1. else 0.
            if not float(end[2]) <= height <= z:
                return {'hit': False}
            return dict(hit=True, rigidBody='/tray/base', position=[x, y, height],
                        normal=[0., 0., 1.], distance=z-height)

        sampling = SimpleNamespace(raytest=raytest)
        with patch.dict(sys.modules, {'omnigibson.utils.sampling_utils': sampling}):
            for point, offsets, rim in (([1.01, 0., .03], [(-.04, 0.)], True),
                                        ([.5, 0., 0.], [(0., 0.)], False)):
                with self.subTest(rim=rim):
                    candidates = backend._open_rim_candidates(held, target, torch.tensor(point),
                        vertices, np.zeros(3), torch.tensor([0., 0., 0., 1.]),
                        offsets, (), .002, start_index=0)
                    self.assertTrue(candidates)
                    self.assertEqual(candidates[0]['selected_rim_surface'], rim)
                    self.assertLess(float(candidates[0]['pose'][0][0]), 1.)

    def test_offline_native_portable_support_keeps_valid_on_top(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.initial_surface import native_initial_surface_proposals
        calls = []
        def native(*args, **kwargs):
            calls.append(kwargs)
            return True
        on_top = SimpleNamespace(sample_kinematics=native)
        utils = SimpleNamespace(get_reachability_sampling_context=lambda *args: {},
            is_pose_reachable_for_predicate=lambda *args: False)
        child = SimpleNamespace(get_position_orientation=lambda: ([0., 0., 0.], None))
        with patch.dict(sys.modules, {'omnigibson.object_states': SimpleNamespace(on_top=on_top),
                'omnigibson.utils.constants': SimpleNamespace(GROUND_CATEGORIES={'floors'}),
                'omnigibson.utils.object_state_utils': utils}):
            with native_initial_surface_proposals():
                self.assertTrue(on_top.sample_kinematics('onTop', child,
                    SimpleNamespace(category='casserole', fixed_base=False), use_trav_map=True))
                self.assertFalse(on_top.sample_kinematics('onTop', child,
                    SimpleNamespace(category='table', fixed_base=True), use_trav_map=True))
        self.assertFalse(calls[0]['use_trav_map'])
        self.assertTrue(calls[1]['use_trav_map'])
        self.assertIs(on_top.sample_kinematics, native)

    def test_side_click_projects_only_when_receiver_has_one_support_level(self):
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
        held = SimpleNamespace(name='lamp', links={}, get_position_orientation=lambda:
            (torch.zeros(3), torch.tensor([0., 0., 0., 1.])))
        target = SimpleNamespace(name='support', links={'base': SimpleNamespace(prim_path='/support/base')})
        backend._collision_vertices = lambda obj: np.array([
            [x, y, z] for x in [-.05, .05] for y in [-.05, .05] for z in [0., .2]])
        backend._placement_record = lambda record: None
        backend._open_rim_candidates = lambda *args, **kwargs: []
        def raytest(start, end, **kwargs):
            z = min(float(start[2]), .6)
            return dict(hit=True, position=[0., 0., z], normal=[0., 0., 1.],
                distance=float(start[2])-z, rigidBody='/support/base')
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils = ModuleType('transform_utils')
        sampling = ModuleType('omnigibson.utils.sampling_utils'); sampling.raytest = raytest
        for heights in ((.6,), (.6, .9)):
            with self.subTest(heights=heights), patch.dict(sys.modules,
                    {'omnigibson.utils': utils, 'omnigibson.utils.sampling_utils': sampling}):
                backend._collision_triangles = lambda obj: np.array([
                    [[-.5, -.5, z], [.5, -.5, z], [.5, .5, z]] for z in heights])
                candidates = backend._surface_candidates(held, target,
                    torch.tensor([0., 0., .48755]), offsets=[(0., 0.)])
            if len(heights) == 1:
                self.assertEqual(len(candidates), 1)
                self.assertAlmostEqual(float(candidates[0]['verification_point'][2]), .6, places=6)
            else:
                self.assertEqual(candidates, [])

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
        from types import SimpleNamespace, ModuleType
        from unittest.mock import patch
        from manipulation_agent.contracts import SkillError
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        backend.og = SimpleNamespace(sim=SimpleNamespace(sync_physx_to_fabric=lambda: None))
        held = SimpleNamespace(prim_type='rigid', links={'root': SimpleNamespace(collision_meshes={})})
        held.get_base_aligned_bbox = lambda: self.fail('Must not fall back to an inflated box')
        constants = ModuleType('omnigibson.utils.constants')
        constants.PrimType = SimpleNamespace(CLOTH='cloth')
        with patch.dict('sys.modules', {'omnigibson.utils.constants': constants}), self.assertRaises(SkillError) as error:
            backend._collision_vertices(held)
        self.assertEqual(error.exception.code, 'unsupported_geometry')

    def test_surface_release_failure_keeps_physical_result_without_retry_or_rollback(self):
        import torch
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.contracts import SkillError
        from manipulation_agent.executors.placement import CheckedPlacement
        backend = CheckedPlacement()
        world = {'position': torch.tensor([0., 0., 1.]), 'neighbor': 3.}
        released, physics, env_steps, records = [], [], [], []
        states = ModuleType('omnigibson.object_states')
        states.OnTop = type('OnTop', (), {})
        held = SimpleNamespace(name='pan', states={states.OnTop: SimpleNamespace(get_value=lambda _: False)})
        held.get_position_orientation = lambda: (world['position'], torch.tensor([0., 0., 0., 1.]))
        def set_pose(position, orientation):
            released.append(position.tolist())
            world['position'] = position.clone()
        held.set_position_orientation = set_pose
        held.keep_still = held.wake = lambda: None
        backend.og = SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: .03))
        backend._get_held = lambda: None
        backend._carry_contents = []
        backend._carry_detach = lambda: None
        backend._placement_record = records.append
        backend._relocate_contents = lambda *args: None
        backend._surface_candidates = lambda *args, **kwargs: [
            {'index': i, 'pose': (torch.tensor([float(i+5), 0., 1.]), torch.tensor([0., 0., 0., 1.]))}
            for i in range(2)]
        backend._inside_pose_overlap_ranker = lambda *a, **kw: lambda p, q: float(p[0]) == 5.
        backend._surface_fallback_candidates = lambda *a, **kw: self.fail('A clear geometric candidate already exists')
        def settle(*args, **kwargs):
            physics.append(True)
            world['neighbor'] += 1.
            world['position'][2] = 0.  # The one actual release falls off the support.
        backend._settle_surface_contacts = settle
        backend._placement_contacts = lambda *args: []
        backend._selected_surface_support = lambda *a, **kw: (False, {'checks': {'selected_shelf_height': False}})
        backend._step = lambda *_: env_steps.append(True)
        backend.primitives = SimpleNamespace(_empty_action=lambda: None)
        target = SimpleNamespace(name='table', links={})
        with patch.dict(sys.modules, {'omnigibson.object_states': states}):
            with self.assertRaises(SkillError) as error:
                backend._try_place_on_top(held, target, 50, torch.tensor([0., 0., 1.]))
            self.assertEqual(error.exception.code, 'postcondition_error')
            self.assertTrue(error.exception.changed)
            self.assertEqual(released, [[6., 0., 1.]])
            self.assertEqual(world['position'].tolist(), [6., 0., 0.])
            self.assertEqual(world['neighbor'], 4.)
            self.assertEqual(len(physics), 1)
            self.assertEqual(len(env_steps), 1)
            # No geometric support is a pre-release error and must not drop the load.
            backend._surface_candidates = backend._surface_fallback_candidates = lambda *a, **kw: []
            with self.assertRaises(SkillError) as error:
                backend._try_place_on_top(held, target, 50, torch.tensor([0., 0., 1.]))
            self.assertEqual(error.exception.code, 'placement_surface_unavailable')
            self.assertEqual(len(released), 1)


if __name__ == '__main__':
    unittest.main()
