"""rc1 (2026-11-20, benchmark side): keep explicit close / toggle results from being undone by contact physics.

Two unintended flips are blocked, nothing else:
1. A door closed by the `close` primitive is held closed: before and after every physics step a held joint pushed more
   than HOLD_TOLERANCE away from its closed position is put back with zero velocity, until the next explicit `open` of
   that object releases it (the post-step clamp precedes OmniGibson's object-state update, so requires_closed and the
   evaluator never read the door open during a hold). Contents pressing on the door can no longer push it open. (Narrowing joint limits was tried in
   the first dev snapshot and rejected: OmniGibson derives Open thresholds and open goals from the limits.)
2. Finger contact no longer toggles switches: OmniGibson's ToggledOn counts steps with a robot finger inside the toggle
   marker and flips the state after CAN_TOGGLE_STEPS; that counter is held at 0. Legal changes are unchanged:
   requires_closed still switches an appliance off when its door is opened, explicit toggle_on/off and task processes
   still set the value directly.
Switch: MAS_ARTICULATION_HOLD=off restores v12b behaviour (default on).
"""
import os

HOLD_TOLERANCE = 0.005   # rad / m a held joint may drift before it is put back; the T2 dryer's Open threshold is
                         # 0.0785 rad. Joint limits are NOT narrowed: Open thresholds are derived from them.
_HELD = {}               # (object name, joint name) -> (joint, closed position)
_SUBSCRIPTION = None


def enabled():
    return os.environ.get('MAS_ARTICULATION_HOLD', 'on') != 'off'


def _joints(obj):
    from omnigibson.object_states import Open
    info = getattr(obj.states[Open], 'relevant_joints_info', None) if Open in obj.states else None
    return list(info[1]) if info and info[1] else []


def enforce():
    """Before every physics step: put each held door joint back to its closed position with zero velocity if contact
    pushed it more than HOLD_TOLERANCE. Returns the number of corrections (for tests and diagnostics)."""
    fixed = 0
    for joint, q in list(_HELD.values()):
        try:
            cur = float(joint.get_state()[0][0])
        except Exception:
            continue
        if abs(cur - q) > HOLD_TOLERANCE:
            joint.set_pos(q)
            joint.set_vel(0.0)
            fixed += 1
    return fixed


def clamp_now(obj):
    """Apply the hold to this object's held joints immediately (no physics step) and report, per joint, the position
    contact physics had pushed it to before the clamp. Used by `close` so that its stability check reads the state the
    hold maintains (the pre-step hook only runs before the next physics step); the pre-clamp position goes to private
    diagnostics only."""
    out = []
    for (name, jname), (joint, q) in list(_HELD.items()):
        if name != getattr(obj, 'name', None):
            continue
        try:
            cur = float(joint.get_state()[0][0])
        except Exception:
            continue
        clamped = abs(cur - q) > HOLD_TOLERANCE
        if clamped:
            joint.set_pos(q)
            joint.set_vel(0.0)
        out.append({'joint': jname, 'pre_clamp_position': cur, 'closed_position': q, 'clamped': clamped})
    return out


def _physx_interface():
    import omnigibson as og
    return og.sim._physics_context._physx_interface


def _subscribe(physx=None):
    """Clamp held joints both before and after every physics step. The post-step clamp runs inside the physics step,
    before OmniGibson updates object states (ToggledOn's requires_closed check reads Open there), so no state read
    during a hold sees the door pushed open; the pre-step clamp keeps the solver starting from the closed pose."""
    global _SUBSCRIPTION
    if _SUBSCRIPTION is not None:
        return True
    try:
        physx = physx if physx is not None else _physx_interface()
        _SUBSCRIPTION = (physx.subscribe_physics_on_step_events(lambda _: enforce(), pre_step=True, order=1),
                         physx.subscribe_physics_on_step_events(lambda _: enforce(), pre_step=False, order=1))
        return True
    except Exception:          # simulator-free unit tests
        return False


def lock_closed(backend, obj):
    """Hold the operated joints at their current (closed) positions until the next explicit open of this object."""
    if not enabled():
        return []
    out = []
    for joint in _joints(obj):
        key = (obj.name, getattr(joint, 'joint_name', None))
        if not all(hasattr(joint, a) for a in ('get_state', 'set_pos', 'set_vel')):
            out.append({'joint': key[1], 'skipped': 'joint_without_state_api'})   # stub joints in unit tests
            continue
        q = float(joint.get_state()[0][0])
        _HELD[key] = (joint, q)
        out.append({'joint': key[1], 'closed_position': q, 'tolerance': HOLD_TOLERANCE, 'step_hook': _subscribe()})
    return out


def unlock(backend, obj):
    """Release the hold before an explicit open of this object."""
    out = []
    for key in [k for k in _HELD if k[0] == obj.name]:
        _HELD.pop(key)
        out.append({'joint': key[1], 'released': True})
    return out


_INSTALLED = False


def toggle_step(state, door_open):
    """One ToggledOn update under articulation_hold. Kept from OmniGibson: an appliance that requires a closed door is
    switched off while its door is open. Removed: the finger-in-marker step count that flips the value."""
    if getattr(state, 'requires_closed', False) and door_open():
        state.set_value(False)
        state.robot_can_toggle_steps = 0
        return 'switched_off_door_open'
    if state.robot_can_toggle_steps != 0:
        state.robot_can_toggle_steps = 0
        state.obj.state_updated()
    return 'held'


def install_toggle_guard():
    """Replace ToggledOn._update once per process: same requires_closed rule, no finger-count toggling."""
    global _INSTALLED
    if _INSTALLED or not enabled():
        return False
    try:
        from omnigibson.object_states import Open, ToggledOn   # same class objects as object_states.toggle.ToggledOn
    except ImportError:        # simulator-free unit tests with stub modules
        return False

    def _update(self):
        toggle_step(self, lambda: bool(self.obj.states[Open].get_value()))

    _update.__doc__ = 'rc1 articulation_hold: requires_closed kept, finger-contact toggling disabled'
    ToggledOn._update = _update
    _INSTALLED = True
    return True
