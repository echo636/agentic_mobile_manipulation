"""Keep native particle contact queries scoped to the requested rigid body."""
from functools import wraps


def install_particle_contact_identity():
    import omnigibson as og
    from omnigibson.object_states.contact_particles import ContactParticles, m

    original = ContactParticles._get_value
    if not getattr(original, '_mas_exact_contact_body', False):
        @wraps(original)
        def get_value(state, system, link=None):
            if link is None:
                return original(state, system)
            # Native OG compares only the final name, e.g. "base_link". A
            # placemat then mistakes contact with a mug for its own contact
            # and absorbs water through the mug. Retain native tolerances and
            # overlap queries; identify the complete requested rigid body.
            lower, upper = link.visual_aabb
            margin = system.particle_radius + m.CONTACT_AABB_TOLERANCE
            positions = system.get_particles_position_orientation()[0]
            inbound = ((lower - margin < positions) & (positions < upper + margin)).all(dim=-1)
            radius = system.particle_contact_radius + m.CONTACT_TOLERANCE
            contacts = set()

            def report_hit(hit):
                if hit.rigid_body == link.prim_path:
                    contacts.add(index)
                    return False
                return True

            for index in inbound.nonzero().flatten().tolist():
                og.sim.psqi.overlap_sphere(radius, positions[index].cpu().numpy(), report_hit, False)
            return contacts

        get_value._mas_exact_contact_body = True
        ContactParticles._get_value = get_value
    return {'status': 'configured', 'scope': 'native_particle_contact_queries_with_explicit_link',
            'change': 'Match complete rigid-body path instead of shared link basename',
            'contact_tolerances_absorption_capacity_and_physics': 'unchanged'}
