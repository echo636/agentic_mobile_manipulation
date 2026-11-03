"""Refresh native physics after explicit object replacement in the pinned OG adapter."""
import copy


def _restore_cut_state(sim, snapshot):
    """Load native state, then finish the kinematics of saved attachments.

    AttachedTo's loader may recreate a joint. Its alignment moves the child
    and clears both bodies' velocities, after their kinematics were loaded.
    Finish those bodies once, after all relationships exist. This does not
    align, strengthen or hold joints during subsequent physics.
    """
    from omnigibson.prims.entity_prim import EntityPrim

    sim.load_state(snapshot, serialized=False)
    for index, scene in enumerate(sim.scenes):
        states = snapshot[index]['registry']['object_registry']
        attached = set()
        for name, state in states.items():
            parent_uuid = state.get('non_kin', {}).get('AttachedTo', {}).get('attached_obj_uuid', -1)
            if parent_uuid != -1:
                attached.add(scene.object_registry('name', name))
                attached.add(scene.object_registry('uuid', parent_uuid))
        for obj in attached:
            EntityPrim._load_state(obj, states[obj.name])
            obj.clear_states_cache()


def rebuild_cut_topology(backend):
    """Initialize replacements and rebuild views, preserving the post-cut world.

    The pinned GPU simulator can retain invalid views after remove/add, failing
    on its next physics step. A native stop/play rebuild fixes that boundary,
    but resets clocks and advances startup physics. Restore the complete state
    and clocks before any counted action step. Never retry a failed CUDA step.
    This is scoped to explicit cutting, outside the simulator's update loop.
    """
    sim = backend.og.sim
    pending = list(getattr(sim, '_objects_to_initialize', ()))
    if not pending:
        return
    from omnigibson.macros import gm
    import omnigibson.lazy as lazy

    # Same pending-object initialization block as OG's _non_physics_step,
    # without running object-state updates or other transition rules early.
    sim.render()
    modified = set()
    for obj in pending:
        obj.initialize()
        modified.add(obj.scene)
        if obj.states.keys() & sim.object_state_types_on_joint_break:
            sim._objects_require_joint_break_callback = True
        obj.keep_still()
    sim._objects_to_initialize = sim._objects_to_initialize[len(pending):]
    sim.update_handles()
    for scene in modified:
        scene.transition_rule_api.refresh_all_rules()

    snapshot = copy.deepcopy(sim.dump_state(serialized=False))
    context = sim._sim_context
    clock = float(sim.current_time)
    native_steps = int(sim.current_time_step_index)
    timeline_time = float(context._timeline.get_current_time())
    control_steps = backend.steps
    enabled_states, enabled_transitions = gm.ENABLE_OBJECT_STATES, gm.ENABLE_TRANSITION_RULES
    capture_on_play = lazy.carb.settings.get_settings().get_as_bool('/omni/replicator/captureOnPlay')
    record = {'status': 'running', 'operation': 'cut_native_topology_rebuild',
              'objects': [obj.name for obj in pending], 'env_step': control_steps,
              'clock': clock, 'native_steps': native_steps, 'timeline': timeline_time,
              'audience': 'executor_private'}
    backend._placement_record(record)
    try:
        try:
            # This stop/play refreshes PhysX, not the camera capture session.
            # Replicator's automatic STOP -> PLAY handling can finish its old
            # capture shutdown during sim.play() and stop the timeline again.
            lazy.omni.replicator.core.orchestrator.set_capture_on_play(False)
            with gm.unlocked():
                gm.ENABLE_OBJECT_STATES = False
                gm.ENABLE_TRANSITION_RULES = False
            sim.stop()
            sim.play()
            _restore_cut_state(sim, copy.deepcopy(snapshot))
            # Loading scene state can render while restoring native resources.
            # Restore all clocks after that work, including the Kit timeline.
            context._timeline.set_current_time(timeline_time)
            context._timeline.commit()
            context._current_time = clock
            context._number_of_steps = native_steps
        finally:
            lazy.omni.replicator.core.orchestrator.set_capture_on_play(capture_on_play)
            with gm.unlocked():
                gm.ENABLE_OBJECT_STATES = enabled_states
                gm.ENABLE_TRANSITION_RULES = enabled_transitions
        record['clock_after'] = float(sim.current_time)
        record['native_steps_after'] = int(sim.current_time_step_index)
        record['timeline_after'] = float(context._timeline.get_current_time())
        record['env_step_after'] = backend.steps
    except BaseException:
        backend._placement_record({**record, 'status': 'failed'})
        raise
    backend.frames_revision = -1
    backend._placement_record({**record, 'status': 'passed'})
