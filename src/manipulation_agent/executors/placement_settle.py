"""rc1 A5 (2026-11-20, benchmark side): placement settle fallback.

When place_on_top / place_inside has passed every precondition (hand holds the object, container open, an upward
receiving surface, a collision-free release pose was selected) but the object did not end on / in the target after
the normal free settling, the executor:
1. puts the object (and its registered carried contents) back at the SAME selected pose, zero velocity, with the
   grasp already released, and runs the SAME settling and the SAME checks as the normal path (place_on_top: support
   ray, upward normal, bottom contact, settled, not supported by the robot; place_inside: official Inside);
2. if that still fails, asks the pose selector for the next candidate (tried poses excluded), up to 2 more;
3. if all fail, reports the normal failure and leaves the object where it physically ended (no rewind).
Nothing is frozen or put to sleep, and no looser check is used: a success receipt means the normal checks passed.
The pose selector is unchanged, so container closure, single hand, upward surface and carrier capacity still apply.
Private record: placement_diagnostics `status: settle_fallback` with why the first check failed (object left the
target / still on the target but a check failed) and every attempt's result.
Switch: MAS_PLACEMENT_SETTLE=off restores v12b behaviour (default on).
"""
import os

EXTRA_CANDIDATES = 2


def enabled():
    return os.environ.get('MAS_PLACEMENT_SETTLE', 'on') != 'off'


def first_failure_kind(primitive, official, checks):
    """'left_target' when the object is no longer on / in the target; otherwise 'on_target_check_failed'."""
    if primitive == 'place_inside':
        return 'left_target'          # the only place_inside check is the official Inside itself
    if official:
        return 'on_target_check_failed'
    if checks and checks.get('support_ray_hits_selected_object') and checks.get('bottom_near_support'):
        return 'on_target_check_failed'
    return 'left_target'


def run(attempt, next_candidate, first, record):
    """Drive the fallback. first / candidates are dicts with 'pose' = (position, orientation);
    attempt(candidate) -> (ok, detail); next_candidate(tried_positions) -> candidate or None.
    Returns (ok, attempts). Pure control flow so it can be unit-tested without a simulator."""
    attempts = []
    tried = [first['pose'][0]]
    ok, detail = attempt(first)
    attempts.append({'candidate': 'same_pose', 'ok': ok, **detail})
    extra = 0
    while not ok and extra < EXTRA_CANDIDATES:
        cand = next_candidate(list(tried))
        if cand is None:
            attempts.append({'candidate': 'none_left', 'ok': False})
            break
        tried.append(cand['pose'][0]); extra += 1
        ok, detail = attempt(cand)
        attempts.append({'candidate': f'next_{extra}', 'ok': ok, **detail})
    record(attempts, ok)
    return ok, attempts
