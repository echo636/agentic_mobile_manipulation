"""Summarize recorded workflow costs without prescribing an oracle policy.

Door-action pairs are review candidates, not proven redundant actions: different
doors or new visual evidence can make an opposite pair necessary.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def recovery_review_candidates(requests):
    """Locate review intervals, without inferring target identity or causality.

    A later success of the same primitive can concern another object. Intervals
    can overlap and must not be summed as a measured recovery-cost total.
    """
    candidates=[]
    for index, failed in enumerate(requests):
        if failed['ok'] is not False or failed['action_number'] is None:
            continue
        following=requests[index+1:]
        end=next((i for i,row in enumerate(following)
                  if row['ok'] is True and row['primitive']==failed['primitive']),None)
        interval=following[:end+1] if end is not None else following
        candidates.append({
            'failed_call_id':failed['call_id'], 'primitive':failed['primitive'],
            'error':failed['error'],
            'next_same_primitive_success_call_id':interval[-1]['call_id'] if end is not None else None,
            'following_request_count':len(interval),
            'following_budget_actions':sum(row['action_number'] is not None for row in interval),
            'following_tool_wall_seconds':sum(row['tool_wall_seconds'] or 0 for row in interval),
            'confirmed_recovery':None,
            'review_required':'Check RGB, target identity and intervening actions; same-primitive success is not proof of recovery. Intervals may overlap; do not sum as recovery cost.'})
    return candidates


def goal_progress_review(evaluation):
    """Compare best alternative bindings, not successive time steps."""
    def best_fraction(options):
        if not options or any(not row or any(type(v) is not bool for v in row)
                              for row in options):
            return None
        return max(sum(row) / len(row) for row in options)

    initial = evaluation.get('initial_goal_options')
    final = evaluation.get('goal_options')
    before, after = best_fraction(initial), best_fraction(final)
    return {
        'initial_best_binding_fraction': before,
        'final_best_binding_fraction': after,
        'net_best_fraction_change': after - before if before is not None and after is not None else None,
        'goal_options_unchanged': initial == final if initial and final else None,
        'interpretation': 'Net fraction change is not a completed-subgoal count: bindings may differ and gains can offset losses. Missing initial evidence stays unknown. Initially satisfied conditions are not robot progress.'}


def analyze(trial: Path):
    run = trial / 'run'
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()
              if line.strip()]
    results = {row['request_id']: row for row in events if row.get('kind') == 'tool_result'}
    calls = [row for row in events if row.get('kind') == 'tool_call']
    requests, rejected_nonmotor, rejected_motor, skill_reads = [], [], [], []
    for call in calls:
        response = results.get(call['request_id'])
        result = response.get('result', {}) if response else {}
        if call['name']=='read_skill':
            arguments=call.get('arguments',{})
            status=('successful' if result.get('ok') is True else
                    'rejected' if result.get('ok') is False else 'unanswered')
            skill_reads.append({'call_id':call['id'],
                'response_id':response.get('id') if response else None,
                'at':call['at'],'status':status,
                'name':result.get('name',arguments.get('name')),
                'resource':result.get('resource',arguments.get('resource')),
                'bundle_sha256':result.get('bundle_sha256') if status=='successful' else None,
                'error':result.get('error',{}).get('code')})
        if call['name'] not in ('act', 'look'):
            if response and result.get('ok') is False:
                rejected_nonmotor.append({'call_id': call['id'], 'tool': call['name'],
                                          'error': result.get('error')})
            continue
        args = call.get('arguments', {})
        duration = ((datetime.fromisoformat(response['at']) - datetime.fromisoformat(call['at']))
                    .total_seconds() if response else None)
        action_number = result.get('actions_used') if response else None
        if response and action_number is None and result.get('ok') is False:
            rejected_motor.append({'call_id': call['id'], 'tool': call['name'],
                                   'error': result.get('error')})
        requests.append({'number': len(requests) + 1, 'call_id': call['id'],
                        'action_number': action_number,
                        'primitive': args.get('primitive', call['name']),
                        'ok': result.get('ok') if response else None,
                        'error': result.get('error', {}).get('code'),
                        'tool_wall_seconds': duration, 'target': args.get('target'),
                        'decision': args.get('decision'),
                        'grasped_category': result.get('effect', {}).get('grasped_category')})
    # Budgeted actions include executor failures, but not pre-dispatch schema
    # rejections. Keep every request in the timeline for latency and recovery.
    actions = [row for row in requests if row['action_number'] is not None]
    primitive_counts = Counter(row['primitive'] for row in actions)
    failure_counts = Counter(row['error'] or 'unspecified' for row in actions if row['ok'] is False)
    pairs = [{'actions': [left['number'], right['number']],
              'call_ids': [left['call_id'], right['call_id']],
              'review_required': 'Check target identity, RGB and information gained before calling redundant'}
             for left, right in zip(requests, requests[1:])
             if left['ok'] and right['ok']
             and {left['primitive'], right['primitive']} == {'open', 'close'}]
    episode = read_json(run / 'run.json')
    evaluation = episode.get('evaluation') or {}
    summary, audit = read_json(trial / 'summary.json'), read_json(trial / 'demo_audit.json')
    skill = read_json(run / 'skill_manifest.json')
    controller_events = trial / 'controller' / 'model_events.jsonl'
    service_errors = Counter()
    if controller_events.exists():
        for line in controller_events.read_text().splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get('type') == 'error':
                message = str(event.get('message', '')).lower()
                category = ('server_overload' if 'overloaded' in message else
                            'stream_disconnected' if 'stream disconnected' in message else
                            'other_controller_error')
                service_errors[category] += 1
    return {'trial': trial.name, 'model': summary.get('model'),
            'reasoning_effort': summary.get('reasoning_effort'),
            'task': summary.get('task'), 'instance': summary.get('instance'),
            'seed': summary.get('seed'), 'bddl_sha256': summary.get('bddl_sha256'),
            'skill_bundle_sha256': skill.get('bundle_sha256'),
            'formal_finish_observed': summary.get('formal_finish_observed'),
            'task_success': evaluation.get('task_success'),
            'official_task_success': evaluation.get('official_task_success'),
            'goal_satisfaction_fraction': evaluation.get('goal_satisfaction_fraction'),
            'goal_progress_review': goal_progress_review(evaluation),
            'audit_passed': audit.get('passed'), 'audit_checks': audit.get('checks'),
            'actions': len(actions), 'action_requests': len(requests),
            'runtime_actions': episode.get('actions'),
            'action_count_matches_runtime': (len(actions) == episode['actions']
                                             if 'actions' in episode else None),
            'primitive_counts': dict(primitive_counts),
            'failed_actions': sum(failure_counts.values()), 'failure_counts': dict(failure_counts),
            'unanswered_motor_calls': sum(row['ok'] is None for row in requests),
            'unclassified_motor_responses': sum(row['ok'] is True and row['action_number'] is None
                                                for row in requests),
            'sim_steps': episode.get('sim_steps'),
            'volume_sampling_physics_steps': evaluation.get('volume_sampling_physics_steps'),
            'wall_seconds': episode.get('wall_seconds'),
            'controller_error_events': dict(service_errors),
            'motor_tool_wall_seconds': sum(row['tool_wall_seconds'] or 0 for row in requests),
            'navigation_distance_m': evaluation.get('ideal_navigation_distance_m'),
            'opposite_door_action_candidates': pairs,
            'recovery_review_candidates': recovery_review_candidates(requests),
            'skill_read_counts': {status:sum(row['status']==status for row in skill_reads)
                                 for status in ('successful','rejected','unanswered')},
            'skill_read_timeline': skill_reads,
            'skill_exposure_contract': 'Successful read responses establish resource delivery, not instruction compliance or causal benefit. Rejected and unanswered requests are separate.',
            'rejected_motor_calls': rejected_motor,
            'rejected_nonmotor_calls': rejected_nonmotor, 'timeline': requests,
            'counting_contract': 'actions: responses with actions_used; action_requests/timeline: all act/look attempts; motor_tool_wall_seconds includes rejected request latency; timeline number is request ordinal, action_number is runtime budget counter',
            'scope': 'Recorded costs; not an optimal plan, deletion proof or causal comparison'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trials', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = [analyze(path.resolve()) for path in args.trials]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep comparisons immutable; use a new filename after additional runs.
    with args.output.open('x') as stream:
        json.dump({'trials': reports}, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    for row in reports:
        print(json.dumps({key: row[key] for key in ('trial', 'task_success', 'audit_passed',
              'actions', 'failed_actions', 'primitive_counts', 'skill_bundle_sha256')},
              ensure_ascii=False))


if __name__ == '__main__':
    main()
