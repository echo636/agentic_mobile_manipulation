"""The six efficiency metrics of a run in one table: success, steps, robot time, route length, wall time, tokens.

Works on a model episode (run.json + events.jsonl, optionally a controller directory for tokens) and on a
privileged reference (run_record.json + reference_trajectory.jsonl). Robot time comes from the executor's robot
clock when the run has one; for older runs it is recomputed from the executor's private logs by the same rules
(runtime/robot_clock.py), which is stated in the source column.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path

from ..runtime.robot_clock import (MANIPULATION_SECONDS, NOT_MANIPULATION, TURN_RATE_RAD_S, VERSION,
                                   route_motion)

ROBOT_TIME_KEYS = ('robot_time_seconds', 'travel_distance_m', 'travel_seconds', 'manipulation_count',
                   'manipulation_seconds', 'wait_seconds')


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def read_jsonl(path):
    if not path.exists():
        return []
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def unix(value):
    return float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value).timestamp()


def actions_from_events(events):
    """Executor actions of a model episode: one row per act/look call that reached the executor."""
    results = {e['request_id']: e for e in events
               if e.get('kind') == 'tool_result' and e.get('request_id') is not None}
    actions, current = [], None
    for event in events:
        if event.get('kind') == 'tool_call' and event.get('name') in ('act', 'look'):
            args = event.get('arguments') or {}
            result = (results.get(event.get('request_id')) or {}).get('result') or {}
            current = {'t': unix(event['at']), 'primitive': args.get('primitive', event['name']),
                       'ok': result.get('ok'), 'dispatched': result.get('actions_used') is not None,
                       'yaw_degrees': args.get('yaw_degrees'), 'sim_seconds': args.get('wait_seconds') or 5.,
                       'tool_seconds': (unix(results[event['request_id']]['at']) - unix(event['at'])
                                        if event.get('request_id') in results else None)}
            actions.append(current)
        elif event.get('kind') == 'private_executor_result' and current is not None:
            seconds = (event.get('details') or {}).get('sim_seconds')
            if isinstance(seconds, (int, float)):
                current['sim_seconds'] = seconds
    return actions


def actions_from_reference(rows):
    return [{'t': unix(row.get('started_at', row['time'])), 'primitive': row['primitive'],
             'ok': row.get('status') == 'passed', 'dispatched': True,
             'yaw_degrees': (row.get('options') or {}).get('yaw_degrees'),
             'sim_seconds': ((row.get('result') or {}).get('sim_seconds')
                             or (row.get('options') or {}).get('seconds') or 5.),
             'tool_seconds': row.get('seconds')}
            for row in rows if row.get('event') == 'action_finished']


def offline_robot_time(run_dir, actions):
    """Robot time of a run without a robot clock, from navigation_plans.jsonl and its action records.

    Every planned navigation is taken as committed (its record is marked failed only if the commit raised).
    The final yaw is re-derived as the executor does: toward the target from the endpoint, or from the start for
    an exact floor point. The yaw before the first motion is unknown, so that first turn is not charged.
    Failed manipulations are not charged, because old logs do not say whether they started physically.
    """
    plans = [row for row in read_jsonl(run_dir / 'navigation_plans.jsonl') if row.get('status') == 'planned']
    if not plans and not actions:
        return None
    timeline = sorted([(unix(row['at']), 0, i, row) for i, row in enumerate(plans)] +
                      [(a['t'], 1, i, a) for i, a in enumerate(actions)
                       if a['primitive'] == 'look' and a['ok'] and a['yaw_degrees'] is not None],
                      key=lambda item: item[:3])
    yaw, distance, seconds, turn = None, 0., 0., 0.
    for _, kind, _, row in timeline:
        if kind == 1:
            radians = math.radians(row['yaw_degrees'])
            turn += abs(radians)
            seconds += abs(radians) / TURN_RATE_RAD_S
            yaw = None if yaw is None else yaw + radians
            continue
        exact_floor_point = row.get('mode') == 'point' and row.get('floor_click_snap_m') is None
        origin = row['start'] if exact_floor_point else row['endpoint']
        end_yaw = math.atan2(row['target'][1] - origin[1], row['target'][0] - origin[0])
        d, t, s = route_motion(row['path'], yaw, end_yaw)
        distance, turn, seconds, yaw = distance + d, turn + t, seconds + s, end_yaw
    completed = sum(1 for a in actions if a['ok'] and a['primitive'] not in NOT_MANIPULATION)
    waited = sum(float(a['sim_seconds']) for a in actions if a['ok'] and a['primitive'] == 'wait')
    return {'robot_time_seconds': seconds + completed * MANIPULATION_SECONDS + waited,
            'travel_distance_m': distance, 'travel_seconds': seconds, 'manipulation_count': completed,
            'manipulation_seconds': completed * MANIPULATION_SECONDS, 'wait_seconds': waited,
            'turn_seconds': turn / TURN_RATE_RAD_S, 'committed_motions': len(timeline),
            'note': 'recomputed offline; first turn and failed manipulation attempts are not charged'}


def robot_time_review(run_dir, evaluation, actions):
    """Robot time of a run and where the numbers come from."""
    for source, record in (('robot_clock', evaluation or {}), ('robot_clock', read_json(run_dir / 'run.json'))):
        if record.get('robot_time_seconds') is not None:
            return {'source': source, 'policy': (record.get('robot_clock') or {}).get('version'),
                    **{key: record.get(key) for key in ROBOT_TIME_KEYS},
                    'turn_seconds': (record.get('robot_clock') or {}).get('turn_seconds')}
    ledger = read_jsonl(run_dir / 'robot_clock.jsonl')
    if ledger:  # the run had a clock but ended without an evaluation
        return {'source': 'robot_clock_ledger', 'policy': VERSION,
                'robot_time_seconds': ledger[-1]['total_s'],
                'travel_distance_m': sum(r['travel_m'] for r in ledger),
                'travel_seconds': sum(r['travel_s'] for r in ledger),
                'manipulation_count': round(sum(r['manipulation_s'] for r in ledger) / MANIPULATION_SECONDS),
                'manipulation_seconds': sum(r['manipulation_s'] for r in ledger),
                'wait_seconds': sum(r['wait_s'] for r in ledger),
                'turn_seconds': sum(m['turn_seconds'] for r in ledger for m in r['motions'])}
    offline = offline_robot_time(run_dir, actions)
    if offline:
        return {'source': 'offline_executor_logs', 'policy': VERSION, **offline}
    return {'source': None, **{key: None for key in ROBOT_TIME_KEYS}, 'turn_seconds': None}


def token_usage(controller_dir):
    """Summed usage of the controller's completed turns, or {} without a controller directory."""
    if controller_dir is None:
        return {}
    from ..clients.events import event_path
    usage = {}
    for event in read_jsonl(event_path(controller_dir)):
        if event.get('type') == 'turn.completed':
            for key, value in (event.get('usage') or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
    return usage


def six_metrics(run_dir, controller_dir=None, label=None):
    run_dir = Path(run_dir)
    if (run_dir / 'reference_trajectory.jsonl').exists():
        kind, record = 'reference', read_json(run_dir / 'run_record.json')
        actions = actions_from_reference(read_jsonl(run_dir / 'reference_trajectory.jsonl'))
        evaluation = record.get('evaluation') or {}
        success = record.get('status') == 'passed' and evaluation.get('task_success') is True
        wall = (record['finished_at_unix'] - record['started_at_unix']
                if record.get('finished_at_unix') and record.get('started_at_unix') else None)
    else:
        kind, record = 'model', read_json(run_dir / 'run.json')
        actions = actions_from_events(read_jsonl(run_dir / 'events.jsonl'))
        evaluation = record.get('evaluation') or {}
        success = record.get('task_success') is True
        wall = record.get('wall_seconds')
    tool = sum(a['tool_seconds'] or 0 for a in actions)
    usage = token_usage(Path(controller_dir) if controller_dir else None)
    return {'label': label or run_dir.name, 'kind': kind, 'run_dir': str(run_dir), 'task_success': success,
            'steps': len(actions), 'failed_steps': sum(a['ok'] is False for a in actions),
            'robot_time': robot_time_review(run_dir, evaluation, actions),
            'endpoint_displacement_m': evaluation.get('ideal_navigation_distance_m'),
            'wall_seconds': wall, 'tool_wall_seconds': tool,
            'model_wall_seconds': wall - tool if kind == 'model' and wall is not None else None,
            'usage': usage}


def _number(value, digits=0):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown_table(rows):
    lines = ['| 运行 | 完成 | 步骤数（失败） | 机器人时间 s（路程 + 操作 + 等待） | 轨迹长度 m | '
             '墙钟 s（模型 + 工具） | token（输入 / 其中缓存 / 输出） | 时间来源 |',
             '|---|---|---:|---:|---:|---:|---:|---|']
    for row in rows:
        time = row['robot_time']
        usage = row['usage']
        tokens = ('—' if not usage else ' / '.join(_number(usage.get(key)) for key in
                                                    ('input_tokens', 'cached_input_tokens', 'output_tokens')))
        wall = (f"{_number(row['wall_seconds'])}（{_number(row['model_wall_seconds'])} + "
                f"{_number(row['tool_wall_seconds'])}）")
        source = {'robot_clock': '执行器计时', 'robot_clock_ledger': '执行器计时（无评估，取流水）',
                  'offline_executor_logs': '事后补算', None: '无'}[time['source']]
        lines.append(
            f"| {row['label']} | {'是' if row['task_success'] else '否'} | "
            f"{row['steps']}（{row['failed_steps']}） | "
            f"{_number(time['robot_time_seconds'])}（{_number(time['travel_seconds'])} + "
            f"{_number(time['manipulation_seconds'])} + {_number(time['wait_seconds'])}） | "
            f"{_number(time['travel_distance_m'], 1)} | {wall} | {tokens} | {source} |")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+',
                        help='RUN_DIR, RUN_DIR:CONTROLLER_DIR, or LABEL=RUN_DIR[:CONTROLLER_DIR]')
    parser.add_argument('--json', type=Path, help='Also write the full rows to this new file')
    args = parser.parse_args()
    rows = []
    for item in args.runs:
        label, _, paths = item.rpartition('=')
        run_dir, _, controller_dir = paths.partition(':')
        rows.append(six_metrics(run_dir, controller_dir or None, label or None))
    if args.json:
        with args.json.open('x') as stream:
            json.dump(rows, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    print(markdown_table(rows))


if __name__ == '__main__':
    main()
