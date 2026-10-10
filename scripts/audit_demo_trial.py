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
    read_only = {'list_skills', 'read_skill'}
    ordered = lambda calls: [(name, args) for name, args in calls if name not in read_only]
    counted = lambda calls: Counter((name, json.dumps(args, sort_keys=True))
                                    for name, args in calls if name in read_only)
    return ordered(model) == ordered(simulator) and counted(model) == counted(simulator)


def hand_empty_after_events(events):
    """Track committed carry/release effects from the simulator's ordered act results."""
    held = False
    for event in events:
        if event.get('kind') != 'tool_result' or event.get('name') != 'act':
            continue
        result = event.get('result') or {}
        if not result.get('ok'):
            continue
        primitive = (result.get('effect') or {}).get('primitive')
        if primitive == 'grasp':
            held = True
        elif primitive in {'place_inside', 'place_on_top', 'place_under',
                           'place_next_to', 'attach', 'hang', 'release'}:
            held = False
    return not held


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
    acts = [arguments.get('primitive') for name, arguments in model if name == 'act']
    effects = [event.get('result') or {} for event in events
               if event.get('kind') == 'tool_result' and event.get('name') == 'act']
    motion = jsonl(run_dir / 'demo_motion.jsonl')
    source = Path(__file__).resolve().parents[1]
    catalog = json.loads((source / 'src/manipulation_agent/tasks.json').read_text())['tasks']
    first_ten = run['config']['task'] in list(catalog)[:10]
    non_manipulation = {'navigate_to', 'look', 'wait', 'release'}
    selected = [primitive for primitive in acts if primitive not in non_manipulation]
    executed = [(effect.get('effect') or {}).get('primitive') for effect in effects if effect.get('ok')]
    checks = {
        'model_controller_passed': controller.get('status') == 'passed'
                                   and controller.get('formal_finish_observed') is True,
        'model_simulator_calls_align': calls_align(model, simulator),
        'target_action_selected_by_model': (args.primitive in acts if args.primitive else bool(selected)),
        'target_action_executed': (args.primitive in executed if args.primitive else
                                   any(primitive in executed for primitive in selected)),
        'official_task_success': (run.get('evaluation') or {}).get('official_task_success') is True,
        'video_passed': (run.get('video') or {}).get('status') == 'passed',
        'visible_motion_recorded': any(row.get('status') == 'shown' for row in motion),
        'agent_reported_achieved': run.get('agent_outcome') == 'achieved',
        'hand_empty_at_finish': not first_ten or hand_empty_after_events(events),
    }
    if checks['video_passed']:
        replay = [sys.executable, '-m', 'manipulation_agent.replay', '--run-dir', str(run_dir)]
        if (controller_dir / 'model_events.jsonl').exists():
            replay += ['--controller-dir', str(controller_dir)]
        subprocess.run(replay, cwd=source, check=True)
        subprocess.run([sys.executable, str(source / 'scripts/build_review.py'), str(run_dir),
                        '--controller-dir', str(controller_dir),
                        '--label', f'Visible demo · {args.primitive or "autonomous actions"} · {run["config"]["task"]}'],
                       cwd=source, check=True)
    report = {'task': run['config']['task'], 'instance': run['config'].get('instance'),
              'seed': run['config'].get('seed'), 'primitive': args.primitive,
              'model': controller.get('model'),
              'reasoning_effort': controller.get('model_reasoning_effort') or summary['reasoning_effort'],
              'demo_motion': (run.get('evaluation') or {}).get('demo_motion'),
              'hand_empty_requirement_applied': first_ten,
              'checks': checks, 'passed': all(checks.values()),
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
