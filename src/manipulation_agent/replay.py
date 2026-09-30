"""Build an offline, event-aligned replay from recorded evidence, never resimulation."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import html
import json
from pathlib import Path

from .audit import audit_episode
from .records import now, write_json


def lines(path: Path) -> list[dict]:
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def build_replay(run_dir: Path, controller_dir: Path | None = None) -> dict:
    run = json.loads((run_dir / 'run.json').read_text())
    events = lines(run_dir / 'events.jsonl')
    captures = lines(run_dir / 'captures.jsonl')
    images = {i['image_ref']: {**i, 'env_steps': c['env_steps']} for c in captures for i in c['images']}
    rgb = run.get('config', {}).get('observation_mode') == 'rgb_only'
    video_path = run_dir / 'video.json'
    video = json.loads(video_path.read_text()) if video_path.exists() else None
    video_markers = {m['request_id']:m for m in (video or {}).get('markers', [])}
    audit = audit_episode(run_dir, controller_dir) if controller_dir else None

    def observation(value):
        if not value:
            return None
        views = []
        for image in value.get('images', []):
            if rgb:
                saved = images.get(image['image_ref'], {})
                item = {**image, 'file': saved.get('file'), 'env_steps': saved.get('env_steps')}
            else:
                camera = image.get('camera', '')
                view = 'head' if 'zed' in camera else ('left_wrist' if 'left' in camera else 'right_wrist')
                item = {'view': view, 'file': image.get('path'), 'image_ref': image.get('path'),
                        'env_steps': value.get('sim_steps')}
            if item.get('file'):
                path = (run_dir / item['file']).resolve()
                if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
                    item['file'] = None
            views.append(item)
        return {'revision': value.get('revision'), 'images': views}

    # The constructor's episode_started image has NOT been returned to the agent.
    last_observation = None
    results = {e['request_id']: e for e in events if e['kind'] == 'tool_result'}
    decisions = []; plan = []; notes = {}; steps = []
    calls = [e for e in events if e['kind'] == 'tool_call']
    start = datetime.fromisoformat(calls[0]['at']) if calls else None
    for call in calls:
        event = results.get(call['request_id']); result = event.get('result') if event else None
        args = call['arguments']; name = call['name']
        before = copy.deepcopy(last_observation)
        new_obs = observation(result.get('observation')) if result else None
        if new_obs is not None:
            last_observation = new_obs
        if name in {'update_plan', 'remember', 'finish'}:
            text = args.get('reason') if name != 'remember' else args.get('text')
            if text:
                decisions.append({'text': text, 'source_tool': name, 'source_event': call['id'],
                                  'accepted': bool(result and result.get('ok'))})
        if result and result.get('ok'):
            if name == 'update_plan': plan = copy.deepcopy(args['subgoals'])
            if name == 'remember': notes[args['key']] = {'text': args['text'], 'revision': args['revision']}
        seconds = (datetime.fromisoformat(event['at']) - datetime.fromisoformat(call['at'])).total_seconds() if event else None
        steps.append({'index': len(steps) + 1, 'event_id': call['id'], 'result_event_id': event['id'] if event else None,
                      'tool': name, 'arguments': args, 'result': result,
                      'request_id':call['request_id'], 'decision':copy.deepcopy(args.get('decision')),
                      'at': call['at'], 'elapsed_seconds': (datetime.fromisoformat(call['at']) - start).total_seconds(),
                      'video_start_seconds': video_markers.get(call['request_id'],{}).get('seconds'),
                      'tool_seconds': seconds, 'is_motor_action': name in {'act', 'look'},
                      'status': 'missing_result' if result is None else ('passed' if result.get('ok') else 'failed'),
                      'before': before, 'after': copy.deepcopy(last_observation), 'new_observation': new_obs is not None,
                      'decisions': copy.deepcopy(decisions[-4:]), 'plan': copy.deepcopy(plan), 'memory': copy.deepcopy(notes)})
    # No private executor events or hidden model reasoning are exported into the replay.
    return {'schema_version': 1, 'generated_at': now(), 'kind': 'discrete_observation_action_replay',
            'run_id': run['run_id'], 'status': run['status'], 'rgb_only': rgb,
            'instruction': run.get('config', {}).get('instruction'), 'config': run.get('config', {}),
            'source': run.get('source', {}), 'execution': {k: run.get(k) for k in
                ('host', 'pid', 'interpreter', 'unit', 'gpu_uuid', 'output_path', 'actions', 'tool_calls', 'sim_steps', 'wall_seconds')},
            'backend': run.get('backend', {}),
            'model': json.loads((controller_dir / 'controller.json').read_text()).get('model') if controller_dir else None,
            'audit': audit, 'steps': steps, 'video':video,
            'evaluation_offline_only': {'task_success': run.get('task_success'), 'evaluation': run.get('evaluation'),
                                        'agent_outcome': run.get('agent_outcome'), 'finish_reason': run.get('finish_reason')},
            'failure': run.get('failure') or run.get('error'),
            'limitations': ['仅回放已记录的工具边界RGB；未录制的运动中间帧不生成、不插值。',
                '决策摘要仅来自模型显式提交的计划、记忆和结束说明；不展示隐藏推理，也不事后补写动机。',
                '独立评分仅供实验结束后审阅，没有通过工具提供给模型。']}


def render_replay(run_dir: Path, controller_dir: Path | None = None) -> dict:
    data = build_replay(run_dir, controller_dir)
    write_json(run_dir / 'replay.json', data)
    if data['audit']:
        write_json(run_dir / 'replay_audit.json', data['audit'])
    assets = Path(__file__).with_name('replay_assets')
    template = (assets / 'index.html').read_text()
    # Escape < so task text cannot close a JSON script element.
    embedded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    page = template.replace('@@TITLE@@', html.escape(data['run_id']))
    page = page.replace('@@CSS@@', (assets / 'style.css').read_text())
    page = page.replace('@@DATA@@', embedded).replace('@@JS@@', (assets / 'player.js').read_text())
    if not data['audit']:
        page = page.replace(' href="replay_audit.json"', '')
    if not data['video']:
        page = page.replace(' href="video.json"', '')
    (run_dir / 'replay.html').write_text(page)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--controller-dir', type=Path)
    args = parser.parse_args()
    data = render_replay(args.run_dir, args.controller_dir)
    print(json.dumps({'run_id': data['run_id'], 'steps': len(data['steps']), 'replay': str(args.run_dir / 'replay.html')}))


if __name__ == '__main__':
    main()
