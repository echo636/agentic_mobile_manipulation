"""Join controller evidence with simulator evidence without rewriting either record."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import tool_specs
from .records import now, write_json


def audit_episode(run_dir: Path, controller_dir: Path) -> dict:
    run = json.loads((run_dir / 'run.json').read_text())
    controller = json.loads((controller_dir / 'controller.json').read_text())
    sim_events = [json.loads(s) for s in (run_dir / 'events.jsonl').read_text().splitlines()]
    model_events = [json.loads(s) for s in (controller_dir / 'model_events.jsonl').read_text().splitlines()]
    sim_calls = [{'name': e['name'], 'arguments': e['arguments']} for e in sim_events if e['kind'] == 'tool_call']
    calls = []; unexpected = []; usage = {}; closed = False
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
    evidence_ok = all(v for k, v in checks.items() if k not in {'independent_task_success', 'official_task_success'})
    return {'schema_version': 1, 'audited_at': now(), 'run_id': run['run_id'], 'model': controller['model'],
            'status': 'passed' if all(checks.values()) else 'failed',
            'evidence_alignment': 'passed' if evidence_ok else 'failed',
            'task_success': run.get('task_success'), 'checks': checks, 'unexpected_items': unexpected,
            'model_tool_calls': len(calls), 'usage': usage,
            'protocol': run.get('evaluation', {}).get('protocol'),
            'official_submission_eligible': False,
            'simulator_record': str((run_dir / 'run.json').resolve()),
            'controller_record': str((controller_dir / 'controller.json').resolve())}


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
