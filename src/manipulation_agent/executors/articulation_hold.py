"""rc1 (2026-11-20, benchmark side): keep explicit close / toggle results from being undone by contact physics.

Two unintended flips are blocked, nothing else:
1. A door closed by the `close` primitive is held closed: the operated joints' runtime limits are narrowed to the closed
   position (+-HOLD_HALF_WIDTH rad, well inside OmniGibson's open threshold) until the next explicit `open` of that
   object restores the original limits. Contents pressing on the door can no longer push it open; they stay inside.
2. Finger contact no longer toggles switches: OmniGibson's ToggledOn counts steps with a robot finger inside the toggle
   marker and flips the state after CAN_TOGGLE_STEPS; that counter is held at 0. Legal changes are unchanged:
   requires_closed still switches an appliance off when its door is opened, explicit toggle_on/off and task processes
   still set the value directly.
Switch: MAS_ARTICULATION_HOLD=off restores v12b behaviour (default on).
"""
import os

HOLD_HALF_WIDTH = 0.005   # rad / m; OmniGibson Open threshold for the T2 dryer is 0.0785 rad


def enabled():
    return os.environ.get('MAS_ARTICULATION_HOLD', 'on') != 'off'


def _joints(obj):
    from omnigibson.object_states import Open
    info = getattr(obj.states[Open], 'relevant_joints_info', None) if Open in obj.states else None
    return list(info[1]) if info and info[1] else []


def lock_closed(backend, obj):
    """Narrow the operated joints' limits around their current (closed) positions. Returns the record kept for unlock."""
    if not enabled():
        return []
    held = backend.__dict__.setdefault('_articulation_held', {})
    out = []
    for joint in _joints(obj):
        key = (obj.name, getattr(joint, 'joint_name', None))
        if not all(hasattr(joint, a) for a in ('lower_limit', 'upper_limit', 'get_state', 'keep_still')):
            out.append({'joint': key[1], 'skipped': 'joint_without_runtime_limits'})   # stub joints in unit tests
            continue
        lo, hi = float(joint.lower_limit), float(joint.upper_limit)
        if key in held:                      # already held: keep the original limits
            lo, hi = held[key]
        q = float(joint.get_state()[0][0])
        joint.keep_still()
        new_lo, new_hi = max(lo, q - HOLD_HALF_WIDTH), min(hi, q + HOLD_HALF_WIDTH)
        joint.lower_limit = new_lo
        joint.upper_limit = new_hi
        held[key] = (lo, hi)
        out.append({'joint': joint.joint_name, 'closed_position': q, 'original_limits': [lo, hi], 'held_limits': [new_lo, new_hi]})
    return out


def unlock(backend, obj):
    """Restore original limits before an explicit open of this object."""
    held = backend.__dict__.get('_articulation_held') or {}
    out = []
    for joint in _joints(obj):
        key = (obj.name, joint.joint_name)
        if key in held:
            lo, hi = held.pop(key)
            joint.lower_limit = lo
            joint.upper_limit = hi
            out.append({'joint': joint.joint_name, 'restored_limits': [lo, hi]})
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
