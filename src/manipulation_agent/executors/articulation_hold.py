"""rc1/rc2 (benchmark side): keep explicit close / open / toggle results from being undone by contact physics.

Two unintended flips are blocked, nothing else:
1. A door closed by `close` is held closed (`lock_closed`), and a door whose `open` verified stable is held open
   (`lock_open`): before and after every physics step a held joint pushed more than HOLD_TOLERANCE away from its held
   position is put back with zero velocity, until the next explicit `open` / `close` of that object releases it (the
   post-step clamp precedes OmniGibson's object-state update, so requires_closed and the evaluator never read a drifted
   state during a hold). Contents pressing on a closed door can no longer push it open, and robot chassis contact during
   placement settling can no longer slam an explicitly opened door shut. (Narrowing joint limits was tried in the first
   dev snapshot and rejected: OmniGibson derives Open thresholds and open goals from the limits.)
2. Finger contact no longer toggles switches: OmniGibson's ToggledOn counts steps with a robot finger inside the toggle
   marker and flips the state after CAN_TOGGLE_STEPS; that counter is held at 0. Legal changes are unchanged:
   requires_closed still switches an appliance off when its door is opened, explicit toggle_on/off and task processes
   still set the value directly.
Switch: MAS_ARTICULATION_HOLD=off restores v12b behaviour (default on).
"""
import contextlib
import os

HOLD_TOLERANCE = 0.005   # rad / m a held joint may drift before it is put back; the T2 dryer's Open threshold is
                         # 0.0785 rad. Joint limits are NOT narrowed: Open thresholds are derived from them.
_HELD = {}               # (object name, joint name) -> (joint, held position)
_MODES = {}              # (object name, joint name) -> 'closed' | 'open'
_PAUSED = {}             # object name -> nesting count (temporarily suspends hold during rolled-back previews)
_SUBSCRIPTION = None


def enabled():
    return os.environ.get('MAS_ARTICULATION_HOLD', 'on') != 'off'


def _joints(obj):
    from omnigibson.object_states import Open
    info = getattr(obj.states[Open], 'relevant_joints_info', None) if Open in obj.states else None
    return list(info[1]) if info and info[1] else []


def _joint_key(obj, joint):
    return (getattr(obj, 'name', None), getattr(joint, 'joint_name', getattr(joint, 'name', None)))


def _joint_pos(joint):
    pos = joint.get_state()[0]
    try:
        return float(pos[0])
    except (TypeError, IndexError):
        return float(pos)


def is_held(obj, joint=None, *, mode=None):
    """Return True if `obj` (or a specific `joint` on `obj`) is currently held (optionally matching `mode`)."""
    if joint is not None:
        key = _joint_key(obj, joint)
        if key not in _HELD:
            return False
        return True if mode is None else _MODES.get(key, 'closed') == mode
    name = getattr(obj, 'name', None)
    return any(k[0] == name and (mode is None or _MODES.get(k, 'closed') == mode) for k in _HELD)


def is_held_open(obj, joint=None):
    return is_held(obj, joint, mode='open')


def is_held_closed(obj, joint=None):
    return is_held(obj, joint, mode='closed')


@contextlib.contextmanager
def pause(obj):
    """Temporarily suspend articulation hold for `obj` (e.g. rolled-back closure clearance previews)."""
    name = getattr(obj, 'name', obj)
    _PAUSED[name] = _PAUSED.get(name, 0) + 1
    try:
        yield
    finally:
        left = _PAUSED.get(name, 1) - 1
        if left <= 0:
            _PAUSED.pop(name, None)
        else:
            _PAUSED[name] = left


def enforce(modes=None, *, exclude_joints=()):
    """Before and after every physics step: put each held door joint back to its target position with zero velocity if
    contact pushed it more than HOLD_TOLERANCE. Returns the number of corrections (for tests and diagnostics)."""
    if not enabled():
        return 0
    mode_set = set(modes) if modes is not None else None
    excluded = set(exclude_joints) if exclude_joints else ()
    fixed = 0
    for key, (joint, q) in list(_HELD.items()):
        if _PAUSED.get(key[0], 0) > 0:
            continue
        if mode_set is not None and _MODES.get(key, 'closed') not in mode_set:
            continue
        if excluded and joint in excluded:
            continue
        try:
            cur = _joint_pos(joint)
        except Exception:
            continue
        if abs(cur - q) > HOLD_TOLERANCE:
            joint.set_pos(q)
            joint.set_vel(0.0)
            fixed += 1
    return fixed


