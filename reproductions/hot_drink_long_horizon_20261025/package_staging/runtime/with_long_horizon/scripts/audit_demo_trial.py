"""Audit one visible-manipulation model attempt and build its five-view page."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def model_calls(events):
    result = []
    for event in events:
        item = event.get('item') or {}
        if event.get('type') != 'item.started' or item.get('type') != 'mcp_tool_call':
            continue
        arguments = item.get('arguments') or {}
        result.append((item.get('tool'), json.loads(arguments) if isinstance(arguments, str) else arguments))
    return result


def calls_align(model, simulator):
    # qwen3.8-flash can emit a JSON object as the string value of act.target.
    # The MCP transport decodes that representation without changing the
    # selected RGB reference or pixel. Compare those equivalent values here.
    from manipulation_agent.mcp_server import normalize_qwen_target
    model = [(name, normalize_qwen_target(name, args)) for name, args in model]
    read_only = {'list_skills', 'read_skill'}
    ordered = lambda calls: [(name, args) for name, args in calls if name not in read_only]
    counted = lambda calls: Counter((name, json.dumps(args, sort_keys=True))
                                    for name, args in calls if name in read_only)
    return ordered(model) == ordered(simulator) and counted(model) == counted(simulator)


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


def replay_high_level_actions(events, state_rows, *, initial_open=None, legacy_wait_heating=False):
    """Recompute the score from committed pixel or legacy named operations."""
    from manipulation_agent.high_level_hot_drink import HighLevelHotDrink

    state = HighLevelHotDrink(initial_open=initial_open, legacy_wait_heating=legacy_wait_heating)
    pending = None
    private_result = None
    committed = 0
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
            'private_state_log_count': len(state_rows),
            'private_state_log_matches': snapshots_match and committed == len(state_rows)}
    # Runs recorded before the cabinet-state correction used a closed symbolic
    # cabinet. Preserve their auditability without changing their saved traces.
    if initial_open is None and not report['private_state_log_matches'] and state_rows:
        legacy = replay_high_level_actions(events, state_rows, initial_open=(), legacy_wait_heating=True)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('trial', type=Path)
    parser.add_argument('--primitive', help='Require this specific model-selected and executed action; '
                        'otherwise require at least one successful manipulation action')
    args = parser.parse_args()
    root = args.trial.resolve()
    run_dir, controller_dir = root / 'run', root / 'controller'
    run = json.loads((run_dir / 'run.json').read_text())
    controller = json.loads((controller_dir / 'controller.json').read_text())
    summary = json.loads((root / 'summary.json').read_text())
    events = jsonl(run_dir / 'events.jsonl')
    model_events = jsonl(controller_dir / 'model_events.jsonl')
    model = model_calls(model_events)
    simulator = [(event['name'], event.get('arguments') or {}) for event in events
                 if event.get('kind') == 'tool_call']
    acts = [arguments.get('primitive') for name, arguments in model
            if name in {'act', 'act_semantic'}]
    effects = [event.get('result') or {} for event in events
               if event.get('kind') == 'tool_result' and event.get('name') in {'act', 'act_semantic'}]
    motion = jsonl(run_dir / 'demo_motion.jsonl')
    source = Path(__file__).resolve().parents[1]
    catalog = json.loads((source / 'src/manipulation_agent/tasks.json').read_text())['tasks']
    first_ten = run['config']['task'] in list(catalog)[:10]
    non_manipulation = {'navigate_to', 'look', 'wait', 'release'}
    selected = [primitive for primitive in acts if primitive not in non_manipulation]
    executed = [(effect.get('effect') or {}).get('primitive') for effect in effects if effect.get('ok')]
    demo_motion = summary.get('demo_motion') is True
    high_level = summary.get('high_level_harness') is True
    custom_native = summary.get('task_definition_origin') == 'custom_bddl_overlay' and not high_level
    visual_high_level = high_level and summary.get('semantic_magic') is not True
    visual_pixel_protocol = visual_high_level or custom_native
    semantic_audit = None
    height_audit = None
    if high_level:
        semantic_audit = replay_high_level_actions(events, jsonl(run_dir / 'high_level_state.jsonl'))
        height_audit = base_height_report(run_dir)
        (run_dir / 'posthoc_semantic_audit.json').write_text(
            json.dumps(semantic_audit, ensure_ascii=False, indent=2) + '\n')
    elif custom_native:
        height_audit = base_height_report(run_dir)
    navigation = (explicit_navigation_report(events, jsonl(run_dir / 'navigation_plans.jsonl'))
                  if visual_pixel_protocol else None)
    checks = {
        'model_controller_passed': controller.get('status') == 'passed'
                                   and controller.get('formal_finish_observed') is True,
        'model_simulator_calls_align': calls_align(model, simulator),
        'target_action_selected_by_model': (args.primitive in acts if args.primitive else bool(selected)),
        'target_action_executed': (args.primitive in executed if args.primitive else
                                   any(primitive in executed for primitive in selected)),
        **({'semantic_task_success': semantic_audit['evaluation']['task_success'] is True,
            'semantic_state_log_matches': semantic_audit['private_state_log_matches'],
            'visual_base_height_stable': height_audit['stable'],
            'official_submission_ineligible': run.get('backend', {}).get('official_submission_eligible') is False}
           if high_level else
           {'custom_native_bddl_task_success':
                (run.get('evaluation') or {}).get('custom_task_success') is True,
            'not_claimed_as_public_task_success':
                (run.get('evaluation') or {}).get('official_task_success') is None}
           if custom_native else
           {'official_task_success': (run.get('evaluation') or {}).get('official_task_success') is True}),
        'video_passed': (run.get('video') or {}).get('status') == 'passed',
        'agent_reported_achieved': run.get('agent_outcome') == 'achieved',
        'hand_empty_at_finish': not first_ten or hand_empty_after_events(events),
    }
    checks.update(motion_checks(run, summary, motion))
    if visual_pixel_protocol:
        profile = run['config'].get('agent_profile')
        checks.update({
            'visual_pixel_action_profile': profile in ({'skills','planning'} if
                summary.get('skill_catalog_enabled',summary.get('task_decomposition_skill_enabled'))
                else {'minimal'}),
            'no_named_action_calls': all(name != 'act_semantic' for name, _ in model),
            'model_chose_navigate_to': 'navigate_to' in acts,
            'all_paths_from_explicit_navigate_to': navigation['all_explicit'],
            'navigation_path_recorded': navigation['paths'] > 0,
            **({'visual_base_height_stable': height_audit['stable']} if custom_native else {}),
        })
        if profile == 'planning':
            checks['public_decision_every_robot_tool'] = all(
                isinstance(arguments.get('decision'),dict) and
                set(arguments['decision']) == {'state','active_subgoal','reason','expected'} and
                all(isinstance(value,str) and value.strip()
                    for value in arguments['decision'].values())
                for name,arguments in model if name in
                {'initialize','look','act','finish','list_skills','read_skill',
                 'update_plan','remember','recall'})
    if checks['video_passed']:
        replay = [sys.executable, '-m', 'manipulation_agent.replay', '--run-dir', str(run_dir)]
        if (controller_dir / 'model_events.jsonl').exists():
            replay += ['--controller-dir', str(controller_dir)]
        subprocess.run(replay, cwd=source, check=True)
        subprocess.run([sys.executable, str(source / 'scripts/build_review.py'), str(run_dir),
                        '--controller-dir', str(controller_dir),
                        '--label', f'{"Visible demo" if demo_motion else "No demo motion"} · '
                                   f'{args.primitive or "autonomous actions"} · {run["config"]["task"]}'],
                       cwd=source, check=True)
    report = {'task': run['config']['task'], 'instance': run['config'].get('instance'),
              'seed': run['config'].get('seed'), 'primitive': args.primitive,
              'model': controller.get('model'),
              'reasoning_effort': controller.get('model_reasoning_effort') or summary['reasoning_effort'],
              'demo_motion': (run.get('evaluation') or {}).get('demo_motion'),
              'hand_empty_requirement_applied': first_ten,
              'checks': checks, 'passed': all(checks.values()),
              'semantic_audit': semantic_audit,
              'base_height_audit': height_audit,
              'model_act_sequence': acts,
              'failure_category': ('provider_rate_limit_429' if any(
                  '429 Too Many Requests' in str(event.get('message') or event.get('error') or '')
                  for event in model_events) else None),
              'motion_segments': sum(row.get('status') in {'shown', 'partial_reach'} for row in motion),
              'review_page': str(run_dir / 'review_5view.html') if checks['video_passed'] else None}
    (root / 'demo_audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
