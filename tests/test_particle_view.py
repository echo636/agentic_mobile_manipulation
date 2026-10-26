"""Protect fluid identity across removal, append and failed-action rollback."""
import unittest
from types import SimpleNamespace

import torch

from manipulation_agent.executors.particle_view import physical_particle_view
from manipulation_agent.executors.material_transfer import move_particle_centers


class Instancer:
    prim_path = '/fluid/instancer'

    def __init__(self):
        self.particle_positions = torch.tensor([[0., 0., 0.], [1., 0., 0.]])
        self.particle_orientations = torch.tensor([[0., 0., 0., 1.]] * 2)
        self.particle_velocities = torch.ones(2, 3)

    @property
    def n_particles(self):
        return len(self.particle_positions)

    def add_particles(self, positions):
        self.particle_positions = torch.cat((self.particle_positions, positions))
        self.particle_orientations = torch.cat((self.particle_orientations,
            torch.tensor([[0., 0., 0., 1.]] * len(positions))))
        self.particle_velocities = torch.cat((self.particle_velocities, torch.zeros(len(positions), 3)))

    def remove_particles(self, idxs):
        keep = [i for i in range(self.n_particles) if i not in torch.as_tensor(idxs).flatten()]
        self.particle_positions = self.particle_positions[keep]
        self.particle_orientations = self.particle_orientations[keep]
        self.particle_velocities = self.particle_velocities[keep]

    def _dump_state(self):
        return {name: getattr(self, name).clone() for name in
                ('particle_positions', 'particle_orientations', 'particle_velocities')}

    def _load_state(self, state):
        for name in ('particle_positions', 'particle_orientations', 'particle_velocities'):
            setattr(self, name, state[name].clone())


class ParticleViewTests(unittest.TestCase):
    def setUp(self):
        self.inst = Instancer()
        class System:
            @property
            def n_particles(inner):
                return self.inst.n_particles
        self.system = System()
        self.system.particle_instancers = {'one': self.inst}
        self.system.default_particle_instancer = self.inst
        self.system.get_particles_position_orientation = lambda: (
            self.inst.particle_positions, self.inst.particle_orientations)
        self.view = physical_particle_view(self.system)

    def test_delete_and_append_same_count_never_reuses_captured_identity(self):
        before = self.view.prim_paths
        self.inst.remove_particles(torch.tensor([[0]]))
        self.inst.add_particles(torch.tensor([[2., 0., 0.]]))
        self.assertEqual(self.system.n_particles, 2)
        self.assertEqual(self.view.prim_paths[0], before[1])
        self.assertNotIn(before[0], self.view.prim_paths)

    def test_unserialized_snapshot_restores_identity_and_positions(self):
        before = self.view.prim_paths
        state = self.inst._dump_state()
        self.inst.remove_particles([0])
        self.inst._load_state(state)
        self.assertEqual(self.view.prim_paths, before)
        torch.testing.assert_close(self.inst.particle_positions, state['particle_positions'])

    def test_selected_transfer_preserves_quantity_other_particles_and_rotation(self):
        paths = self.view.prim_paths
        orientation = self.inst.particle_orientations.clone()
        move_particle_centers(self.system, [1], torch.tensor([[3., 2., 1.]]))
        self.assertEqual(paths, self.view.prim_paths)
        torch.testing.assert_close(self.inst.particle_positions[0], torch.zeros(3))
        torch.testing.assert_close(self.inst.particle_positions[1], torch.tensor([3., 2., 1.]))
        torch.testing.assert_close(self.inst.particle_velocities[0], torch.ones(3))
        torch.testing.assert_close(self.inst.particle_velocities[1], torch.zeros(3))
        torch.testing.assert_close(orientation, self.inst.particle_orientations)

    def test_unknown_state_replacement_invalidates_old_identity(self):
        before = set(self.view.prim_paths)
        state = self.inst._dump_state()
        state.pop('_controlled_particle_ids')
        self.inst._load_state(state)
        self.assertFalse(before.intersection(self.view.prim_paths))

    def test_empty_macro_system_does_not_expose_destroyed_backend(self):
        class ExpiredView:
            @property
            def prim_paths(self):
                raise AttributeError("'NoneType' object has no attribute 'prim_paths'")
        system=SimpleNamespace(n_particles=0,particles_view=ExpiredView())
        self.assertIsNone(physical_particle_view(system))
        # Regeneration is an actual new view, never the former particle IDs.
        system.n_particles=1
        system.particles_view=SimpleNamespace(prim_paths=('/new/material/0',))
        self.assertEqual(physical_particle_view(system).prim_paths,('/new/material/0',))
