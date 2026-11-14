"""Read-only audit helpers for historical explicit-navigation hot-drink runs.

These checks preserve the original high-level scoring contract. They do not
apply that contract to the current native executor or change archived scores.
"""
import json
import hashlib

def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

def visual_profile_checks(profile, skill_catalog_enabled, model):
    """Audit document access separately from the shared planning protocol."""
    allowed={'skills','planning'} if skill_catalog_enabled else {'minimal','planning_no_skills'}
    checks={'visual_pixel_action_profile':profile in allowed}
    if profile in {'planning','planning_no_skills'}:
        checks['public_decision_every_robot_tool']=all(
            isinstance(arguments.get('decision'),dict) and
            set(arguments['decision'])=={'state','active_subgoal','reason','expected'} and
            all(isinstance(value,str) and value.strip()
                for value in arguments['decision'].values())
            for name,arguments in model if name in
            {'initialize','look','act','finish','list_skills','read_skill',
             'update_plan','remember','recall'})
    if profile=='planning_no_skills':
        checks['skill_tools_absent_in_ablation']=all(
            name not in {'list_skills','read_skill'} for name,_ in model)
    return checks

def hand_empty_after_events(events):
    """Track committed carry/release effects from the simulator's ordered act results."""
    held = False
    for event in events:
        if event.get('kind') != 'tool_result' or event.get('name') not in {'act', 'act_semantic'}:
            continue
        result = event.get('result') or {}
        if not result.get('ok'):
            continue
        primitive = (result.get('effect') or {}).get('primitive')
        if primitive == 'grasp':
            held = True
        elif primitive in {'place_inside', 'place_on_top', 'place_near_heating_element', 'place_under',
                           'place_next_to', 'attach', 'hang', 'release'}:
            held = False
    return not held

def motion_checks(run, summary, motion):
    demo_motion = summary.get('demo_motion') is True
    checks = {'motion_mode_matches_run': (run.get('evaluation') or {}).get('demo_motion') is demo_motion}
    if demo_motion:
        checks['visible_motion_recorded'] = any(row.get('status') == 'shown' for row in motion)
    else:
        checks['no_demo_motion_recorded'] = not motion
    return checks

def replay_high_level_actions(events, state_rows, *, initial_open=None, legacy_wait_heating=False,
                              task_object_names=None):
    """Recompute the score from committed pixel or legacy named operations."""
    from ..tasks.hot_drink_state import HighLevelHotDrink

    state = HighLevelHotDrink(initial_open=initial_open, legacy_wait_heating=legacy_wait_heating)
    pending = None
    private_result = None
    committed = 0
    environment_only = 0
    snapshots_match = True
    for event in events:
        if event.get('kind') == 'tool_call' and event.get('name') in {'act', 'act_semantic'}:
            if pending is not None:
                raise ValueError('Overlapping actions in simulator trace')
            pending = event.get('arguments') or {}
            private_result = None
        elif event.get('kind') == 'private_executor_result' and pending is not None:
            private_result = event.get('details') or {}
        elif event.get('kind') == 'tool_result' and event.get('name') in {'act', 'act_semantic'}:
            if pending is None:
                raise ValueError('Action result without preceding call')
            primitive = pending['primitive']
            if (event.get('result') or {}).get('ok') is True and primitive not in {'navigate_to','look'}:
                if private_result is None:
                    raise ValueError('Successful action has no private executor result')
                target = private_result.get('semantic_target')
                if event['name'] == 'act_semantic' and target != pending.get('target'):
                    raise ValueError('Named target differs from executor target')
                if event['name'] == 'act' and primitive not in {'wait','release'}:
                    grounding = private_result.get('private_grounding') or {}
                    if grounding.get('selected_pixel') != pending.get('target',{}).get('point'):
                        raise ValueError('Executor grounding differs from model pixel')
                if 'environment_only_action' in private_result:
                    grounding = private_result.get('private_grounding') or {}
                    selected = grounding.get('selected_object_prim')
                    digest = hashlib.sha256(json.dumps(state.snapshot(), sort_keys=True,
                                                        allow_nan=False).encode()).hexdigest()
                    valid = (private_result['environment_only_action'] == 'non_task_open_close_v1'
                             and event['name'] == 'act' and primitive in {'open', 'close'}
                             and private_result.get('primitive') == primitive and target is None
                             and state.held is None
                             and bool(task_object_names) and isinstance(selected, str)
                             and selected.startswith('/World/scene_0/')
                             and len(selected.split('/')) == 4 and bool(selected.rsplit('/', 1)[-1])
                             and selected.rsplit('/', 1)[-1] not in task_object_names
                             and private_result.get('environment_target_prim') == selected
                             and grounding.get('image_ref') == pending.get('target', {}).get('image_ref')
                             and private_result.get('high_level_state_sha256') == digest)
                    if not valid:
                        raise ValueError('Invalid or insufficient non-task environment-operation evidence')
                    environment_only += 1
                    pending = private_result = None
                    continue
                state.apply(primitive, target,
                            seconds=pending.get('wait_seconds') or 0)
                if committed >= len(state_rows):
                    snapshots_match = False
                else:
                    row = state_rows[committed]
                    snapshots_match &= row.get('state') == state.snapshot()
                committed += 1
            pending = None
            private_result = None
    if pending is not None:
        raise ValueError('Unfinished action in simulator trace')
    evaluation = state.evaluate()
    report = {'protocol': 'posthoc_action_trace_replay_v1',
            'evaluation': evaluation, 'committed_actions': committed,
            'environment_only_actions': environment_only,
            'private_state_log_count': len(state_rows),
            'private_state_log_matches': snapshots_match and committed == len(state_rows)}
    # Runs recorded before the cabinet-state correction used a closed symbolic
    # cabinet. Preserve their auditability without changing their saved traces.
    if initial_open is None and not report['private_state_log_matches'] and state_rows:
        legacy = replay_high_level_actions(events, state_rows, initial_open=(), legacy_wait_heating=True,
                                          task_object_names=task_object_names)
        if legacy['private_state_log_matches']:
            return legacy
    return report

def base_height_report(run_dir):
    rows = jsonl(run_dir / 'base_motion.jsonl')
    corrections = jsonl(run_dir / 'high_level_base_height_corrections.jsonl')
    heights = [float(row['actual_position'][2]) for row in rows]
    baseline = heights[0] if heights else None
    deviation = max((abs(height - baseline) for height in heights), default=None)
    return {'sampled_control_steps': len(heights), 'initial_base_z_m': baseline,
            'max_deviation_m': deviation, 'height_corrections': len(corrections),
            'stable': deviation is not None and deviation <= .10}

def explicit_navigation_report(events, plans):
    """Every executed path must belong to a model-selected navigate_to call."""
    active = None
    intervals = []
    for event in events:
        if event.get('kind') == 'tool_call':
            active = event
        elif event.get('kind') == 'tool_result' and active is not None:
            intervals.append((active['at'], event['at'], active.get('name'),
                              (active.get('arguments') or {}).get('primitive')))
            active = None
    return {'paths': len(plans), 'all_explicit': all(
        any(start <= plan['at'] <= end and name == 'act' and primitive == 'navigate_to'
            for start, end, name, primitive in intervals) for plan in plans)}
