"""Carry bounded, previously public action feedback into a fresh policy thread."""
import json


def public_action_history(raw_events, *, max_records=240):
    """Use completed Codex MCP records only; never infer simulator state."""
    if max_records <= 0:
        raise ValueError('max_records must be positive')
    records = []
    for event in raw_events:
        item = event.get('item') or {}
        if (event.get('type') != 'item.completed' or item.get('type') != 'mcp_tool_call'
                or item.get('server') != 'manipulation' or item.get('tool') not in ('act', 'look')):
            continue
        args = item.get('arguments') or {}
        if not isinstance(args, dict):
            continue
        row = {'primitive': args.get('primitive', item['tool']), 'ok': None}
        result = item.get('result') or {}
        for block in result.get('content', []) if isinstance(result, dict) else []:
            if block.get('type') != 'text':
                continue
            try:
                feedback = json.loads(block.get('text', ''))
            except (ValueError, TypeError):
                continue
            if not isinstance(feedback, dict) or 'ok' not in feedback:
                continue
            row['ok'] = feedback['ok']
            effect = feedback.get('effect') or {}
            row['effect'] = {key: effect[key] for key in
                             ('primitive', 'status', 'grasped_category') if key in effect}
            error = feedback.get('error') or {}
            if error:
                row['error'] = {key: error[key] for key in ('code', 'world_may_have_changed')
                                if key in error}
            break
        records.append(row)
    return {'records': records[-max_records:], 'omitted_earlier_records': max(0, len(records)-max_records),
            'scope': 'Previously public completed action feedback, in order. Historical outcomes are not current state or whole-task success; inspect current RGB and choose every next action yourself.'}
