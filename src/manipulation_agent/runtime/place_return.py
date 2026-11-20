"""Harness option --place-return (2026-11-21, default off), bridge side.

install(harness): records the robot's own base pose (odometry) at every observation revision, privately, and adds
the bridge-only act primitive 'return_to' with the integer option 'return_revision' to the bridge act schema. The
proxy (mcp_server --place-return) shows the model 'return_to' with a 'place' (a name it gave or a loc id) and sends
the revision of that place's latest visit; the model never sees revisions-to-pose, poses, routes or lengths.
perform_return(...) mirrors VisionHarness.perform for one navigation-like action: one act step, ordinary robot clock,
the ordinary navigation receipt. With the switch off none of this is installed and the bridge catalog is unchanged.
"""
from __future__ import annotations

import copy
import math

from ..contracts import SkillError
from ..observations.boundary import factual_execution_error

NAV_MESSAGES = {
    'reached': 'Returned to that place. Inspect the updated RGB.',
    'approached': ('Returned near that place; the exact spot was occupied or not walkable now. '
                   'Inspect the updated RGB.'),
}


def _pose(backend):
    p, q = backend.robot.get_position_orientation()
    x, y, z, w = (float(v) for v in q)
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return (float(p[0]), float(p[1]), yaw)


def install(harness, *, visited_only=True):
    harness._place_poses = {}
    harness._place_return_visited_only = bool(visited_only)
    harness._place_return_start = _pose(harness.backend)[:2]
    original = harness.refresh

    def refresh():
        snapshot = original()
        try:
            harness._place_poses[int(harness.revision)] = _pose(harness.backend)
        except Exception:
            pass
        return snapshot

    harness.refresh = refresh
    harness._place_poses[int(harness.revision)] = _pose(harness.backend)
    act = harness.catalog.get('act')
    if act is not None:
        props = act['inputSchema']['properties']
        enum = props['primitive'].get('enum')
        if isinstance(enum, list) and 'return_to' not in enum:
            enum.append('return_to')
        props['return_revision'] = {'type': ['integer', 'null'], 'minimum': 0}


def perform_return(harness, revision, return_revision, *, parent_request_id=None):
    """One return_to act: same revision / budget / refresh / receipt rules as VisionHarness.perform."""
    from .robot_clock import clocked_action
    from ..executors.place_return import return_to_pose
    harness.deadline.check()
    if revision != harness.revision:
        raise SkillError('stale_observation', 'Use the latest observation revision')
    poses = getattr(harness, '_place_poses', None)
    if poses is None:
        raise SkillError('invalid_arguments', 'return_to is not enabled')
    if not isinstance(return_revision, int) or return_revision not in poses:
        raise SkillError('invalid_arguments', 'Unknown place for return_to')
    goal = poses[return_revision]
    harness.actions += 1
    limit = harness.budget.max_sim_steps - harness.backend.steps
    if harness.unrestricted_physics or harness.deadline.managed:
        from .deadline import DEADLINE_STEP_SENTINEL
        limit = DEADLINE_STEP_SENTINEL - harness.backend.steps
    error = None
    harness.backend._active_request_id = parent_request_id
    try:
        details = clocked_action(harness.backend, 'navigate_to', {}, lambda: return_to_pose(
            harness.backend, goal[:2], goal[2], limit, start_xy=harness._place_return_start,
            visited_only=harness._place_return_visited_only))
        harness.recorder.event('private_executor_result', {'primitive': 'return_to', 'details': details})
    except SkillError as exc:
        harness.recorder.event('private_executor_error', {'code': exc.code, 'stage': exc.stage, 'detail': str(exc)})
        error = factual_execution_error(exc, 'navigate_to', harness._public_hand_state())
        if exc.code == 'navigation_unreachable':
            error = {**error, 'message': str(exc)}
    finally:
        harness.backend._active_request_id = None
        harness.revision += 1
        if not harness.deadline.expired and not harness.deadline.stop_requested:
            harness.refresh()
    if error:
        return {'ok': False, 'error': error, 'observation': harness.snapshot, 'actions_used': harness.actions}
    status = details.get('nav_status')
    effect = {'primitive': 'return_to', 'status': 'completed', 'verification': 'executor_operation_only'}
    if status in NAV_MESSAGES:
        effect.update(navigation_status=status, message=NAV_MESSAGES[status])
    return {'effect': effect, 'observation': copy.deepcopy(harness.snapshot), 'actions_used': harness.actions}
