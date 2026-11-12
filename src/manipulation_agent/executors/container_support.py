"""Resolve articulated contents to the physical support that moves them."""


def closed_container(obj, objects, *, exclude=()):
    """Find a closed parent whose geometry actually covers the target.

    Inside alone also includes exposed sink contents in an overlapping cabinet
    volume. A single downward ray distinguishes that open top from a lid or
    shelf. This is a read-only enclosure check, not an extraction planner.
    """
    from omnigibson.object_states import Inside, Open

    if Inside not in obj.states:
        return None
    for parent in objects:
        if (parent is obj or any(parent is item for item in exclude)
                or Open not in getattr(parent, 'states', {})
                or parent.states[Open].get_value() or not obj.states[Inside].get_value(parent)):
            continue
        from omnigibson.utils.sampling_utils import raytest
        low, high = obj.aabb
        center = (low + high) / 2
        above = center.clone()
        above[2] = max(float(high[2]), float(parent.aabb[1][2])) + .01
        bodies = {str(link.prim_path) for link in parent.links.values() if not link.is_meta_link}
        if any(hit['rigidBody'] in bodies
               for hit in raytest(above, center, only_closest=False)):
            return parent
    return None


def contacting_support(backend, child, objects):
    """Return one unambiguous upward support, using contacts and lower rays.

    A fillable volume can belong to an oven's stationary base while its shelf
    slides. Conversely a side contact with the door must not carry the food.
    Ambiguous or absent physical support leaves the caller's volume fallback.
    """
    from omnigibson.utils.sampling_utils import raytest
    from .placement_contacts import assembly_contact_pairs
    from .surface_geometry import bottom_support_points

    candidates = {str(link.prim_path): (obj, link) for obj in objects if obj is not child
                  for link in obj.links.values() if not link.is_meta_link}
    own = {str(link.prim_path) for link in child.links.values()}
    pairs = assembly_contact_pairs(child)
    contacts = {str(b) if str(a) in own else str(a) for a, b in pairs
                if str(a) in own or str(b) in own}
    if not contacts.intersection(candidates):
        return None
    ignored = [*own, *(str(link.prim_path) for link in backend.robot.links.values())]
    supports = set()
    for point in bottom_support_points(backend._collision_vertices(child)):
        hit = raytest((point + [0., 0., .03]).tolist(),
                      (point - [0., 0., .04]).tolist(), ignore_bodies=ignored)
        body = str(hit.get('rigidBody'))
        if not hit.get('hit') or body not in contacts or body not in candidates:
            continue
        gap = float(point[2] - hit['position'][2])
        if float(hit['normal'][2]) >= .9 and -.01 <= gap <= .015:
            supports.add(body)
    return candidates[next(iter(supports))] if len(supports) == 1 else None


def support_frames(backend, container, contents):
    """Resolve nested food/plate support to the container's actual shelf link."""
    objects = [container, *contents]
    supports = {child: contacting_support(backend, child, objects) for child in contents}
    frames = {}
    for child in contents:
        current, visited = child, set()
        while current not in visited:
            visited.add(current)
            support = supports.get(current)
            if support is None:
                break
            owner, link = support
            if owner is container:
                frames[child] = link
                break
            current = owner
    return frames
