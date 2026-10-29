"""Summarize first-ten demo attempts without mixing different source/config versions.

Read-only: accepts the immutable trial directory and writes no run artifacts.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def error_codes(path):
    counts = Counter()
    if not path.exists():
        return {}
    for line in path.open():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('kind') == 'tool_result' and event.get('name') == 'act':
            result = event.get('result') or {}
            if not result.get('ok'):
                counts[(result.get('error') or {}).get('code', 'unknown')] += 1
    return dict(sorted(counts.items()))


def load_attempt(path):
    name = path.name
    match = re.fullmatch(r'(\d\d)_(.+)_r(\d+)', name)
    if match is None:
        return None
    number, task, attempt = match.groups()
    audit = read_json(path / 'demo_audit.json')
    run = read_json(path / 'run' / 'run.json')
    controller = read_json(path / 'controller' / 'controller.json')
    if not audit or not run:
        return None
    source = run.get('source') or {}
    backend = run.get('backend') or {}
    config = run.get('config') or {}
    identity = {
        'source_sha256': source.get('source_sha256') or source.get('commit'),
        'skill_bundle_sha256': run.get('skill_bundle_sha256'),
        'model': audit.get('model'),
        'reasoning_effort': audit.get('reasoning_effort'),
        'instance': config.get('instance'),
        'seed': config.get('seed'),
        'image_size': backend.get('image_size'),
        'camera_rig': backend.get('camera_rig'),
        'demo_motion': backend.get('demo_motion'),
        'executor': backend.get('executor'),
        'omnigibson_source_commit': backend.get('omnigibson_source_commit'),
        'assets': backend.get('assets'),
        'input_hashes': backend.get('input_hashes'),
        'inside_placement': backend.get('inside_placement'),
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    video = run.get('video') or {}
    fps = video.get('fps') or 0
    frames = video.get('frame_count') or 0
    deadline = run.get('episode_deadline_unix')
    finished = run.get('execution_finished_at_unix')
    finish_reason = run.get('finish_reason') or ''
    remaining = max(0, deadline - finished) if deadline and finished else None
    premature_time_claim = (run.get('agent_outcome') == 'blocked' and remaining is not None
                            and remaining > 600 and any(term in finish_reason.casefold()
                            for term in ('execution window', 'time limit', 'time budget',
                                         'deadline', 'out of time', '时间不够', '时间不足')))
    return {
        'number': int(number), 'task': task, 'attempt': int(attempt),
        'passed': audit.get('passed') is True, 'source_commit': source.get('commit'),
        'configuration': fingerprint, 'model': audit.get('model'),
        'reasoning_effort': audit.get('reasoning_effort'),
        'outcome': run.get('agent_outcome'), 'official_task_success':
            (run.get('evaluation') or {}).get('official_task_success') is True,
        'action_errors': error_codes(path / 'run' / 'events.jsonl'),
        'motion_segments': audit.get('motion_segments'),
        'wall_minutes': round((run.get('wall_seconds') or 0) / 60, 1),
        'video_seconds': round(frames / fps, 1) if fps else None,
        'premature_time_claim': premature_time_claim,
        'controller_status': controller.get('status'),
    }


def report(root):
    attempts = [row for path in root.iterdir() if path.is_dir()
                if (row := load_attempt(path)) is not None and row['number'] <= 10]
    tasks = []
    for number in range(1, 11):
        rows = sorted((row for row in attempts if row['number'] == number),
                      key=lambda row: row['attempt'])
        if not rows:
            continue
        latest = rows[-1]
        cohort = [row for row in rows if row['configuration'] == latest['configuration']]
        tasks.append({
            'number': number, 'task': latest['task'],
            'historical_audited_attempts': len(rows),
            'historical_passing_attempts': [row['attempt'] for row in rows if row['passed']],
            'latest': latest,
            'latest_configuration': {'fingerprint': latest['configuration'],
                'audited_attempts': len(cohort),
                'passing_attempts': sum(row['passed'] for row in cohort)},
        })
    return {'passed_tasks': sum(bool(task['historical_passing_attempts']) for task in tasks),
            'total_tasks': len(tasks), 'tasks': tasks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trials', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    data = report(args.trials)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"Independently audited task successes: {data['passed_tasks']}/{data['total_tasks']}")
        print('Task | Historical pass attempts | Latest run | Latest source/config pass/audited | Errors')
        for item in data['tasks']:
            latest, cohort = item['latest'], item['latest_configuration']
            passes = ','.join(map(str, item['historical_passing_attempts'])) or '-'
            errors = json.dumps(latest['action_errors'], sort_keys=True)
            print(f"{item['number']:02d} {item['task']} | {passes} | r{latest['attempt']} "
                  f"{'pass' if latest['passed'] else 'fail'} | "
                  f"{cohort['passing_attempts']}/{cohort['audited_attempts']} "
                  f"({cohort['fingerprint']}) | {errors}")


if __name__ == '__main__':
    main()
