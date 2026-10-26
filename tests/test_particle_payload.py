"""Particle identity, containment and pose regressions; no simulator required."""
import importlib.util
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.particle_payload import snapshot_particles, relocate_particles, verify_particles
from manipulation_agent.executors.particle_payload import _particle_supports, particle_count
from manipulation_agent.executors.particle_payload import particle_bounds, particles_in_volumes


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('scipy'), 'Requires geometry environment')
class ParticlePayloadTests(unittest.TestCase):
    def setUp(self):
        import torch
        from scipy.spatial.transform import Rotation
        self.torch = torch
        transform = ModuleType('omnigibson.utils.transform_utils')
        transform.quat2mat = lambda q: torch.tensor(Rotation.from_quat(q.numpy()).as_matrix(), dtype=q.dtype)
        transform.quat_conjugate = lambda q: q * torch.tensor([-1., -1., -1., 1.])
        transform.quat_multiply = lambda q, other: torch.tensor(
            (Rotation.from_quat(q.numpy()) * Rotation.from_quat(other.T.numpy())).as_quat(), dtype=q.dtype).T
        state = ModuleType('omnigibson.object_states')
        state.ContainedParticles = self.state_key = type('ContainedParticles', (), {})
        modules = {'omnigibson': ModuleType('omnigibson'), 'omnigibson.utils': ModuleType('omnigibson.utils'),
                   'omnigibson.utils.transform_utils': transform, 'omnigibson.object_states': state}
        self.override = patch.dict(sys.modules, modules)
        self.override.start(); self.addCleanup(self.override.stop)
        self.transform = transform
        self.pose = [torch.zeros(3), torch.tensor([0., 0., 0., 1.])]
        self.link = SimpleNamespace(get_position_orientation=lambda: self.pose)
        self.link.check_points_in_volume = lambda points: (((points-self.pose[0]) @ transform.quat2mat(self.pose[1])).abs() < .5).all(dim=1)
        self.bag = SimpleNamespace(states={self.state_key: SimpleNamespace(link=self.link)})
        # Nonzero mesh-to-sphere-center offset catches the OG macro setter trap.
        self.raw = torch.tensor([[.1, .2, .15, 0., 0., 0., 1.], [2., 0., 0., 0., 0., 0., 1.]])
        self.velocities = torch.ones((2, 6))
        self.view = SimpleNamespace(prim_paths=['/particle/0', '/particle/1'],
            get_transforms=lambda: self.raw, get_velocities=lambda: self.velocities,
            set_transforms=lambda values, indices: self.raw.copy_(values),
            set_velocities=lambda values, indices: self.velocities.copy_(values))
        def poses():
            centers = self.raw[:, :3] + (transform.quat2mat(self.raw[:, 3:]) @ torch.tensor([.03, 0., 0.]))
            return centers, self.raw[:, 3:]
        self.system = SimpleNamespace(name='material', particles_view=self.view, n_particles=2, get_particles_position_orientation=poses)
        self.scene = SimpleNamespace(active_systems={'material': self.system})

    def surface_scene(self):
        torch = self.torch
        self.link.prim_path = '/board/base'
        self.link.is_meta_link = False
        self.bag.states = {}
        self.bag.links = {'base': self.link}
        self.bag.aabb = (torch.tensor([-.5, -.5, .08]), torch.tensor([.5, .5, .1]))
        # Bottom grain, stacked grain, table grain, grain below board, floating
        # grain, grain on another tray, and side contact (not support).
        self.raw = torch.tensor([[0., 0., .13, 0., 0., 0., 1.],
                                 [0., 0., .18, 0., 0., 0., 1.],
                                 [.7, 0., .03, 0., 0., 0., 1.],
                                 [0., 0., .03, 0., 0., 0., 1.],
                                 [.2, 0., .3, 0., 0., 0., 1.],
                                 [.3, 0., .15, 0., 0., 0., 1.],
                                 [.51, 0., .09, 0., 0., 0., 1.]])
        self.velocities = torch.ones((len(self.raw), 6))
        self.view.prim_paths = [f'/particle/{i}' for i in range(len(self.raw))]
        self.system.n_particles = len(self.raw)
        self.system.particle_contact_radius = .03
        self.supports = {'/particle/0': '/board/base', '/particle/1': '/particle/0',
                         '/particle/2': '/table/base', '/particle/3': '/table/base',
                         '/particle/4': None, '/particle/5': '/other_tray/base', '/particle/6': None}
        self.support_patch = patch('manipulation_agent.executors.particle_payload._particle_supports',
            side_effect=lambda system, path, center: ((self.supports[path], center.new_zeros(3),
                center.new_tensor([0., 0., -.035])),) if self.supports[path] else ())
        self.support_patch.start(); self.addCleanup(self.support_patch.stop)
        check = patch('manipulation_agent.executors.particle_payload._support_at',
                      side_effect=lambda path, start, vector: self.supports[path])
        check.start(); self.addCleanup(check.stop)

    def test_particle_envelope_uses_captured_sphere_centers_and_radii(self):
        self.system.particle_contact_radius = .02
        payload = snapshot_particles(self.scene, [self.bag])
        low, high = particle_bounds(self.scene, payload)[0]
        self.torch.testing.assert_close(low, self.torch.tensor([.11, .18, .13]))
        self.torch.testing.assert_close(high, self.torch.tensor([.15, .22, .17]))
        # The uncaptured second grain must not enlarge this assembly.
        self.assertLess(float(high[0]), 1.)
        self.view.prim_paths[0] = '/replacement'
        with self.assertRaises(SkillError):
            particle_bounds(self.scene, payload)

    def test_stationary_vessel_does_not_ride_outer_door_but_moving_drawer_does(self):
        from manipulation_agent.executors.particle_payload import particle_link_motion
        particles = snapshot_particles(self.scene, [self.bag])
        moved = particle_link_motion(particles)
        self.assertEqual(moved(), [])
        # A serializer may flip quaternion signs without moving the vessel.
        self.pose[1] *= -1
        self.assertEqual(moved(), [])
        before = self.raw.clone()
        self.pose[0][0] += .3
        transferred = moved()
        self.assertEqual(len(transferred), 1)
        relocate_particles(self.scene, transferred)
        self.torch.testing.assert_close(self.raw[0, :3], before[0, :3] + self.torch.tensor([.3, 0., 0.]))
        self.torch.testing.assert_close(self.raw[1], before[1])
        verify_particles(self.scene, transferred)

    def test_bag_support_does_not_substitute_for_destination_containment(self):
        self.surface_scene()
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        verify_particles(self.scene, payload)
        low_ceiling = SimpleNamespace(check_points_in_volume=lambda points: points[:, 2] < .16)
        self.assertFalse(particles_in_volumes(self.scene, payload, [low_ceiling]))
        enough_room = SimpleNamespace(check_points_in_volume=lambda points: points[:, 2] < .25)
        self.assertTrue(particles_in_volumes(self.scene, payload, [enough_room]))
        self.assertFalse(particles_in_volumes(self.scene, payload, []))

    def test_surface_support_pile_excludes_table_below_floating_and_other_tray(self):
        self.surface_scene()
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        self.assertEqual(payload[0].paths, ('/particle/0', '/particle/1'))
        self.assertEqual(particle_count(payload, 'supported'), 2)
        self.assertEqual(particle_count(payload, 'contained'), 0)
        self.assertEqual(snapshot_particles(self.scene, [self.bag]), [])
        before = self.raw.clone()
        self.pose[:] = [self.torch.tensor([3., 4., 1.]), self.torch.tensor([0., 0., 2**-.5, 2**-.5])]
        relocate_particles(self.scene, payload)
        self.torch.testing.assert_close(self.raw[:2, :3], self.torch.tensor([[3., 4., 1.13], [3., 4., 1.18]]))
        self.torch.testing.assert_close(self.raw[2:], before[2:])
        verify_particles(self.scene, payload)
        self.supports['/particle/1'] = '/table/base'
        with self.assertRaises(SkillError) as error: verify_particles(self.scene, payload)
        self.assertEqual(error.exception.code, 'postcondition_error')

    def test_surface_particles_remain_identifiable_after_reordering_or_deletion(self):
        self.surface_scene()
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        self.view.prim_paths.reverse(); self.raw = self.raw.flip(0); self.velocities = self.velocities.flip(0)
        self.pose[0] += self.torch.tensor([1., 0., 0.])
        relocate_particles(self.scene, payload)
        self.assertAlmostEqual(float(self.raw[-1, 0]), 1.)
        self.assertAlmostEqual(float(self.raw[-2, 0]), 1.)
        before = self.raw.clone()
        self.view.prim_paths[-2] = '/particle/replacement'
        with self.assertRaises(SkillError) as error: relocate_particles(self.scene, payload)
        self.assertEqual(error.exception.code, 'target_changed')
        self.torch.testing.assert_close(self.raw, before)

    def test_current_owned_contacts_can_recertify_missed_saved_probes(self):
        self.surface_scene()
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        before = self.raw.clone()
        # A different support chain can be valid, but must still reach the
        # captured board. Neither new identities nor pose writes are allowed.
        self.supports.update({'/particle/0': '/particle/1', '/particle/1': '/board/base'})
        with patch('manipulation_agent.executors.particle_payload._support_at', return_value=None):
            verify_particles(self.scene, payload)
        self.torch.testing.assert_close(self.raw, before)

    def test_current_support_cannot_escape_through_foreign_or_uncaptured_particles(self):
        self.surface_scene()
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        for foreign in ('/table/base', '/particle/2', '/particle/new', None):
            with self.subTest(foreign=foreign):
                self.supports['/particle/0'] = foreign
                with self.assertRaises(SkillError):
                    verify_particles(self.scene, payload)

    def test_current_support_cycle_has_no_valid_rigid_anchor(self):
        self.surface_scene()
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        self.supports.update({'/particle/0': '/particle/1', '/particle/1': '/particle/0'})
        with self.assertRaises(SkillError):
            verify_particles(self.scene, payload)

    def test_surface_stack_can_span_material_systems_without_duplicate_container_claim(self):
        self.surface_scene()
        # This grain is above the existing pile, but belongs to another system.
        second = SimpleNamespace(name='other', particle_contact_radius=.03, n_particles=1,
            particles_view=SimpleNamespace(prim_paths=['/other/0'],
                get_transforms=lambda: self.torch.tensor([[0., 0., .23, 0., 0., 0., 1.]])),
            get_particles_position_orientation=lambda: (self.torch.tensor([[0., 0., .23]]), None))
        self.scene.active_systems['other'] = second
        self.supports['/other/0'] = '/particle/1'
        payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        self.assertEqual(particle_count(payload), 3)
        self.assertEqual(payload[1].paths, ('/other/0',))
        # A nested annotated container has first claim over these same grains.
        child = SimpleNamespace(states={self.state_key: SimpleNamespace(link=self.link)}, links={}, aabb=self.bag.aabb)
        payload = snapshot_particles(self.scene, [self.bag, child], include_supported=True)
        self.assertEqual(particle_count(payload, 'supported'), 0)
        self.assertTrue(all(record.container is child for record in payload))

    def test_support_ray_uses_contact_distance_and_nearest_occluder(self):
        contact = ModuleType('omnigibson.object_states.contact_particles')
        contact.m = SimpleNamespace(CONTACT_TOLERANCE=.005)
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        center = self.torch.tensor([0., 0., .13])
        self.system.particle_contact_radius = .03
        def ray(start, end, **kwargs):
            self.assertLessEqual(float(self.torch.linalg.norm(end-center)), .035001)
            self.assertEqual(kwargs, {'ignore_bodies': ('/particle/0',), 'ignore_collisions': ('/particle/0',)})
            return {'hit': True, 'normal': [0., 0., 1.], 'rigidBody': '/other_tray/base'}
        sampling.raytest = ray
        with patch.dict(sys.modules, {'omnigibson.object_states.contact_particles': contact,
                                     'omnigibson.utils.sampling_utils': sampling}):
            self.assertEqual([s[0] for s in _particle_supports(self.system, '/particle/0', center)], ['/other_tray/base'])
            sampling.raytest = lambda *a, **k: {'hit': True, 'normal': [1., 0., 0.], 'rigidBody': '/board/base'}
            self.assertEqual(_particle_supports(self.system, '/particle/0', center), ())
            sampling.raytest = lambda *a, **k: {'hit': False}
            self.assertEqual(_particle_supports(self.system, '/particle/0', center), ())
            self.system.particle_contact_radius = float('nan')
            self.assertEqual(_particle_supports(self.system, '/particle/0', center), ())

    def test_off_center_contact_probe_rotates_with_board_at_arbitrary_yaw(self):
        self.surface_scene()
        # Keep graph setup, then exercise the real saved-probe transformation.
        offset = self.torch.tensor([.0075, 0., 0.])
        direction = self.torch.tensor([0., 0., -.013])
        with patch('manipulation_agent.executors.particle_payload._particle_supports',
                   side_effect=lambda system, path, center: (('/board/base', offset, direction),)
                   if path == '/particle/0' else ()):
            payload = snapshot_particles(self.scene, [self.bag], include_supported=True)
        from scipy.spatial.transform import Rotation
        self.pose[:] = [self.torch.tensor([1., 2., 3.]),
                        self.torch.tensor(Rotation.from_euler('z', 23, degrees=True).as_quat(), dtype=offset.dtype)]
        relocate_particles(self.scene, payload)
        expected_offset = offset @ self.transform.quat2mat(self.pose[1]).T
        center = self.system.get_particles_position_orientation()[0][0]
        def check(path, start, vector):
            self.torch.testing.assert_close(start, center + expected_offset)
            self.torch.testing.assert_close(vector, direction)
            return '/board/base'
        with patch('manipulation_agent.executors.particle_payload._support_at', side_effect=check):
            verify_particles(self.scene, payload)

    def test_support_ray_finds_off_center_contact_when_center_misses(self):
        contact = ModuleType('omnigibson.object_states.contact_particles')
        contact.m = SimpleNamespace(CONTACT_TOLERANCE=.005)
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        self.system.particle_contact_radius = .01
        center = self.torch.tensor([0., 0., .02])
        sampling.raytest = lambda start, end, **kwargs: (
            {'hit': True, 'normal': [0., 0., 1.], 'rigidBody': '/particle/lower'}
            if float(start[0]) > .006 else {'hit': False})
        with patch.dict(sys.modules, {'omnigibson.object_states.contact_particles': contact,
                                     'omnigibson.utils.sampling_utils': sampling}):
            found = _particle_supports(self.system, '/particle/0', center)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][0], '/particle/lower')
            self.assertAlmostEqual(float(found[0][1][0]), .0075)

    def test_translate_rotate_preserves_only_contained_particles_and_offset(self):
        torch = self.torch
        payload = snapshot_particles(self.scene, [self.bag])
        self.assertEqual(payload[0].paths, ('/particle/0',))
        untouched = self.raw[1].clone()
        self.pose[:] = [torch.tensor([3., 4., 1.]), torch.tensor([0., 0., 2**-.5, 2**-.5])]
        relocate_particles(self.scene, payload)
        torch.testing.assert_close(self.raw[0, :3], torch.tensor([2.8, 4.1, 1.15]))
        torch.testing.assert_close(self.raw[0, 3:], self.pose[1])
        torch.testing.assert_close(self.raw[1], untouched)
        torch.testing.assert_close(self.velocities[0], torch.zeros(6))
        torch.testing.assert_close(self.velocities[1], torch.ones(6))
        verify_particles(self.scene, payload)

    def test_particle_reordering_tracks_paths_not_stale_indices(self):
        payload = snapshot_particles(self.scene, [self.bag])
        self.view.prim_paths.reverse()
        self.raw = self.raw.flip(0)
        self.pose[0] += self.torch.tensor([1., 0., 0.])
        relocate_particles(self.scene, payload)
        self.assertAlmostEqual(float(self.raw[1, 0]), 1.1, places=6)
        self.assertEqual(float(self.raw[0, 0]), 2.)

    def test_removed_or_replaced_particles_are_never_recreated(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                payload = snapshot_particles(self.scene, [self.bag])
                before = self.raw.clone()
                if replacement: self.scene.active_systems['material'] = object()
                else: self.view.prim_paths[0] = '/particle/new'
                with self.assertRaises(SkillError) as error:relocate_particles(self.scene, payload)
                self.assertEqual(error.exception.code, 'target_changed')
                self.torch.testing.assert_close(self.raw, before)
                self.scene.active_systems['material'] = self.system
                self.view.prim_paths[0] = '/particle/0'

    def test_nested_container_claims_particle_once_and_verification_detects_spill(self):
        outer = SimpleNamespace(states=self.bag.states)
        payload = snapshot_particles(self.scene, [outer, self.bag])
        self.assertEqual(len(payload), 1)
        self.assertIs(payload[0].container, self.bag)
        self.raw[0, 0] = 8.
        with self.assertRaises(SkillError) as error: verify_particles(self.scene, payload)
        self.assertEqual(error.exception.code, 'postcondition_error')





if __name__ == '__main__': unittest.main()
