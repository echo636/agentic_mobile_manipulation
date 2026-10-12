"""Particle identity, containment and pose regressions; no simulator required."""
import importlib.util
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.particle_payload import snapshot_particles, relocate_particles, verify_particles


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

    def test_failed_placement_restores_particle_carry_and_release_discards_it(self):
        from manipulation_agent.executors.carry import ControlledCarry
        backend = ControlledCarry()
        backend.ideal_carry = True
        backend.env = SimpleNamespace(scene=self.scene)
        self.scene.objects = [self.bag]
        backend._ideal_held = self.bag
        backend._carry_relative = None
        backend._carry_contents = []
        backend._carry_dependencies = []
        backend._carry_particles = snapshot_particles(self.scene, [self.bag])
        backend._anchored_operation = lambda *args: nullcontext()
        backend._placement_record = lambda record: None
        backend._carry_follow = lambda: relocate_particles(self.scene, backend._carry_particles)
        def load(state, **kwargs):
            self.raw.copy_(state[0]); self.pose[:] = state[1]
            # OG's macro serialized poses are sphere centers, whereas its
            # two-argument setter consumes raw frames. Model the restore drift.
            self.raw[:, 0] += .03
        backend.og = SimpleNamespace(sim=SimpleNamespace(scenes=[self.scene],
            dump_state=lambda **kwargs: (self.raw.clone(), [p.clone() for p in self.pose]), load_state=load))
        before = self.raw.clone()
        with self.assertRaises(ValueError), backend._placement_context():
            backend._carry_detach()
            self.pose[0] += 1
            backend._relocate_contents(self.bag, [])
            raise ValueError('reject candidate')
        self.assertIs(backend._ideal_held, self.bag)
        self.assertEqual(len(backend._carry_particles), 1)
        self.torch.testing.assert_close(self.raw, before)
        backend._carry_detach()
        self.assertFalse(backend._carry_particles)
        self.pose[0] += 3
        backend._relocate_contents(self.bag, [])
        self.torch.testing.assert_close(self.raw, before)


if __name__ == '__main__': unittest.main()
