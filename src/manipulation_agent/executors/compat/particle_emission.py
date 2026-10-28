"""Prevent native raycast dispensers from spawning inside their own body."""
from functools import wraps
import json


def install_particle_emission_clearance(output):
    import omnigibson as og
    from omnigibson.object_states.particle_modifier import ParticleApplier
    from omnigibson.systems.micro_particle_system import MicroPhysicalParticleSystem

    original = ParticleApplier._apply_particles_at_raycast_hits
    if not getattr(original, '_mas_emitter_clearance', False):
        @wraps(original)
        def apply(state, system, hits, scales=None):
            if not isinstance(system, MicroPhysicalParticleSystem) or not hits:
                return original(state, system, hits, scales=scales)
            # Native ray sampling intentionally ignores the emitter so it can
            # see the destination. On a supporting surface, however, its padded
            # hit can lie inside the emitter's base. Spawning there makes the
            # contact solver launch an otherwise stable dispenser.
            paths = {str(link.prim_path) for link in state.obj.links.values()
                     if not link.is_meta_link and not getattr(link, 'visual_only', False)}
            radius = float(system.particle_contact_radius)
            accepted = []
            blocked_by = set()
            for index, hit in enumerate(hits):
                blocked = False

                def contact(row):
                    nonlocal blocked
                    path = str(row.rigid_body)
                    if path in paths:
                        blocked = True
                        blocked_by.add(path)
                        return False
                    return True

                # Micro particles physically collide as spheres, so this uses
                # their native contact radius and actual PhysX body colliders.
                og.sim.psqi.overlap_sphere(radius, hit[0].tolist(), contact, False)
                if not blocked:
                    accepted.append(index)
            if len(accepted) != len(hits):
                with (output / 'particle_emission_clearance.jsonl').open('a') as stream:
                    stream.write(json.dumps({'source': state.obj.name, 'material': system.name,
                        'tested': len(hits), 'accepted': len(accepted),
                        'blocked_by_emitter': len(hits)-len(accepted),
                        'contact_radius_m': radius, 'colliders': sorted(blocked_by)})+'\n')
            # Pass the filtered hits to native generation so ModifiedParticles
            # charges only material actually emitted. No existing particles,
            # switches, authored conditions or task predicates are modified.
            selected_scales = None if scales is None else [scales[i] for i in accepted]
            return original(state, system, [hits[i] for i in accepted], scales=selected_scales)

        apply._mas_emitter_clearance = True
        ParticleApplier._apply_particles_at_raycast_hits = apply
    return {'status': 'configured', 'scope': 'native_micro_particle_raycast_emission',
            'change': 'Reject proposed particles intersecting the emitter physical collider',
            'switches_conditions_existing_particles_and_task_predicates': 'unchanged'}