apply = enforce


def clamp_now(obj):
    """Apply the hold to this object's held joints immediately (no physics step) and report, per joint, the position
    contact physics had pushed it to before the clamp. Used by `close` so that its stability check reads the state the
    hold maintains (the pre-step hook only runs before the next physics step); the pre-clamp position goes to private
    diagnostics only."""
    out = []
    obj_name = getattr(obj, 'name', None)
    if _PAUSED.get(obj_name, 0) > 0:
        return out
    for (name, jname), (joint, q) in list(_HELD.items()):
        if name != obj_name:
            continue
        try:
            cur = _joint_pos(joint)
        except Exception:
            continue
        clamped = abs(cur - q) > HOLD_TOLERANCE
        if clamped:
            joint.set_pos(q)
            joint.set_vel(0.0)
        pos_key = 'open_position' if _MODES.get((name, jname), 'closed') == 'open' else 'closed_position'
        out.append({'joint': jname, 'pre_clamp_position': cur, pos_key: q, 'clamped': clamped})
    return out


def _physx_interface():
    import omnigibson as og
    return og.sim._physics_context._physx_interface


def _subscribe(physx=None):
    """Clamp held joints both before and after every physics step. The post-step clamp runs inside the physics step,
    before OmniGibson updates object states (ToggledOn's requires_closed check reads Open there), so no state read
    during a hold sees the door pushed out of its held state; the pre-step clamp keeps the solver starting from the
    held pose."""
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
        key = _joint_key(obj, joint)
        if not all(hasattr(joint, a) for a in ('get_state', 'set_pos', 'set_vel')):
            out.append({'joint': key[1], 'skipped': 'joint_without_state_api'})   # stub joints in unit tests
            continue
        q = _joint_pos(joint)
        _HELD[key] = (joint, q)
        _MODES[key] = 'closed'
        out.append({'joint': key[1], 'closed_position': q, 'tolerance': HOLD_TOLERANCE, 'step_hook': _subscribe()})
    return out


def lock_open(backend, obj, joint_targets=None):
    """Hold the operated joints at their verified open positions until the next explicit close or unlock.

    `joint_targets` may be None (all Open-relevant joints at current positions), an iterable of joints (current
    positions), an iterable of `(joint, target_q)` pairs, or a `{joint: target_q}` mapping.
    """
    if not enabled():
        return []
    if joint_targets is None:
        items = [(joint, None) for joint in _joints(obj)]
    elif isinstance(joint_targets, dict):
        items = list(joint_targets.items())
    else:
        items = [(item[0], item[1]) if isinstance(item, tuple) and len(item) == 2 else (item, None)
                 for item in joint_targets]
    out = []
    for joint, target_q in items:
        key = _joint_key(obj, joint)
        if not all(hasattr(joint, a) for a in ('get_state', 'set_pos', 'set_vel')):
            out.append({'joint': key[1], 'skipped': 'joint_without_state_api'})
            continue
        cur = _joint_pos(joint)
        q = cur if target_q is None else float(target_q)
        if abs(cur - q) > HOLD_TOLERANCE:
            joint.set_pos(q)
        joint.set_vel(0.0)
        _HELD[key] = (joint, q)
        _MODES[key] = 'open'
        out.append({'joint': key[1], 'open_position': q, 'mode': 'open',
                    'tolerance': HOLD_TOLERANCE, 'step_hook': _subscribe()})
    return out


def unlock(backend, obj, joints=None):
    """Release the hold on `obj` (or only `joints` on `obj`) before an explicit open/close."""
    wanted = {_joint_key(obj, j) for j in joints} if joints is not None else None
    out = []
    for key in [k for k in _HELD if (k in wanted if wanted is not None else k[0] == obj.name)]:
        _HELD.pop(key, None)
        _MODES.pop(key, None)
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
