"""Check new contents against the actual closed door/lid collision geometry.

This is a private, rolled-back placement preview. It neither closes the live
container nor asks the model to choose a different object or task goal.
"""
import random


def closed_geometry_proposals(backend, target):
    """Compute closed-link bounds once, without advancing any physics.

    Bounds rank candidates only: concave links can have empty space inside an
    AABB, so an overlap is never a collision rejection certificate.
    """
    from omnigibson.object_states import Open
    from omnigibson.utils import transform_utils as T
    if Open not in target.states or not target.states[Open].get_value():
        return None
    joints = target.get_joint_positions().clone()
    rng = random.getstate()
    links = [link for link in target.links.values() if not link.is_meta_link]
    before = {str(link.prim_path): tuple(v.clone() for v in link.get_position_orientation())
              for link in links}
    volumes = [link for link in target.links.values() if link.is_meta_link and
               link.meta_link_type in {'fillable', 'openfillable'}]
    open_poses = {str(link.prim_path): tuple(v.clone() for v in link.get_position_orientation())
                  for link in volumes}
    try:
        if not target.states[Open].set_value(False, fully=True):
            return None
        bounds = []
        for link in links:
            after = link.get_position_orientation()
            if all(backend.torch.allclose(a, b, atol=1e-5, rtol=0.)
                   for a, b in zip(before[str(link.prim_path)], after)):
                continue
            points = link.collision_boundary_points_world
            if points is not None:
                bounds.append((points.amin(dim=0).cpu().numpy(), points.amax(dim=0).cpu().numpy()))
        transforms = {}
        for link in volumes:
            p0, q0 = open_poses[str(link.prim_path)]
            p1, q1 = link.get_position_orientation()
            rotation = T.quat2mat(q1) @ T.quat2mat(q0).T
            transforms[str(link.prim_path)] = (rotation.cpu().numpy(),
                (p1-rotation@p0).cpu().numpy())
        return bounds, transforms
    finally:
        target.set_joint_positions(joints)
        target.states[Open].clear_cache()
        target.state_updated()
        random.setstate(rng)


def closed_geometry_overlap(geometry, volume, center, extent):
    """Cheap proposal score in the future volume frame (including drawers)."""
    if geometry is None:
        return 0.
    import numpy as np
    bounds, transforms = geometry
    rotation, translation = transforms[str(volume.prim_path)]
    center = rotation @ np.asarray(center) + translation
    extent = abs(rotation) @ np.asarray(extent)
    low, high = center-extent/2, center+extent/2
    return sum(float(np.prod(np.maximum(0., np.minimum(high, b)-np.maximum(low, a))))
               for a, b in bounds) / max(float(np.prod(extent)), 1e-12)


def check_closure_clearance(backend, target, assembly):
    from omnigibson.object_states import Open
    from omnigibson.utils.usd_utils import RigidContactAPI
    from .particle_payload import snapshot_particle_state, restore_particle_state, particle_state_unchanged
    from .placement import scene_object_identities
    from ..contracts import SkillError
    import numpy as np

    if Open not in target.states or not target.states[Open].get_value():
        return {'applicable': False, 'clear': True}
    backend.deadline.check(changed=True)
    sim = backend.og.sim
    state = sim.dump_state(serialized=False)
    particles = snapshot_particle_state(sim)
    identities = scene_object_identities(sim)
    rng = random.getstate()
    numpy_rng = np.random.get_state()
    torch_rng = backend.torch.random.get_rng_state()
    links = [link for link in target.links.values() if not link.is_meta_link]
    poses = {str(link.prim_path): tuple(v.clone() for v in link.get_position_orientation())
             for link in links}
    payload = backend._container_payload(target)
    paths = {str(link.prim_path) for obj in assembly for link in obj.links.values()}
    try:
        if not target.states[Open].set_value(False, fully=True):
            return {'applicable': True, 'clear': False, 'reason': 'closed_state_setter_rejected'}
        moving = set()
        for link in links:
            before = poses[str(link.prim_path)]
            after = link.get_position_orientation()
            if not all(backend.torch.allclose(a, b, atol=1e-5, rtol=0.) for a, b in zip(before, after)):
                moving.add(str(link.prim_path))
        # Drawers carry their contents with the fillable link. Hinged lids do
        # not move the base cavity. Use the same link-relative rule as close.
        backend._relocate_container_payload(payload)
        target.keep_still()
        sim.step_physics()
        pairs = RigidContactAPI.get_contact_pairs(target.scene.idx, query_set=[target],
                                                  with_set=list(assembly), current_only=True)
        blocking = sorted((str(a), str(b)) for a, b in pairs
                          if (str(a) in moving and str(b) in paths)
                          or (str(b) in moving and str(a) in paths))
        return {'applicable': True, 'clear': not blocking,
                'moving_links': sorted(moving), 'blocking_contacts': blocking,
                'reason': 'new_contents_contact_closed_articulation' if blocking else None}
    finally:
        random.setstate(rng)
        np.random.set_state(numpy_rng)
        backend.torch.random.set_rng_state(torch_rng)
        backend.frames_revision = -1
        if scene_object_identities(sim) != identities or not particle_state_unchanged(particles):
            raise SkillError('target_changed','Scene identities changed during closure preview',changed=True)
        sim.load_state(state, serialized=False)
        restore_particle_state(particles)
