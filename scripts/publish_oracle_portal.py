"""Publish Oracle evidence using the repository's existing portal and replay shell.

Only rendered review data, verified RGB and finalized media enter the static root.
Native sessions, authentication, private logs and raw evaluation payloads stay private.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.replay import build_replay, render_replay_page
from manipulation_agent.tool_trace import enrich_tool_trace
from manipulation_agent.streaming_video import prepare_browser_video, file_hash
from manipulation_agent.records import now
from publish_oracle_replays import link_media


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def safe_source(env, relative):
    path = env / relative
    if not path.resolve().is_relative_to(env.resolve()) or not path.is_file():
        raise ValueError('Invalid archived viewing artifact: ' + str(relative))
    return path


def publish(run, root, private):
    env = run / 'env'
    selection = read(run / 'controller_selection.json', {})
    controller_name = selection.get('controller', 'controller')
    if not isinstance(controller_name, str) or Path(controller_name).name != controller_name or not controller_name.startswith('controller'):
        raise ValueError('Invalid selected controller directory')
    controller = run / controller_name
    has_controller = (controller / 'controller.json').is_file()
    config = read(controller / 'controller.json', {})
    # The RGB/legacy audit uses another tool catalog. Preserve independent Oracle
    # scores and actual trace verification instead of labeling that audit a pass.
    if (env / 'run.json').is_file():
        data = build_replay(env, controller if has_controller else None, include_audit=False)
    else:
        scope = read(run / 'trial_scope.json', {})
        data = {'run_id': run.name, 'status': 'blocked', 'config': scope,
                'instruction': scope.get('instruction'), 'steps': [], 'model_transcript': [],
                'video': None, 'audit': None, 'has_public_trace': False,
                'evaluation_offline_only': {'task_success': None},
                'model_public_events': [], 'execution': {}, 'model': None,
                'limitations': ['Application bootstrap failed before simulator episode creation; no model calls or score.']}
    data['run_id'] = run.name
    engineering = read(run / 'engineering_validation.json')
    scope = read(run / 'trial_scope.json', {})
    workflow = read(run / 'rax_workflow.json')
    preparation = read(env / 'scene_geometry_repair.json', {})
    preparation = {key: preparation[key] for key in ('policy', 'scope', 'task_object_bindings_unchanged', 'original_assets_unmodified', 'added_floors') if key in preparation}
    validation = read(run / 'attempt_validation.json')
    if validation and validation.get('status') == 'initialization_failed':
        data['status'] = 'blocked'
    data['oracle_review'] = {'scope': scope, 'engineering_validation': engineering,
        'reasoning_effort': config.get('model_reasoning_effort'), 'attempt_validation': validation,
        'controller_source': controller_name, 'previous_controllers': selection.get('previous_controllers', []),
        'scene_preparation': preparation,
        'audit_scope': 'Official whole-task score and engineering validation are separate. Actual MCP attachment bytes verified below; legacy RGB audit not applied.'}
    if workflow is not None:
        # Operator-authored compact provenance, never raw session/config data.
        allowed = ('phase', 'control_mode', 'author_model', 'reasoning_effort',
                   'bundle', 'code_sha256', 'execution_clean', 'controller_ok',
                   'motor_calls', 'failed_motor_calls', 'near_zero_joint_calls')
        data['oracle_review']['rax_workflow'] = {k: workflow[k] for k in allowed if k in workflow}
        if workflow.get('control_mode') == 'frozen_program':
            data['model'] = None
    target = root / 'manipulation_runs' / run.name
    target.mkdir(parents=True, exist_ok=True)
    scratch = private / run.name
    scratch.mkdir(parents=True, exist_ok=True)
    events = data.pop('model_public_events')
    if events:
        (scratch / 'model_public_events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events))
    if (env / 'captures.jsonl').is_file():
        shutil.copyfile(env / 'captures.jsonl', scratch / 'captures.jsonl')
    image_files = {im['file'] for step in data['steps'] for side in ('before', 'after')
                   for im in (step.get(side) or {}).get('images', []) if im.get('file')}
    for relative in image_files:
        source = safe_source(env, relative)
        link_media(source, scratch / relative)
        link_media(source, target / relative)
    video = data.get('video')
    if video and video.get('status') == 'passed':
        browser = prepare_browser_video(env, video['file'])
        video['playback_file'] = browser['file']
    # These records are existing optional viewing derivatives, never new motion.
    media = []
    for key in ('video', 'spectator_video', 'spectator_walltime_video', 'inspection_video', 'walltime_video'):
        record = data.get(key)
        if not record or record.get('status') != 'passed':
            continue
        for field in ('file', 'playback_file', 'poster'):
            relative = record.get(field)
            if relative:
                source = safe_source(env, relative)
                link_media(source, target / relative)
                media.append({'file': relative, 'sha256': file_hash(source), 'bytes': source.stat().st_size})
    data = enrich_tool_trace(data, scratch, target)
    write(target / 'replay.json', data)
    page = render_replay_page(data, source_dir=scratch, asset_dir=target)
    page = page.replace('href="/compare100_20261002/"', 'href="../../replays.html"')
    (target / 'replay.html').write_text(page)
    task_success = data['evaluation_offline_only'].get('task_success')
    engineering_success = (engineering or {}).get('engineering_success', (engineering or {}).get('success'))
    task = data['config'].get('task', 'unknown')
    label = (scope.get('label') if scope.get('kind') == 'engineering_subtask' else None) or task.replace('_', ' ')
    if engineering is not None:
        label += (' · 工程子目标成功' if engineering_success is True else
                  ' · 工程子目标未完成' if engineering_success is False else
                  ' · 开门指标已达，连续保持未认证' if engineering.get('sampled_goal_checks_passed') else
                  ' · 工程证据待验证')
    if validation and validation.get('model_calls') == 0:
        label += ' · 初始化故障，模型未调用'
    actual_model = data.get('model')
    note = ('模型 ' + str(actual_model) + ' / ' + str(config.get('model_reasoning_effort'))
            if actual_model else '模型未调用')
    if workflow is not None:
        if workflow.get('control_mode') == 'frozen_program':
            note = ('RAX 冻结程序独立执行，回合内无模型调用。程序作者 ' +
                    str(workflow.get('author_model')) + ' / ' + str(workflow.get('reasoning_effort')) + '。')
        else:
            note = ('RAX 探索与代码调试，保留失败动作。模型 ' +
                    str(workflow.get('author_model')) + ' / ' + str(workflow.get('reasoning_effort')) + '。')
    note += '。Oracle 3D；完整官方任务评分与工程子目标分别记录。'
    if engineering is not None:
        note += '工程子目标成功=' + str(engineering_success) + '；完整任务成功=' + str(task_success) + '。'
        note += engineering.get('summary_zh', '')
    row = {'task': task, 'name': label, 'run_id': run.name, 'status': data['status'],
           'task_success': task_success, 'stage': 'complete', 'actions': data['execution'].get('actions'),
           'instruction': note + '\n' + (data.get('instruction') or ''),
           'replay_url': f'manipulation_runs/{run.name}/replay.html',
           'official_task_success': task_success, 'engineering_success': engineering_success,
           'model': actual_model, 'reasoning_effort': config.get('model_reasoning_effort')}
    raw_stream = controller / 'model_events.jsonl'
    row['cohort'] = ('rax' if workflow is not None else 'historical' if not run.name.startswith('oracle_transfer_') else
                     'trials' if raw_stream.is_file() and raw_stream.stat().st_size > 0 else 'initialization')
    return row, {'run': str(run), 'replay': row['replay_url'], 'trace': data.get('tool_trace_stats'),
                 'controller_source': controller_name,
                 'raw_model_sha256': file_hash(controller / 'model_events.jsonl') if (controller / 'model_events.jsonl').is_file() else None,
                 'model_stream_invalid_lines': data.get('model_stream_invalid_lines'),
                 'model_stream_ignored_lines': data.get('model_stream_ignored_lines'), 'media': media}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, action='append', required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--private-manifest', type=Path, required=True)
    p.add_argument('--append', action='store_true', help='Preserve existing portal rows and bookmarked replays')
    args = p.parse_args()
    root = args.root.resolve(); private = args.private_manifest.resolve().parent / 'publication_sources'
    if private.is_relative_to(root) or args.private_manifest.resolve().is_relative_to(root):
        p.error('Private provenance must remain outside public static root')
    root.mkdir(parents=True, exist_ok=True); private.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    for name in ('replays.html', 'replay_portal.js'):
        shutil.copyfile(repo / 'web' / name, root / name)
    previous = read(root / 'portal_progress.json', {}).get('tasks', []) if args.append else []
    rows, manifests = [], []
    for run in args.run:
        row, manifest = publish(run.resolve(), root, private)
        row['index'] = len(rows); rows.append(row); manifests.append(manifest)
    if previous:
        merged = {row['run_id']: row for row in previous}
        merged.update({row['run_id']: row for row in rows})
        rows = [dict(row, index=i) for i, row in enumerate(merged.values())]
    write(root / 'portal_progress.json', {'updated_at': now(), 'tasks': rows})
    sources = []
    note = '此列表“目标成功”按完整官方任务评分。工程子目标另行标注；Oracle 3D、原生 assisted、官方 IK，均非纯视觉官方提交。'
    for cohort, label, identifier in [('rax', 'RAX 代码迭代与独立复跑', 'rax'),
                                      ('trials', '本轮新操作 · Astra/xhigh', 'oracle'),
                                      ('historical', '历史成功参考', 'historical'),
                                      ('initialization', '启动故障 · 模型未调用', 'initialization')]:
        selected = [dict(row, index=i) for i, row in enumerate(r for r in rows if r['cohort'] == cohort)]
        if not selected:
            continue
        filename = cohort + '_progress.json'
        write(root / filename, {'updated_at': now(), 'tasks': selected})
        sources.append({'id': identifier, 'label': label, 'data_url': filename, 'replay_base': './',
                        'run_prefixes': [r['run_id'] for r in selected], 'note': note +
                        (' 初始化故障没有任务评分，不计 GPT 操作失败。' if cohort == 'initialization' else '')})
    write(root / 'results_sources.json', {'default': sources[0]['id'], 'sources': sources})
    write(args.private_manifest, {'generated_at': now(), 'framework': 'web/replays.html + manipulation_agent.replay_shell', 'runs': manifests})
    print(json.dumps({'root': str(root), 'cases': len(rows), 'scores': [r['task_success'] for r in rows]}))


if __name__ == '__main__':
    main()
