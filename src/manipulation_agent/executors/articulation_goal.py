"""Choose a usable open endpoint without forcing a door or lid into the room.

Private endpoint probes restore the scene and do not execute model actions.
Robot stance and departure remain the responsibility of local_articulation.
"""
import copy
import random

from ..contracts import SkillError


def checked_open_goal(backend, obj, check):
    from omnigibson.object_states import Open
    from omnigibson.utils.usd_utils import RigidContactAPI
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
    links=[link for link in obj.links.values() if not link.is_meta_link]
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
        for fraction in (1.,.875,.75,.625,.5,.375,.25,.125):
            check()
            restore()
            goal=closed+(opened-closed)*fraction
            obj.set_joint_positions(goal);obj.keep_still()
            backend._relocate_container_payload(payload)
            obj.states[Open].clear_cache()
            if not obj.states[Open].get_value():continue
            origin=obj.get_position_orientation()[0].clone()
            blocking=set()
            # Use one real control interval without resetting the target root
            # between substeps. Per-substep anchoring hides impact impulses.
            count=max(1,round(sim.get_sim_step_dt()/sim.get_physics_dt()))
            for _ in range(count):
                check();sim.step_physics()
                pairs=RigidContactAPI.get_contact_pairs(obj.scene.idx,query_set=[obj],with_set=None,current_only=True)
                blocking.update((str(a),str(b)) for a,b in pairs if
                    (str(a) in moving and str(b) not in ignored) or
                    (str(b) in moving and str(a) not in ignored))
            displacement=float(backend.torch.linalg.norm(obj.get_position_orientation()[0]-origin))
            obj.states[Open].clear_cache()
            usable=bool(obj.states[Open].get_value())
            clear=not blocking and usable and (backend._get_held() is obj or displacement<=.005)
            trials.append(dict(fraction=fraction,blocking_contacts=sorted(blocking),
                               root_displacement_m=displacement,official_open=usable,clear=clear))
            if clear:
                backend._placement_record(dict(status='articulation_open_endpoint_selected',target=obj.name,
                    goal_joints=goal.cpu().tolist(),fraction=fraction,trials=trials,
                    criterion='open_with_no_external_moving_link_contact_or_root_impulse'))
                return goal
        backend._placement_record(dict(status='articulation_open_endpoint_unresolved',target=obj.name,trials=trials))
        raise SkillError('articulation_endpoint_unresolved','No checked usable opening at the current object pose; move the container or clear the door/lid space')
    finally:
        try:
            restore()
        finally:
            random.setstate(rng);np.random.set_state(numpy_rng);backend.torch.random.set_rng_state(torch_rng)
            backend.frames_revision=-1
