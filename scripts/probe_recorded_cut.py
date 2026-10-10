"""Replay a recorded RGB cut prefix through real tool handlers, then score.

Private scripted regression only: no model is invoked, no new pixels selected,
and partial scene scoring is never presented as full-task success.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, write_json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fixture_from_archive(archive):
    run = json.loads((archive / 'run.json').read_text())
    groundings = rows(archive / 'grounding_diagnostics.jsonl')
    calls = []
    for event in rows(archive / 'events.jsonl'):
        if event.get('kind') != 'tool_call':
            continue
        call = copy.deepcopy(event)
        target = call['arguments'].get('target')
        if target:
            matches = [g for g in groundings if g.get('image_ref') == target['image_ref']
                       and g.get('selected_pixel') == target['point']]
            if len(matches) != 1:
                raise ValueError(f"Expected unique archived grounding for {call['id']}")
            call['archived_grounding'] = matches[0]
        calls.append(call)
        if call['arguments'].get('primitive') == 'cut':
            break
    actions = [c for c in calls if c['name'] in ('act', 'look')]
    if not actions or actions[-1]['arguments'].get('primitive') != 'cut':
        raise ValueError('Archived calls do not reach cut')
    return {'task': run['config']['task'], 'instance': run['config']['instance'],
            'seed': run['config']['seed'], 'calls': calls, 'action_count': len(actions),
            'archive': str(archive.resolve()), 'archive_source': run['source'],
            'input_sha256': {name: sha(archive / name) for name in
                ('run.json', 'events.jsonl', 'grounding_diagnostics.jsonl')},
            'scope': 'Recorded normalized pixels and action order unchanged; only image_ref and revision '
                     'are remapped to the current four-camera snapshot. Identity divergence stops the replay.'}


def object_record(obj):
    return {'name': obj.name, 'category': obj.category, 'model': getattr(obj, 'model', None),
            'pose': [v.detach().cpu().tolist() for v in obj.get_position_orientation()]}


def find_q(evaluation):
    metrics = evaluation.get('official_metrics', {})
    q_record = metrics.get('q_score', {})
    final = q_record.get('final') if isinstance(q_record, dict) else q_record
    for value in (final, metrics.get('q'), evaluation.get('q_score')):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
    # Keep the metric lookup explicit: do not mistake another number for Q.
    for key in ('task', 'task_metric', 'task_metrics'):
        record = metrics.get(key, {})
        if isinstance(record, dict):
            value = record.get('q_score', record.get('q'))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                return value
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execution-seconds', type=float, default=1800)
    parser.add_argument('--fixture-only', action='store_true')
    args = parser.parse_args()
    fixture = fixture_from_archive(args.archive_run)
    if args.fixture_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, fixture)
        return 0
    config = {'task': fixture['task'], 'instance': fixture['instance'], 'seed': fixture['seed'],
              'backend': 'omnigibson', 'policy': 'private_fixed_recorded_RGB_cut_replay',
              'instruction': '固定历史 RGB 工具序列诊断：没有模型参与，仅验证一次切割及正式评分；不是完整任务成功测试。',
              'model_used': False, 'not_a_benchmark_attempt': True, 'record_video': True,
              'observation_mode': 'rgb_only', 'script_sha256': sha(Path(__file__)),
              'validation_level': 'real_simulator_recorded_cut_and_finish_regression',
              'source_archive': fixture['archive'], 'expected_action_count': fixture['action_count']}
    recorder = Recorder(args.output, config)
    write_json(args.output / 'fixture.json', fixture)
    validation = {'status': 'running', 'validation_level': config['validation_level'],
                  'model_used': False, 'full_task_success_required': False,
                  'checks': {}, 'actions': []}
    write_json(args.output / 'validation.json', validation)
    backend = harness = None
    try:
        from manipulation_agent.contracts import Budget
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.vision_harness import VisionHarness
        backend = RGBBackend(fixture['task'], fixture['instance'], args.output, seed=fixture['seed'],
                             max_steps=20000, inside_placement='official_volume', record_video=True)
        backend.deadline.arm_local(args.execution_seconds)
        harness = VisionHarness(backend, recorder, Budget(wall_seconds=args.execution_seconds), profile='skills')
        recorder.run['execution_clock'] = backend.deadline.clock
        write_json(args.output / 'run.json', recorder.run)
        for original in fixture['calls']:
            arguments = copy.deepcopy(original['arguments'])
            name = original['name']
            primitive = arguments.get('primitive', name)
            target = arguments.get('target')
            target_obj = None
            if 'revision' in arguments:
                arguments['revision'] = harness.revision
            if target:
                view = target['image_ref'].rsplit('-', 1)[-1]
                target['image_ref'] = next(image['image_ref'] for image in harness.snapshot['images']
                                           if image['view'] == view)
                target_obj, point, grounding = backend._ground(target, require_object=primitive != 'navigate_to')
                expected = original['archived_grounding'].get('selected_object_prim')
                actual = target_obj.name if target_obj else None
                expected_name = expected.rsplit('/', 1)[-1] if expected else None
                record = {'original_event': original['id'], 'primitive': primitive,
                          'expected_object': expected_name, 'actual_object': actual,
                          'actual_point': point.cpu().tolist(), 'actual_grounding': grounding,
                          'original_arguments': original['arguments'], 'replayed_arguments': arguments,
                          'identity_matches': expected_name is None or actual == expected_name}
                recorder.event('diagnostic_grounding', record)
                if not record['identity_matches']:
                    validation['grounding_divergence'] = record
                    raise ValueError(f'Recorded pixel grounded to {actual}, expected {expected_name}; no substitute pixel attempted')
            if primitive == 'cut':
                validation['cut_before'] = {'target': object_record(target_obj),
                    'held_tool': object_record(backend._get_held()) if backend._get_held() else None,
                    'target_part_metadata': target_obj.metadata.get('object_parts'),
                    'half_logs': [object_record(obj) for obj in backend.env.scene.objects if obj.category == 'half_log']}
                before_names = {obj.name for obj in backend.env.scene.objects}
                cut_target_name = target_obj.name
                recorder.event('diagnostic_cut_before', validation['cut_before'])
                write_json(args.output / 'validation.json', validation)
            recorder.event('diagnostic_archived_call', {'original_event': original['id'],
                           'original_arguments': original['arguments'], 'replayed_arguments': arguments})
            result = harness.call(name, arguments, 'recorded-cut-' + original['id'])
            if name in ('act', 'look'):
                item = {'original_event': original['id'], 'primitive': primitive,
                        'arguments': arguments, 'ok': result.get('ok'), 'error': result.get('error'),
                        'returned_views': [i['view'] for i in result.get('observation', {}).get('images', [])]}
                validation['actions'].append(item)
                print(json.dumps(item), flush=True)
                write_json(args.output / 'validation.json', validation)
            if not result.get('ok'):
                raise ValueError(f'Recorded tool returned error at {original["id"]}: {result.get("error")}')
            if primitive == 'cut':
                current = {obj.name: obj for obj in backend.env.scene.objects}
                added = [object_record(obj) for name, obj in current.items() if name not in before_names]
                validation['cut_after'] = {'original_removed': cut_target_name not in current,
                    'added_objects': added,
                    'half_logs': [object_record(obj) for obj in current.values() if obj.category == 'half_log']}
                validation['checks'].update(cut_returned_ok=True,
                    original_target_removed=validation['cut_after']['original_removed'],
                    exactly_two_new_half_logs=sum(obj['category'] == 'half_log' for obj in added) == 2)
                recorder.event('diagnostic_cut_after', validation['cut_after'])
        validation['checks'].update(all_recorded_actions_ok=len(validation['actions']) == fixture['action_count']
                                    and all(row['ok'] for row in validation['actions']),
            every_action_returned_four_rgb=all(set(row['returned_views']) == {'front','back','left','right'}
                                              for row in validation['actions']))
    except Exception as exc:
        validation.update(error_type=type(exc).__name__, error=str(exc))
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.event('diagnostic_failure', {'error_type': type(exc).__name__, 'error': str(exc)})
    finally:
        if harness is not None:
            try:
                result = harness.call('finish', {'outcome': 'aborted',
                    'reason': 'Private fixed recorded RGB sequence diagnostic ended after one cut. '
                              'No autonomous model or full-task success claim.'}, 'recorded-cut-finish')
                evaluation = recorder.run.get('evaluation', {})
                validation['q_score'] = find_q(evaluation)
                validation['official_partial_task_success'] = recorder.run.get('task_success')
                validation['checks'].update(finish_closed=result.get('closed') is True,
                    official_scoring_completed=recorder.run.get('scoring', {}).get('status') == 'passed',
                    final_q_numeric=validation['q_score'] is not None)
                harness.finalize_recording()
                validation['checks']['video_finalized'] = recorder.run.get('video', {}).get('status') == 'passed'
            except Exception as exc:
                validation['finish_error'] = {'type': type(exc).__name__, 'error': str(exc),
                                              'traceback': traceback.format_exc()}
        validation['status'] = 'passed' if not validation.get('error') and not validation.get('finish_error') \
            and validation['checks'] and all(validation['checks'].values()) else 'failed'
        write_json(args.output / 'validation.json', validation)
        recorder.run['component_validation'] = validation
        if recorder.run['status'] == 'running':
            recorder.finish({'status': 'failed', 'task_success': None, 'failure': validation.get('error')}, render=False)
        else:
            write_json(args.output / 'run.json', recorder.run)
        if backend is not None:
            backend.close()
        else:
            from manipulation_agent.startup_cleanup import shutdown_partial_simulator
            recorder.event('startup_cleanup', shutdown_partial_simulator())
    print(json.dumps(validation), flush=True)
    return 0 if validation['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
