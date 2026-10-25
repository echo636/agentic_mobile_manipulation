"""Preserve physical centers through native-style snapshot and partial updates."""
import importlib.util
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


from manipulation_agent.executors.macro_particle_pose import set_particle_center, set_particle_centers


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('scipy'), 'Requires geometry environment')
class MacroParticlePoseTests(unittest.TestCase):
    def setUp(self):
        import torch
        from scipy.spatial.transform import Rotation
        self.torch = torch
        transform = ModuleType('omnigibson.utils.transform_utils')
        transform.quat2mat = lambda q: torch.as_tensor(
            Rotation.from_quat(q.numpy()).as_matrix(), dtype=q.dtype)
        modules = {'omnigibson': ModuleType('omnigibson'),
                   'omnigibson.utils': ModuleType('omnigibson.utils'),
                   'omnigibson.utils.transform_utils': transform}
        override = patch.dict(sys.modules, modules)
        override.start()
        self.addCleanup(override.stop)
        self.raw = torch.tensor([[1., 2., 3., 0., 0., 0., 1.],
                                 [4., 5., 6., 0., 0., 1., 0.]])
        offset = torch.tensor([.02, -.03, .01])
        def pose():
            return (self.raw[:, :3] + transform.quat2mat(self.raw[:, 3:]) @ offset,
                    self.raw[:, 3:])
        def write(values, indices):
            self.raw[indices] = values
        self.system = SimpleNamespace(n_particles=2, _particle_offset=offset,
            get_particles_position_orientation=pose,
            get_particle_position_orientation=lambda idx: tuple(x[idx] for x in pose()),
            particles_view=SimpleNamespace(set_transforms=write))

    def test_repeated_snapshot_restore_has_no_center_drift(self):
        torch = self.torch
        before = self.raw.clone()
        for _ in range(8):
            centers, quaternions = (x.clone() for x in self.system.get_particles_position_orientation())
            set_particle_centers(self.system, centers, quaternions)
        torch.testing.assert_close(self.raw, before, atol=1e-6, rtol=0)

    def test_batch_rotation_preserves_centers_and_translation_preserves_rotation(self):
        torch = self.torch
        centers, rotations = (x.clone() for x in self.system.get_particles_position_orientation())
        new_rotations = rotations.flip(0)
        set_particle_centers(self.system, orientations=new_rotations)
        actual, quats = self.system.get_particles_position_orientation()
        torch.testing.assert_close(actual, centers)
        torch.testing.assert_close(quats, new_rotations)
        set_particle_centers(self.system, positions=centers + 1)
        actual, quats = self.system.get_particles_position_orientation()
        torch.testing.assert_close(actual, centers + 1)
        torch.testing.assert_close(quats, new_rotations)

    def test_single_updates_preserve_other_particle_and_omitted_component(self):
        torch = self.torch
        untouched = self.raw[0].clone()
        center, orientation = (x[1].clone() for x in self.system.get_particles_position_orientation())
        for arguments in [dict(position=center, orientation=orientation),
                          dict(orientation=torch.tensor([0., 0., 0., 1.])),
                          dict(position=center + 1)]:
            previous = tuple(x[1].clone() for x in self.system.get_particles_position_orientation())
            set_particle_center(self.system, 1, **arguments)
            after = tuple(x[1] for x in self.system.get_particles_position_orientation())
            torch.testing.assert_close(after[0], arguments.get('position', previous[0]))
            torch.testing.assert_close(after[1], arguments.get('orientation', previous[1]))
            torch.testing.assert_close(self.raw[0], untouched)
