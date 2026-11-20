"""Independent single-bottle acceptance from owner-recorded events/physics traces.

Executed by the host with JSON on stdin. No controller imports or simulator calls.
This engineering goal never substitutes for the official full sorting evaluator.
"""
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import sys


def rotate(q, v):
    x, y, z, w = q
    a = [y*v[2]-z*v[1], z*v[0]-x*v[2], x*v[1]-y*v[0]]
    b = [y*a[2]-z*a[1], z*a[0]-x*a[2], x*a[1]-y*a[0]]
    return [v[i]+2*(w*a[i]+b[i]) for i in range(3)]


def inverse(q):
    return [-q[0], -q[1], -q[2], q[3]]


def world_position(base, position):
    offset = rotate(base['quaternion_xyzw'], position)
    return [base['position_m'][i]+offset[i] for i in range(3)]


def relative_object(row, arm):
    point = row['arms'][arm]['held_object_pose_world']['position_m']
    base = row['base_pose_world']
    local = rotate(inverse(base['quaternion_xyzw']), [point[i]-base['position_m'][i] for i in range(3)])
    eef = row['arms'][arm]['actual']
    return rotate(inverse(eef['quaternion_xyzw']), [local[i]-eef['position_m'][i] for i in range(3)])


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def require_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite measurement')
    if isinstance(value, dict):
        for child in value.values():
            require_finite(child)
    elif isinstance(value, list):
        for child in value:
            require_finite(child)


def evaluate(request):
    goal = request.get('goal', {})
    geometry_pick = goal.get('protocol') == 'geometry_pick_v1'
    required_lift = goal.get('lift_m', .10) if geometry_pick else .05
    if not isinstance(required_lift, (int, float)) or not math.isfinite(required_lift) or required_lift < .05:
        raise ValueError('Invalid required lift')
    task_name = goal.get('target_task_name', 'wine_bottle.n.01_1')
    hold_steps = goal.get('hold_steps', 60)
    if type(hold_steps) is not int or hold_steps < 60:
        raise ValueError('Independent hold window must be at least 60 control steps')
    run = Path(request['run_dir'])
    events, trace = run/'env/events.jsonl', run/'env/waypoint_trace.jsonl'
    initial = final = target = initial_object = None
    grasp_calls, failed_calls, release_calls = 0, 0, 0
    motors = {'navigate_to', 'move_joints', 'execute_waypoints', 'grasp', 'place', 'translate',
              'rotate', 'press', 'set_gripper', 'release', 'wait', 'manip'}
    with events.open() as stream:
        for line in stream:
            event = json.loads(line)
            if event.get('kind') == 'tool_call':
                grasp_calls += event.get('name') == 'grasp'
                release_calls += event.get('name') == 'release'
            if event.get('kind') == 'tool_result' and event.get('name') in motors:
                failed_calls += event.get('result', {}).get('ok') is False
            obs = event.get('observation') or event.get('result', {}).get('observation')
            if not isinstance(obs, dict) or not obs.get('oracle_3d'):
                continue
            require_finite(obs)
            if initial is None:
                matches = [o for o in obs['oracle_3d']['objects'] if task_name in o.get('task_names', [])]
                if len(matches) != 1:
                    raise ValueError('Missing or ambiguous initial target binding')
                initial, initial_object = obs, matches[0]
                target = initial_object['id']
            final = obs
    if initial is None or final is None:
        raise ValueError('Missing owner-recorded initial/final observations')
    initial_robot = initial['oracle_3d']['robot']
    initial_z = world_position(initial_robot['base_pose_world'], initial_object['pose']['position_m'])[2]
    initial_h = initial_robot['trunk']['body_posture']['upper_body_height_above_base_m']
    window = deque(maxlen=hold_steps)
    held_navigation = False
    retention_episodes, previously_held = 0, False
    with trace.open() as stream:
        for line in stream:
            row = json.loads(line)
            require_finite(row)
            currently_held = any(a.get('held_object') == target for a in row.get('arms', {}).values())
            retention_episodes += int(currently_held and not previously_held)
            previously_held = currently_held
            if row.get('phase') == 'legacy_navigation' and any(a.get('held_object') is not None for a in row.get('arms', {}).values()):
                held_navigation = True
            window.append(row)
    checks = {'complete_final_window': len(window) == hold_steps,
              'one_native_grasp': retention_episodes == 1 if geometry_pick else grasp_calls == 1,
              'no_failed_motor_calls': failed_calls == 0,
              'no_release': release_calls == 0, 'no_held_navigation': not held_navigation,
              'continuous_steps': False, 'continuous_retention': False, 'continuous_lift': False,
              'continuous_upright': False, 'continuous_relative_stability': False,
              'final_native_retention': False}
    measurements = {'initial_world_height_m': initial_z, 'initial_body_height_m': initial_h,
                    'required_lift_m': required_lift, 'native_retention_episodes': retention_episodes,
                    'failed_motor_calls': failed_calls}
    if len(window) == hold_steps:
        samples = list(window)
        checks['continuous_steps'] = all(type(r.get('sim_step')) is int for r in samples) and all(
            b['sim_step'] == a['sim_step']+1 for a, b in zip(samples, samples[1:]))
        holders = [a for a, v in samples[-1]['arms'].items() if v.get('held_object') == target]
        if len(holders) == 1:
            arm = holders[0]
            checks['continuous_retention'] = all(
                r['arms'][arm].get('held_object') == target and
                all(v.get('held_object') is None for a, v in r['arms'].items() if a != arm) for r in samples)
            if checks['continuous_retention']:
                lifts = [r['arms'][arm]['held_object_pose_world']['position_m'][2]-initial_z for r in samples]
                rel = [relative_object(r, arm) for r in samples]
                drift = max(math.dist(rel[0], v) for v in rel)
                checks['continuous_lift'] = min(lifts) >= required_lift
                checks['continuous_relative_stability'] = drift <= .03
                measurements.update(arm=arm, min_lift_m=min(lifts), max_relative_drift_m=drift)
            checks['continuous_upright'] = all(
                r['body_posture']['upper_body_tilt_deg'] <= 5 and
                r['body_posture']['upper_body_height_above_base_m'] >= initial_h-.08 for r in samples)
            hand = final['oracle_3d']['robot']['arms'].get(arm, {})
            native = hand.get('native_grasp') or {}
            checks['final_native_retention'] = (hand.get('held_object') == target and native.get('object_id') == target and
                                                native.get('target_link') == 'base_link' and bool(native.get('constraint_type')))
        measurements.update(first_step=samples[0]['sim_step'], last_step=samples[-1]['sim_step'])
    return {'engineering_success': all(checks.values()), 'scope': 'single_bottle_continuous_hold',
            'target_task_name': task_name, 'target_object_id': target, 'hold_steps': hold_steps,
            'checks': checks, 'measurements': measurements,
            'evidence': {str(p): file_hash(p) for p in (events, trace)},
            'official_whole_task_success': request.get('record', {}).get('evaluation', {}).get('official_task_success'),
            'not_official_whole_task_evaluation': True}


if __name__ == '__main__':
    try:
        result = evaluate(json.load(sys.stdin))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        result = {'engineering_success': False, 'error': str(exc), 'scope': 'single_bottle_continuous_hold'}
    print(json.dumps(result, allow_nan=False))
