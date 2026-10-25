"""Restore only the objects changed by a no-physics articulation preview."""
import copy

from ..contracts import SkillError
from .placement import scene_object_identities


def preview_restore(backend, obj, payload, *, follows_carry=False):
    from .particle_payload import snapshot_particle_state, particle_state_unchanged, restore_particle_state
    sim = backend.og.sim
    identities = scene_object_identities(sim)
    changed = [obj, *[record[0] for record in payload]]
    particle_systems = set()
    if follows_carry:
        held = backend._get_held()
        if held is not None:
            changed.extend([held, *[item for item, _ in backend._carry_contents]])
            changed.extend(getattr(backend, '_carry_joint_positions', {}))
            particle_systems = {id(record.system) for record in getattr(backend, '_carry_particles', ())}
    # Native velocity dumps borrow tensor buffers. Freeze before any preview
    # setter can change them, and give each restore its own copy.
    objects = {id(item): item for item in changed}.values()
    states = [(item, copy.deepcopy(item.dump_state(serialized=False))) for item in objects]
    particles = snapshot_particle_state(sim)
    moved_particles = [record for record in particles if id(record[1]) in particle_systems]

    def restore():
        if scene_object_identities(sim) != identities or not particle_state_unchanged(particles):
            raise SkillError('target_changed', 'Scene identities changed during articulation preview', changed=True)
        # No physics or transitions run in this preview. Reloading the entire
        # scene needlessly rewrites unrelated articulations, controllers and
        # physical particle instancers, including their native solver state.
        for item, state in states:
            item.load_state(copy.deepcopy(state), serialized=False)
        restore_particle_state(moved_particles)

    return restore
