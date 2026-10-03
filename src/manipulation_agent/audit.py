"""Join controller evidence with simulator evidence without rewriting either record."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from .contracts import tool_specs
from .records import now, write_json


def official_executor_protocol(backend):
    protocol=backend.get('control_protocol')
    if protocol=='rgb_official_symbolic_direct_v1':
        return True
    if protocol!='rgb_official_symbolic_initialized_navigation_v2':
        return False
    planner=backend.get('navigation_planner',{})
    return (planner.get('initialized') is True
        and planner.get('implementation')=='upstream_CuRoboMotionGenerator'
        and set(planner.get('embodiments',[]))=={'DEFAULT','ARM','BASE'})


def audit_episode(run_dir: Path, controller_dir: Path) -> dict:
    run = json.loads((run_dir / 'run.json').read_text())
    controller = json.loads((controller_dir / 'controller.json').read_text())
    sim_events = [json.loads(s) for s in (run_dir / 'events.jsonl').read_text().splitlines()]
    model_events = [json.loads(s) for s in (controller_dir / 'model_events.jsonl').read_text().splitlines()]
    sim_calls = [{'name': e['name'], 'arguments': e['arguments']} for e in sim_events if e['kind'] == 'tool_call']
    calls = []; unexpected = []; usage = {}; closed = False
    rgb = run.get('config', {}).get('observation_mode') == 'rgb_only'
    if rgb:
        from .tools import tool_specs as rgb_tool_specs
        catalog = {t['name'] for t in rgb_tool_specs(run.get('config', {}).get('agent_profile', 'workflow'))}
    else:
        catalog = {t['name'] for t in tool_specs()}
    for event in model_events:
        if event.get('type') == 'turn.completed':
            for key, value in event.get('usage', {}).items():
                if isinstance(value, (int, float)): usage[key] = usage.get(key, 0) + value
        if event.get('type') != 'item.completed': continue
        item = event.get('item', {})
        kind = item.get('type')
        if kind not in {'agent_message', 'reasoning', 'mcp_tool_call'}:
            unexpected.append({'type': kind, 'id': item.get('id')})
        if kind != 'mcp_tool_call': continue
        if item.get('server') != 'manipulation' or item.get('tool') not in catalog:
            unexpected.append({'server': item.get('server'), 'tool': item.get('tool')})
        calls.append({'name': item.get('tool'), 'arguments': item.get('arguments')})
        if item.get('server') == 'manipulation' and item.get('tool') == 'finish':
            for content in (item.get('result') or {}).get('content', []):
                if content.get('type') == 'text':
                    try:
                        result = json.loads(content['text'])
                        closed = closed or (result.get('ok') is True and result.get('closed') is True)
                    except (ValueError, AttributeError): pass
    same_commit = bool(run.get('source', {}).get('commit')) and run['source']['commit'] == controller.get('source', {}).get('commit')
    checks = {
        'real_simulator': run['config']['backend'] == 'omnigibson',
        'real_controller_finished': controller.get('status') == 'passed' and controller.get('exit_code') == 0,
        'formal_finish_in_model_trace': closed,
        'only_allowed_tools': not unexpected,
        'controller_calls_match_simulator_exactly': calls == sim_calls and bool(calls),
        'same_source_commit': same_commit,
        'independent_task_success': run.get('task_success') is True,
        'official_task_success': run.get('evaluation', {}).get('official_task_success') is True,
    }
    rgb_evidence = {}
    if rgb:
        rgb_evidence = audit_rgb_transport(run_dir, sim_events, model_events)
        checks.update(rgb_evidence.pop('checks'))
        checks['same_clean_source_digest'] = (
            run.get('source', {}).get('dirty') is False and controller.get('source', {}).get('dirty') is False
            and bool(run['source'].get('source_sha256'))
            and run['source']['source_sha256'] == controller.get('source', {}).get('source_sha256'))
        profile = run.get('config', {}).get('agent_profile', 'workflow')
        if profile in {'workflow','skills'}:
            manifest = run_dir / 'skill_manifest.json'
            checks['frozen_skill_manifest'] = manifest.exists() and bool(run.get('skill_bundle_sha256')) and (
                json.loads(manifest.read_text()).get('bundle_sha256') == run.get('skill_bundle_sha256'))
            if profile == 'skills':
                checks['skills_profile_on_both_sides'] = controller.get('agent_profile') == 'skills'
                checks['no_plan_memory_tools_called'] = all(c['name'] in catalog for c in calls)
                checks['recorded_summary_fields_valid_if_present'] = all(
                    set(c['arguments']['decision']) == {'observation','reason','expected'}
                    for c in calls if 'decision' in c['arguments'])
        elif profile == 'official':
            checks['official_profile_on_both_sides'] = controller.get('agent_profile') == 'official'
            checks['official_executor_protocol'] = official_executor_protocol(run.get('backend',{}))
            checks['no_custom_executor'] = all(run.get('backend',{}).get(k) is False for k in ('custom_navigation','custom_carry','custom_placement'))
        else:
            checks['minimal_profile_on_both_sides'] = controller.get('agent_profile') == 'minimal'
            checks['no_workflow_tools_called'] = all(c['name'] in {'observe','look','act','finish'} for c in calls)
    evidence_ok = all(v for k, v in checks.items() if k not in {'independent_task_success', 'official_task_success'})
    return {'schema_version': 1, 'audited_at': now(), 'run_id': run['run_id'], 'model': controller['model'],
            'status': 'passed' if all(checks.values()) else 'failed',
            'evidence_alignment': 'passed' if evidence_ok else 'failed',
            'task_success': run.get('task_success'), 'checks': checks, 'unexpected_items': unexpected,
            'model_tool_calls': len(calls), 'usage': usage,
            'rgb_evidence': rgb_evidence,
            'protocol': run.get('evaluation', {}).get('protocol'),
            'official_submission_eligible': False,
            'simulator_record': str((run_dir / 'run.json').resolve()),
            'controller_record': str((controller_dir / 'controller.json').resolve())}


def audit_rgb_transport(run_dir: Path, sim_events: list[dict], model_events: list[dict]) -> dict:
    """Check actual model-facing bytes, result equality and the RGB allowlist.

    This is a protocol audit, not a claim to prove absence of all possible covert
    channels. Private simulator events are never treated as model observations.
    """
    from .observations.boundary import public_observation
    items = [e['item'] for e in model_events if e.get('type') == 'item.completed'
             and e.get('item', {}).get('type') == 'mcp_tool_call']
    results = [e for e in sim_events if e.get('kind') == 'tool_result']
    capture_path = run_dir / 'captures.jsonl'
    captures = [json.loads(s) for s in capture_path.read_text().splitlines()] if capture_path.exists() else []
    files = {i['image_ref']: i for c in captures for i in c['images']}
    checks = {'model_results_match_simulator': len(items) == len(results),
              'rgb_observation_allowlist': True, 'image_bytes_hashes_and_archive_match': True,
              'pixel_actions_use_latest_images': True, 'actual_images_delivered': False}
    count = 0; refs = set(); latest = set(); errors = []
    for item, event in zip(items, results):
        try:
            if item['tool'] == 'act' and item['arguments'].get('target') is not None:
                target = item['arguments']['target']
                valid = isinstance(target, dict) and set(target) == {'image_ref', 'point'}
                valid = valid and target['image_ref'] in latest and isinstance(target['point'], list) and len(target['point']) == 2
                valid = valid and all(type(v) in (int, float) and 0 <= v <= 1 for v in target['point'])
                checks['pixel_actions_use_latest_images'] &= bool(valid)
            content = (item.get('result') or {}).get('content', [])
            if not content:
                # The simulator can finish after the MCP client has timed out.
                # Its private result is not evidence of delivery to the model.
                checks['model_results_match_simulator'] = False
                error = item.get('error')
                message = str(error.get('message', '') if isinstance(error, dict) else error or '').lower()
                errors.append({'event_id': event.get('id'), 'model_item_id': item.get('id'),
                               'tool': item.get('tool'), 'error_type': 'ToolResponseUnavailable',
                               'code': 'tool_response_timeout' if 'timed out' in message or 'timeout' in message else 'missing_tool_response',
                               'response_received': False})
                continue
            result = json.loads(content[0]['text'])
            expected = {**event['result'], 'evidence_id': event['id']}
            checks['model_results_match_simulator'] &= result == expected
            observation = result.get('observation')
            images = [c for c in content if c.get('type') == 'image']
            if observation is None:
                checks['image_bytes_hashes_and_archive_match'] &= not images
                continue
            try:
                raw = {k: v for k, v in observation.items() if k != 'revision'}
                public_observation(raw, observation['revision'])
            except (RuntimeError, KeyError, TypeError):
                checks['rgb_observation_allowlist'] = False
            metadata = observation.get('images', [])
            checks['image_bytes_hashes_and_archive_match'] &= len(images) == len(metadata)
            for meta, image in zip(metadata, images):
                payload = base64.b64decode(image['data'], validate=True)
                sha = hashlib.sha256(payload).hexdigest()
                saved = files[meta['image_ref']]
                path = (run_dir / saved['file']).resolve()
                if not path.is_relative_to(run_dir.resolve()):
                    raise ValueError('Image path outside run directory')
                checks['image_bytes_hashes_and_archive_match'] &= (
                    sha == meta['sha256'] == saved['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
                    and image['mimeType'] == meta['mime_type'])
                count += 1; refs.add(meta['image_ref'])
            latest = {m['image_ref'] for m in metadata}
        except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
            checks['image_bytes_hashes_and_archive_match'] = False
            errors.append({'event_id': event.get('id'), 'error_type': type(exc).__name__})
    checks['actual_images_delivered'] = count > 0
    return {'checks': checks, 'image_content_count': count, 'unique_model_image_count': len(refs),
            'archive_capture_count': len(captures), 'errors': errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--controller-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit_episode(args.run_dir, args.controller_dir)
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
