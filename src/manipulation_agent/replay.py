"""Build an offline, event-aligned replay from recorded evidence, never resimulation."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import html
import json
import subprocess
from pathlib import Path

from .audit import audit_episode
from .records import now, write_json, read_run
from .transcript import build_transcript
from .clients.events import event_path


def lines(path: Path) -> list[dict]:
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def build_replay(run_dir: Path, controller_dir: Path | None = None) -> dict:
    run = read_run(run_dir)
    events = lines(run_dir / 'events.jsonl')
    captures = lines(run_dir / 'captures.jsonl')
    images = {i['image_ref']: {**i, 'env_steps': c['env_steps']} for c in captures for i in c['images']}
    rgb = run.get('config', {}).get('observation_mode') == 'rgb_only'
    video_path = run_dir / 'video.json'
    video = json.loads(video_path.read_text()) if video_path.exists() else None
    explained_path = run_dir / 'explained_video.json'
    explained = json.loads(explained_path.read_text()) if explained_path.exists() else None
    walltime_path = run_dir / 'walltime_video.json'
    walltime = json.loads(walltime_path.read_text()) if walltime_path.exists() else None
    review_path = run_dir / 'review_video.json'
    review = json.loads(review_path.read_text()) if review_path.exists() else None
    inspection_path = run_dir / 'inspection_video.json'
    inspection = json.loads(inspection_path.read_text()) if inspection_path.exists() else None
    video_markers = {m['request_id']:m for m in (video or {}).get('markers', [])}
    audit = audit_episode(run_dir, controller_dir) if controller_dir else None
    model_messages = {}; model_payloads = {}; pending = []; public_events = []; model_index = 0
    summaries=lines(controller_dir/'model_reasoning_summaries.jsonl') if controller_dir else []
    summaries=[r for r in summaries if r.get('source')=='provider_returned_reasoning_summary' and r.get('verbatim') is True]
    summary_index=0
    model_source = event_path(controller_dir or run_dir)
    model_events = lines(model_source)
    if model_events:
        for event_index, event in enumerate(model_events):
            item = event.get('item',{})
            if item.get('type') in {'agent_message','mcp_tool_call'}:
                public_events.append(event)
            elif item.get('type') == 'reasoning' and isinstance(item.get('text'), str):
                public_events.append({'type': event.get('type'), 'item': {
                    'type': 'reasoning', 'id': item.get('id'), 'text': item['text']}})
            if event.get('type') != 'item.completed': continue
            if item.get('type') == 'agent_message':
                pending.append({'id':item.get('id'),'text':item.get('text',''),
                                'source_event_index':event_index,'source':'LLM public assistant message, verbatim'})
            elif item.get('type') == 'mcp_tool_call':
                model_index += 1
                model_messages[model_index] = pending
                model_payloads[model_index] = {
                    'model_call_event_id':item.get('id'),
                    'model_tool':item.get('tool'),'model_arguments':item.get('arguments'),
                    'model_transport_error':item.get('error'),
                    'model_result_text':next((c.get('text','') for c in (item.get('result') or {}).get('content',[])
                                              if c.get('type')=='text'),None)}
                pending = []

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
        result = {'revision': value.get('revision'), 'images': views}
        if 'capture' in value: result['capture'] = copy.deepcopy(value['capture'])
        return result

    # The constructor's episode_started image has NOT been returned to the agent.
    last_observation = None
    results = {e['request_id']: e for e in events if e['kind'] == 'tool_result'}
    decisions = []; plan = []; notes = {}; steps = []
    calls = [e for e in events if e['kind'] == 'tool_call']
    start = datetime.fromisoformat(calls[0]['at']) if calls else None
    for call in calls:
        step_summaries=[]
        while summary_index<len(summaries) and summaries[summary_index].get('at') and datetime.fromisoformat(summaries[summary_index]['at'])<=datetime.fromisoformat(call['at']):
            step_summaries.append(summaries[summary_index]);summary_index+=1
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
                      'observation_job': copy.deepcopy((result or {}).get('job')),
                      'request_id':call['request_id'], 'decision':copy.deepcopy(args.get('decision')),
                      'model_messages':copy.deepcopy(model_messages.get(len(steps)+1,[])),
                      'model_reasoning_summaries':step_summaries,
                      **model_payloads.get(len(steps)+1,{}),
                      'at': call['at'], 'elapsed_seconds': (datetime.fromisoformat(call['at']) - start).total_seconds(),
                      'video_start_seconds': video_markers.get(call['request_id'],{}).get('seconds'),
                      'tool_seconds': seconds, 'is_motor_action': name in {'act', 'look'},
                      'status': 'missing_result' if result is None else ('passed' if result.get('ok') else 'failed'),
                      'before': before, 'after': copy.deepcopy(last_observation), 'new_observation': new_obs is not None,
                      'decisions': copy.deepcopy(decisions[-4:]), 'plan': copy.deepcopy(plan), 'memory': copy.deepcopy(notes)})
    # Keep combinatorial goal arrays only in raw run.json, not replay exports.
    evaluation = run.get('evaluation')
    if isinstance(evaluation, dict):
        evaluation = {k:v for k,v in evaluation.items()
                      if k not in {'goal_options', 'initial_goal_options'}}
    # Only provider-returned summaries, never opaque/encrypted internal reasoning.
    return {'schema_version': 1, 'generated_at': now(), 'kind': 'discrete_observation_action_replay',
            'run_id': run['run_id'], 'status': run['status'], 'rgb_only': rgb,
            'instruction': run.get('config', {}).get('instruction'), 'config': run.get('config', {}),
            'source': run.get('source', {}), 'execution': {k: run.get(k) for k in
                ('host', 'pid', 'interpreter', 'unit', 'gpu_uuid', 'output_path', 'actions', 'tool_calls', 'sim_steps', 'wall_seconds')},
            'backend': run.get('backend', {}),
            'model': json.loads((controller_dir / 'controller.json').read_text()).get('model') if controller_dir else run.get('config', {}).get('model'),
            'audit': audit, 'steps': steps, 'video':video, 'explained_video':explained, 'walltime_video':walltime, 'review_video':review, 'inspection_video':inspection,
            'model_public_events':public_events, 'model_final_messages':pending, 'has_public_trace':bool(model_events),
            'model_transcript':build_transcript(model_events, steps, summaries),
            'model_reasoning_summaries':summaries,
            'model_final_reasoning_summaries':summaries[summary_index:],
            'reasoning_availability':'provider_returned_summary' if summaries else 'not_recorded_or_not_returned',
            'model_messages_after_last_sim_call':[m for i,ms in model_messages.items() if i>len(steps) for m in ms],
            'model_calls_without_sim_record':[c for i,c in model_payloads.items() if i>len(steps)],
            'model_text_contract':'Verbatim assistant messages and provider-returned reasoning summaries are separate. No translation, rewritten decision summary or opaque internal reasoning. Assistant text uses event order; provider summaries use recorded timestamps.',
            'evaluation_offline_only': {'task_success': run.get('task_success'), 'evaluation': evaluation,
                                        'agent_outcome': run.get('agent_outcome'), 'finish_reason': run.get('finish_reason')},
            'failure': run.get('failure') or run.get('error'),
            'limitations': ['连续录像保留实际控制步；讲解版额外停顿仅重复真实帧，省略模型等待，不插造运动。',
                'LLM 文本为公开 assistant 原文，按原始事件顺序对齐，不翻译或补写，不展示隐藏思维链。',
                '独立评分仅供实验结束后审阅，没有通过工具提供给模型。']}


def render_replay(run_dir: Path, controller_dir: Path | None = None) -> dict:
    data = build_replay(run_dir, controller_dir)
    if data['video'] and data['video'].get('status') == 'passed':
        from .streaming_video import prepare_browser_video
        try:
            browser = prepare_browser_video(run_dir, data['video']['file'])
            data['video']['playback_file'] = browser['file']
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            # Display optimization cannot invalidate the recorded experiment.
            write_json(run_dir / 'browser_video_warning.json', {'status':'failed', 'error':str(exc)})
    # Keep the downloadable original public stream separate: images may be large.
    public_events = data.pop('model_public_events')
    if data.get('has_public_trace'):
        (run_dir/'model_public_events.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in public_events))
    write_json(run_dir / 'replay.json', data)
    if data['audit']:
        write_json(run_dir / 'replay_audit.json', data['audit'])
    (run_dir / 'replay.html').write_text(render_replay_page(data))
    return data


def render_replay_page(data: dict) -> str:
    """Render archived replay data without rebuilding or changing experiment evidence."""
    assets = Path(__file__).with_name('replay_assets')
    template = (assets / 'index.html').read_text()
    # Combinatorial evaluator arrays can exceed hundreds of MB. They remain
    # in raw run.json, but scores and scalar metrics suffice for the viewer.
    offline = dict(data.get('evaluation_offline_only') or {})
    evaluation = offline.get('evaluation')
    if isinstance(evaluation, dict):
        offline['evaluation'] = {k:v for k,v in evaluation.items()
                                 if k not in {'goal_options','initial_goal_options'}}
    data = {**data, 'evaluation_offline_only': offline}
    # Escape < so task text cannot close a JSON script element.
    embedded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    page = template.replace('@@TITLE@@', html.escape(data['run_id']))
    page = page.replace('@@CSS@@', (assets / 'style.css').read_text() + (assets / 'transcript.css').read_text())
    page = page.replace('@@DATA@@', embedded).replace('@@JS@@', '\n'.join((assets / name).read_text() for name in ('timing.js', 'transcript.js', 'navigation_target.js', 'player.js')))
    if not data['audit']:
        page = page.replace(' href="replay_audit.json"', '')
    if not data['video']:
        page = page.replace(' href="video.json"', '')
    if not data['has_public_trace']:
        page = page.replace(' href="model_public_events.jsonl"', '')
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--controller-dir', type=Path)
    args = parser.parse_args()
    data = render_replay(args.run_dir, args.controller_dir)
    print(json.dumps({'run_id': data['run_id'], 'steps': len(data['steps']), 'replay': str(args.run_dir / 'replay.html')}))


if __name__ == '__main__':
    main()
