"""Conservative clearance for the ideal carry protocol's optional initial lift.

An in-place attachment is valid in this protocol. It does not certify an
extraction path; subsequent navigation and placement retain their own checks.
"""
import math


def choose_lift(bounds, blocked):
    """Query swept world AABBs without changing any object or particle pose.

    Exclude only the bottom 2 mm of each original bound, where resting support
    is expected. The rest of the complete vertical sweep must be clear. Bounds
    overapproximate geometry: ambiguous clearance selects a smaller lift, or
    an in-place attachment, rather than moving through an enclosure ceiling.
    Query failures propagate; a missing collision result is never clearance.
    """
    boxes = [(tuple(map(float, low)), tuple(map(float, high))) for low, high in bounds]
    if not boxes or any(len(low) != 3 or len(high) != 3
            or any(not math.isfinite(v) for v in (*low, *high))
            or any(a >= b for a, b in zip(low, high)) for low, high in boxes):
        raise ValueError('Carry clearance requires finite nonempty volume bounds')
    for lift in (.18, .09, .045, .02, .01):
        clear = True
        for low, high in boxes:
            start = (low[0], low[1], min(low[2] + .002, high[2]))
            end = (high[0], high[1], high[2] + lift + .002)
            if blocked(start, end):
                clear = False
                break
        if clear:
            return lift
    return 0.


def assembly_lift(backend, obj, contents, particles):
    """Use live PhysX overlap queries for rigid and captured particle bounds."""
    import omnigibson.lazy as lazy
    from .particle_payload import _indices

    assembly = (obj, *(child for child, _ in contents))
    bounds = [(low.tolist(), high.tolist()) for body in assembly for low, high in [body.aabb]]
    ignored = {link.prim_path for body in assembly for link in body.links.values()}
    ignored.update(link.prim_path for body in backend.env.scene.objects
                   for link in body.links.values() if link.is_meta_link)
    for record in particles:
        indices = _indices(backend.env.scene, record)
        centers = record.system.get_particles_position_orientation()[0][indices]
        radius = float(record.system.particle_contact_radius)
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError('Invalid carried particle collision radius')
        bounds.append(((centers.amin(dim=0)-radius).tolist(), (centers.amax(dim=0)+radius).tolist()))
        ignored.update(record.paths)
    query = lazy.omni.physx.get_physx_scene_query_interface()
    rejected = []

    def blocked(low, high):
        deadline = getattr(backend, 'deadline', None)
        if deadline is not None:
            deadline.check()
        hits = set()
        errors = []

        def callback(hit):
            try:
                path = str(hit.rigid_body)
                if path not in ignored:
                    hits.add(path)
            except Exception as exc:
                errors.append(exc)
                return False
            return True

        query.overlap_box(tuple((b-a)/2 for a, b in zip(low, high)),
                          tuple((a+b)/2 for a, b in zip(low, high)),
                          (0., 0., 0., 1.), callback, False)
        if errors:
            raise RuntimeError('Carry clearance overlap callback failed') from errors[0]
        if hits:
            rejected.append(dict(low=low, high=high, bodies=sorted(hits)))
        return bool(hits)

    lift = choose_lift(bounds, blocked)
    backend._carry_record(event='initial_lift_clearance', object=obj.name,
        lift_m=lift, bounds=bounds, rejected_sweeps=rejected,
        method='live_PhysX_conservative_swept_AABBs',
        in_place_attachment=lift == 0., extraction_path_certified=False)
    return lift
