"""Uniform pose/identity access for physical macro particles and micro fluids.

OG's micro arrays have no persistent particle IDs. Track their append/delete
operations on the particular instancer, including un-serialized state snapshots.
An index reused after removal must never identify an old carried particle.
No upstream class or particle physics settings are modified.
"""
from itertools import count

from ..contracts import SkillError

_tokens = count()


class MicroParticleView:
    def __init__(self, instancer):
        self.instancer = instancer
        self.paths = self._new_ids(instancer.n_particles)
        add, remove = instancer.add_particles, instancer.remove_particles
        dump, restore = instancer._dump_state, instancer._load_state

        def append(positions, *args, **kwargs):
            before = self.prim_paths
            result = add(positions, *args, **kwargs)
            self.paths = [*before, *self._new_ids(len(positions))]
            return result

        def delete(idxs):
            import torch
            before = self.prim_paths
            deleted = set(torch.as_tensor(idxs).flatten().tolist())
            result = remove(idxs)
            self.paths = [path for i, path in enumerate(before) if i not in deleted]
            return result

        def snapshot():
            return {**dump(), '_controlled_particle_ids': list(self.prim_paths)}

        def load(state):
            result = restore(state)
            saved = state.get('_controlled_particle_ids')
            self.paths = (list(saved) if saved is not None and len(saved) == instancer.n_particles
                          else self._new_ids(instancer.n_particles))
            return result

        instancer.add_particles = append
        instancer.remove_particles = delete
        instancer._dump_state = snapshot
        instancer._load_state = load

    def _new_ids(self, n):
        return [f'{self.instancer.prim_path}#controlled-{next(_tokens)}' for _ in range(n)]

    @property
    def prim_paths(self):
        if len(self.paths) != self.instancer.n_particles:
            # An untracked array replacement cannot preserve claimed identity.
            self.paths = self._new_ids(self.instancer.n_particles)
        return tuple(self.paths)

    def get_transforms(self):
        import torch
        return torch.cat((self.instancer.particle_positions,
                          self.instancer.particle_orientations), dim=1)

    def get_velocities(self):
        return self.instancer.particle_velocities

    def set_transforms(self, transforms, indices):
        current = self.get_transforms()
        current[indices] = transforms
        self.instancer.particle_positions = current[:, :3]
        self.instancer.particle_orientations = current[:, 3:]

    def set_velocities(self, velocities, indices):
        current = self.get_velocities().clone()
        current[indices] = velocities
        self.instancer.particle_velocities = current


def physical_particle_view(system):
    view = getattr(system, 'particles_view', None)
    if view is not None:
        return view
    instancers = getattr(system, 'particle_instancers', None)
    if not instancers or not system.n_particles:
        return None
    # OG's own pose/containment API addresses only the default instancer.
    # Refuse mixed-index interpretation if an unusual system uses several.
    instancer = system.default_particle_instancer
    if instancer.n_particles != system.n_particles:
        raise SkillError('unsupported_relation', 'Multiple physical particle instancers are not supported')
    view = getattr(instancer, '_controlled_particle_view', None)
    if view is None:
        view = MicroParticleView(instancer)
        instancer._controlled_particle_view = view
    return view
