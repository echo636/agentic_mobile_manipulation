"""Choose a usable open endpoint without forcing a door or lid into the room.

Private endpoint probes restore edited objects and do not execute model actions.
Local manipulation handles robot stance and post-operation departure.
"""
import random

from ..contracts import SkillError


def checked_open_goal(backend, obj, check):
    from omnigibson.object_states import Open
    from .articulation_clearance import moving_link_clearance
    from .articulation_snapshot import preview_restore
    import numpy as np

    payload=backend._container_payload(obj)
    restore=preview_restore(backend,obj,payload)
    rng=random.getstate();numpy_rng=np.random.get_state();torch_rng=backend.torch.random.get_rng_state()
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
        active=(abs(opened-closed)>1e-5).nonzero().flatten().cpu().tolist()
        binding=getattr(getattr(backend,'_local_operation',None),'binding',None)
        anchor=getattr(binding,'anchor',None) if getattr(binding,'obj',None) is obj else None
        selected=[]
        if anchor is not None and any(anchor is link for link in links) and str(anchor.prim_path) in moving:
            # A click on a door (including its rigidly attached glass/handle)
            # must not also pull out unrelated shelves or drawers. Discover
            # its native joint ancestry by kinematic probes, without physics
            # or assumptions about asset names, joint order or joint type.
            for index in active:
                check()
                probe=closed.clone();probe[index]=opened[index]
                obj.set_joint_positions(probe)
                if any(not backend.torch.allclose(a,b,atol=1e-5,rtol=0.)
                       for a,b in zip(closed_poses[str(anchor.prim_path)],anchor.get_position_orientation())):
                    selected.append(index)
        # Prefer the largest useful opening. A maximal joint angle is not an
        # obligation: a lid behind a box can intersect a wall at that angle.
        fractions=(1.,.875,.75,.625,.5,.375,.25,.125)
        def candidates():
            if selected:
                for fraction in fractions:
                    goal=initial.clone()
                    for index in selected:
                        before=float((initial[index]-closed[index])/(opened[index]-closed[index]))
                        goal[index]=closed[index]+(opened[index]-closed[index])*max(fraction,before)
                    yield 'selected_link',selected[0] if len(selected)==1 else None,fraction,goal
                # A blocked selected leaf is not permission to open another.
                return
            # Preserve the existing maximum-common-opening preference. If one
            # independent leaf is blocked, try the other without moving it.
            for fraction in fractions:
                yield 'common',None,fraction,closed+(opened-closed)*fraction
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
            # Reopening the selected fully open leaf yields the identical
            # joint state. There is no swept geometry to certify; requiring a
            # queried collision piece would reject every repeated open call.
            # Keep missing/disabled geometry rejection for any actual motion.
            unchanged=(fraction==1. and not candidate_moving
                       and backend.torch.equal(goal,initial))
            clearance=(dict(clear=True,method='unchanged_open_joint_state',
                            queried_collision_pieces=0,overlapping_enclosures=[],
                            missing_collision_geometry=[])
                       if unchanged else
                       moving_link_clearance(backend,links,candidate_moving,ignored,check))
            trials.append(dict(policy=policy,joint_index=index,fraction=fraction,official_open=True,**clearance))
            if clearance['clear']:
                backend._placement_record(dict(status='articulation_open_endpoint_selected',target=obj.name,
                    goal_joints=goal.cpu().tolist(),opening_policy=policy,joint_index=index,fraction=fraction,trials=trials,
                    selected_link=str(anchor.prim_path) if selected else None,selected_joint_indices=selected,
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
