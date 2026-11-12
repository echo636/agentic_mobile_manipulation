"""Initialize native cutting replacements without restarting the running world."""


def rebuild_cut_topology(backend):
    """Use OG's pending-object initialization and handle refresh once.

    Existing bodies, attachments and clocks remain in the running simulation.
    Stop/play additionally resets robots and interacts with camera capture;
    neither a restart nor a whole-scene reload is needed for this refresh.
    """
    sim = backend.og.sim
    pending = list(getattr(sim, '_objects_to_initialize', ()))
    if not pending:
        return
    record = {'operation': 'cut_native_topology_rebuild',
              'objects': [obj.name for obj in pending], 'env_step': backend.steps,
              'scene_restarted': False, 'audience': 'executor_private'}
    backend._placement_record({**record, 'status': 'running'})
    # Native pending-object initialization, outside the object-state update loop.
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
    backend.frames_revision = -1
    backend._placement_record({**record, 'status': 'passed'})
