"""Choose a usable open endpoint without forcing a door or lid into the room.

Private endpoint probes restore the scene and do not execute model actions.
Robot stance and departure remain the responsibility of local_articulation.
"""
import copy
import random

from ..contracts import SkillError


def checked_open_goal(backend, obj, check):
    from omnigibson.object_states import Open
    from .articulation_clearance import moving_link_clearance
    from .particle_payload import snapshot_particle_state, restore_particle_state, particle_state_unchanged
    import numpy as np

    sim=backend.og.sim
    state=copy.deepcopy(sim.dump_state(serialized=False))
    particles=snapshot_particle_state(sim)
    from .placement import scene_object_identities
    identities=scene_object_identities(sim)
    def restore():
        if scene_object_identities(sim)!=identities or not particle_state_unchanged(particles):
            raise SkillError('target_changed','Scene identities changed during articulation preview',changed=True)
        sim.load_state(copy.deepcopy(state),serialized=False)
        restore_particle_state(particles)
    rng=random.getstate();numpy_rng=np.random.get_state();torch_rng=backend.torch.random.get_rng_state()
    payload=backend._container_payload(obj)
    own={str(link.prim_path) for link in obj.links.values()}
    # Robot clearance is evaluated at prospective stances after this endpoint
    # is selected. Contents move with a drawer and may touch their own support.
    ignored=own|{str(link.prim_path) for item in [backend.robot,*[x[0] for x in payload]]
                 for link in item.links.values()}
    ignored.update(str(link.prim_path) for item in backend.env.scene.objects
                   for link in item.links.values() if link.is_meta_link)
    links=[link for link in obj.links.values() if not link.is_meta_link]
    initial=obj.get_joint_positions().clone()
    initial_poses={str(link.prim_path):tuple(v.clone() for v in link.get_position_orientation()) for link in links}
    trials=[]
    try:
        check()
        if not obj.states[Open].set_value(False,fully=True):
            raise SkillError('pre_condition_error','Cannot determine closed articulation endpoint')
        closed=obj.get_joint_positions().clone()
        closed_poses={str(link.prim_path):tuple(v.clone() for v in link.get_position_orientation()) for link in links}
        if not obj.states[Open].set_value(True,fully=True):
            raise SkillError('pre_condition_error','Cannot determine open articulation endpoint')
        opened=obj.get_joint_positions().clone()
        moving={str(link.prim_path) for link in links if any(
            not backend.torch.allclose(a,b,atol=1e-5,rtol=0.)
            for a,b in zip(closed_poses[str(link.prim_path)],link.get_position_orientation()))}
        # Prefer the largest useful opening. A maximal joint angle is not an
        # obligation: a lid behind a box can intersect a wall at that angle.
        fractions=(1.,.875,.75,.625,.5,.375,.25,.125)
        def candidates():
            # Preserve the existing maximum-common-opening preference. If one
            # independent leaf is blocked, try the other without moving it.
            for fraction in fractions:
                yield 'common',None,fraction,closed+(opened-closed)*fraction
            active=(abs(opened-closed)>1e-5).nonzero().flatten().cpu().tolist()
            if len(active)>1:
                for index in active:
                    for fraction in fractions:
                        goal=initial.clone()
                        goal[index]=closed[index]+(opened[index]-closed[index])*fraction
                        # An opening attempt must not close a leaf that is
                        # already more open than this candidate.
                        before=float((initial[index]-closed[index])/(opened[index]-closed[index]))
                        if fraction<=before+1e-6:continue
                        yield 'independent_leaf',index,fraction,goal
        for policy,index,fraction,goal in candidates():
            check()
            restore()
            obj.set_joint_positions(goal);obj.keep_still()
            candidate_moving=moving if policy=='common' else {
                str(link.prim_path) for link in links if any(
                    not backend.torch.allclose(a,b,atol=1e-5,rtol=0.)
                    for a,b in zip(initial_poses[str(link.prim_path)],link.get_position_orientation()))}
            backend._relocate_container_payload(payload)
            obj.states[Open].clear_cache()
            if not obj.states[Open].get_value():continue
            # Never integrate hypothetical overlapping endpoints. The actual
            # operation still checks collision clearance and physical settling.
            clearance=moving_link_clearance(backend,links,candidate_moving,ignored,check)
            trials.append(dict(policy=policy,joint_index=index,fraction=fraction,official_open=True,**clearance))
            if clearance['clear']:
                backend._placement_record(dict(status='articulation_open_endpoint_selected',target=obj.name,
                    goal_joints=goal.cpu().tolist(),opening_policy=policy,joint_index=index,fraction=fraction,trials=trials,
                    criterion='open_with_clear_moving_collision_enclosures_without_physics'))
                return goal
        backend._placement_record(dict(status='articulation_open_endpoint_unresolved',target=obj.name,trials=trials))
        raise SkillError('articulation_endpoint_unresolved','No checked usable opening at the current object pose; move the container or clear the door/lid space')
    finally:
        try:
            restore()
        finally:
            random.setstate(rng);np.random.set_state(numpy_rng);backend.torch.random.set_rng_state(torch_rng)
            backend.frames_revision=-1
