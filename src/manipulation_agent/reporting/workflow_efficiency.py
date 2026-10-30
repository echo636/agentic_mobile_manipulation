"""Offline workflow costs, adapted from yujie PR #6 (bbe9b5f).

Recovery windows and opposite door actions are review candidates, not evidence
of wasted work. No audit result is fed to the policy or task evaluator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from .goal_replay import goal_replacement_review


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def recovery_review_candidates(requests):
    candidates = []
    for index, failed in enumerate(requests):
        if failed['ok'] is not False or failed['action_number'] is None:
            continue
        following = requests[index + 1:]
        end = next((i for i, row in enumerate(following)
                    if row['ok'] is True and row['primitive'] == failed['primitive']), None)
        interval = following[:end + 1] if end is not None else following
        candidates.append({
            'failed_call_id': failed['call_id'], 'primitive': failed['primitive'],
            'error': failed['error'],
            'next_same_primitive_success_call_id': interval[-1]['call_id'] if end is not None else None,
            'following_request_count': len(interval),
            'following_tool_wall_seconds': sum(row['tool_wall_seconds'] or 0 for row in interval),
            'confirmed_recovery': None})
    return candidates


def goal_progress_review(evaluation):
    def best_fraction(options):
        if not options or any(not row or any(type(v) is not bool for v in row) for row in options):
            return None
        return max(sum(row) / len(row) for row in options)

    before = best_fraction(evaluation.get('initial_goal_options'))
    after = best_fraction(evaluation.get('goal_options'))
    return {'initial_best_binding_fraction': before, 'final_best_binding_fraction': after,
            'net_best_fraction_change': after - before if before is not None and after is not None else None,
            'scope': 'Alternative bindings, not temporal subgoals; gains can offset losses.'}


def analyze_events(events, episode, controller=None):
    controller = controller or {}
    results = {e['request_id']: e for e in events
               if e.get('kind') == 'tool_result' and e.get('request_id') is not None}
    calls = [e for e in events if e.get('kind') == 'tool_call']
    requests, skill_reads, rejected_nonmotor, finishes = [], [], [], []
    for call in calls:
        response = results.get(call.get('request_id'))
        result = (response or {}).get('result') or {}
        args = call.get('arguments') or {}
        error = (result.get('error') or {}).get('code')
        if call['name'] == 'read_skill':
            skill_reads.append({'call_id': call['id'], 'name': args.get('name'),
                                'resource': args.get('resource'), 'at': call['at'],
                                'ok': result.get('ok'), 'error': error})
        if call['name'] == 'finish':
            finishes.append({'call_id': call['id'], 'origin': call.get('origin'),
                             'declared_outcome': args.get('outcome'), 'reason': args.get('reason'),
                             'ok': result.get('ok'), 'closed': result.get('closed')})
        if call['name'] not in ('act', 'look'):
            if result.get('ok') is False:
                rejected_nonmotor.append({'call_id': call['id'], 'tool': call['name'], 'error': error})
            continue
        duration = ((datetime.fromisoformat(response['at']) - datetime.fromisoformat(call['at']))
                    .total_seconds() if response else None)
        requests.append({'number': len(requests) + 1, 'call_id': call['id'],
                         'action_number': result.get('actions_used'),
                         'agent_step': (result.get('execution_budget') or {}).get('steps_used'),
                         'primitive': args.get('primitive', call['name']),
                         'ok': result.get('ok'), 'error': error,
                         'tool_wall_seconds': duration, 'target': args.get('target')})
    actions = [row for row in requests if row['action_number'] is not None]
    errors = Counter(row['error'] or 'unspecified' for row in actions if row['ok'] is False)
    evaluation = episode.get('evaluation') or {}
    config = episode.get('config') or {}
    return {
        'run_id': episode.get('run_id'), 'task': config.get('task'),
        'instance': config.get('instance'), 'seed': config.get('seed'),
        'model': controller.get('model') or config.get('controller'),
        'reasoning_effort': controller.get('model_reasoning_effort') or config.get('model_reasoning_effort'),
        'source': episode.get('source'), 'backend': episode.get('backend'),
        'execution': {key: episode.get(key) for key in
                      ('host', 'interpreter', 'gpu_uuid', 'pid', 'unit', 'output_path')},
        'task_success': episode.get('task_success'),
        'official_task_success': evaluation.get('official_task_success'),
        'goal_satisfaction_fraction': evaluation.get('goal_satisfaction_fraction'),
        'goal_progress_review': goal_progress_review(evaluation),
        'finish_requests': finishes,
        'execution_limit_policy': episode.get('execution_limit_policy'),
        'action_requests': len(requests), 'actions': len(actions),
        'runtime_actions': episode.get('actions'), 'runtime_agent_steps': episode.get('agent_steps'),
        'action_count_matches_runtime': len(actions) == episode['actions'] if 'actions' in episode else None,
        'primitive_counts': dict(Counter(row['primitive'] for row in actions)),
        'failed_actions': sum(errors.values()), 'failure_counts': dict(errors),
        'unanswered_motor_calls': sum(row['ok'] is None for row in requests),
        'pre_dispatch_rejections': [row for row in requests if row['ok'] is False and row['action_number'] is None],
        'unclassified_motor_responses': sum(row['ok'] is True and row['action_number'] is None for row in requests),
        'motor_tool_wall_seconds': sum(row['tool_wall_seconds'] or 0 for row in requests),
        'navigation_distance_m': evaluation.get('ideal_navigation_distance_m'),
        'opposite_door_action_candidates': [
            [left['call_id'], right['call_id']] for left, right in zip(requests, requests[1:])
            if left['ok'] and right['ok'] and {left['primitive'], right['primitive']} == {'open', 'close'}],
        'recovery_review_candidates': recovery_review_candidates(requests),
        'goal_replacements': goal_replacement_review(events),
        'skill_read_timeline': skill_reads, 'rejected_nonmotor_calls': rejected_nonmotor,
        'timeline': requests,
        'counting_contract': 'actions count executor responses carrying actions_used, including execution failures. '
                             'action_requests include schema rejections and unanswered calls. agent_steps count '
                             'admitted attempts, including validation failures; these are different counters.',
        'review_contract': 'Same-primitive success may involve another target. Recovery windows may overlap; '
                           'never sum them as recovery cost. Opposite door actions can be necessary. '
                           'Skill delivery and authored plan changes do not establish planning quality or causality. '
                           'Model finish declarations are separate from independent evaluator success.'}


def analyze(run_dir, controller_dir=None):
    with (run_dir / 'events.jsonl').open() as stream:
        events = [json.loads(line) for line in stream if line.strip()]
    controller = read_json(controller_dir / 'controller.json') if controller_dir else {}
    return analyze_events(events, read_json(run_dir / 'run.json'), controller)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--controller-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.run_dir, args.controller_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in
                      ('run_id', 'task_success', 'actions', 'failed_actions', 'action_requests')}))


if __name__ == '__main__':
    main()
