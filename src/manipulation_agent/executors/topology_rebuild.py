"""Refresh native physics after explicit object replacement in the pinned OG adapter."""
import copy
import math


def _state_mismatches(left, right, torch, path='state'):
    import numpy as np

    if isinstance(left, dict):
        if not isinstance(right, dict) or set(left) != set(right):
            return [path + ':keys']
        return [item for key in left
                for item in _state_mismatches(left[key], right[key], torch, path+'/'+str(key))]
    if isinstance(left, (list, tuple)):
        if not isinstance(right, (list, tuple)) or len(left) != len(right):
            return [path + ':length']
        return [item for i, (x, y) in enumerate(zip(left, right))
                for item in _state_mismatches(x, y, torch, path+'/'+str(i))]
    if isinstance(left, torch.Tensor):
        equal = (isinstance(right, torch.Tensor) and left.shape == right.shape
                 and torch.allclose(left, right, rtol=0., atol=1e-5, equal_nan=False))
    elif isinstance(left, np.ndarray):
        equal = (isinstance(right, np.ndarray) and left.shape == right.shape
                 and np.allclose(left, right, rtol=0., atol=1e-5, equal_nan=False))
    elif isinstance(left, float):
        # Native MaxTemperature starts at -inf until its first state update.
        # New cut products are snapshotted before that update. Two identical
        # sentinels are preserved state, not a changed temperature. Keep every
        # other nonfinite scalar (including NaN and +inf) rejected.
        initial_temperature = (isinstance(right, (int, float))
                               and path.endswith('/MaxTemperature/max_temperature')
                               and left == right == -math.inf)
        equal = initial_temperature or (isinstance(right, (int, float))
                and math.isfinite(left) and abs(left-right) <= 1e-5)
    else:
        equal = left == right
    return [] if equal else [path]


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
    callbacks = dict(context._physics_callback_functions)
    control_steps = backend.steps
    identities = tuple((scene, tuple(scene.objects)) for scene in sim.scenes)
    enabled_states, enabled_transitions = gm.ENABLE_OBJECT_STATES, gm.ENABLE_TRANSITION_RULES
    record = {'status': 'running', 'operation': 'cut_native_topology_rebuild',
              'objects': [obj.name for obj in pending], 'env_step': control_steps,
              'clock': clock, 'native_steps': native_steps, 'timeline': timeline_time,
              'audience': 'executor_private'}
    record['pending_max_temperature'] = {
        obj.name: repr(snapshot.get(obj.scene.idx, {}).get('registry', {}).get('object_registry', {})
                       .get(obj.name, {}).get('non_kin', {}).get('MaxTemperature', {}).get('max_temperature'))
        for obj in pending}
    backend._placement_record(record)
    try:
        try:
            with gm.unlocked():
                gm.ENABLE_OBJECT_STATES = False
                gm.ENABLE_TRANSITION_RULES = False
            sim.stop()
            sim.play()
            sim.load_state(copy.deepcopy(snapshot), serialized=False)
            # Loading scene state can render while restoring native resources.
            # Restore all clocks after that work, including the Kit timeline.
            context._timeline.set_current_time(timeline_time)
            context._timeline.commit()
            context._current_time = clock
            context._number_of_steps = native_steps
        finally:
            with gm.unlocked():
                gm.ENABLE_OBJECT_STATES = enabled_states
                gm.ENABLE_TRANSITION_RULES = enabled_transitions
        record['clock_after'] = float(sim.current_time)
        record['native_steps_after'] = int(sim.current_time_step_index)
        record['timeline_after'] = float(context._timeline.get_current_time())
        record['env_step_after'] = backend.steps
        if (backend.steps != control_steps or float(sim.current_time) != clock
                or int(sim.current_time_step_index) != native_steps
                or abs(float(context._timeline.get_current_time())-timeline_time) >= 1e-9):
            raise RuntimeError('Native cut rebuild changed simulation clocks')
        if dict(context._physics_callback_functions) != callbacks:
            raise RuntimeError('Native cut rebuild changed physics callbacks')
        if tuple((scene, tuple(scene.objects)) for scene in sim.scenes) != identities:
            raise RuntimeError('Native cut rebuild changed scene object identities')
        mismatches = _state_mismatches(snapshot, sim.dump_state(serialized=False), backend.torch)
        if mismatches:
            record['state_mismatches'] = mismatches
            raise RuntimeError('Native cut rebuild changed serialized world state')
    except BaseException:
        backend._placement_record({**record, 'status': 'failed'})
        raise
    backend.frames_revision = -1
    backend._placement_record({**record, 'status': 'passed', 'state_preserved': True,
                               'clocks_preserved': True, 'callbacks_preserved': True})
