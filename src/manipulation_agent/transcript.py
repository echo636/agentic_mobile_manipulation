"""Ordered public conversation, independent of the currently selected replay step."""
from __future__ import annotations

import copy
import json
from collections import Counter


def build_transcript(events: list[dict], steps: list[dict], summaries: list[dict]) -> list[dict]:
    """Keep event order, including interleaved messages and calls without sim records.

    Provider session summaries have a separate clock. Insert those at their next
    timestamp-aligned tool boundary, explicitly recording this weaker alignment.
    Only readable returned text is exported; opaque reasoning is never copied.
    """
    mapped = {s.get('model_call_event_id'): s['index'] for s in steps if s.get('model_call_event_id')}
    result_steps = {s['result_event_id']: s['index'] for s in steps if s.get('result_event_id')}
    calls = {}
    completed_ordinal = 0
    for i, event in enumerate(events):
        item = event.get('item', {})
        if item.get('type') != 'mcp_tool_call':
            continue
        key = item.get('id') or f'event_{i}'
        record = calls.setdefault(key, {'first': i, 'item': item, 'step': mapped.get(key)})
        if event.get('type') == 'item.completed':
            completed_ordinal += 1
            record['item'] = item
            if record['step'] is not None:
                candidate = next(s for s in steps if s['index'] == record['step'])
                if item.get('tool') != candidate['tool'] or item.get('arguments') != candidate['arguments']:
                    record['step'] = None
            # Legacy fixtures may not have call IDs; never assign by ordinal alone.
            if record['step'] is None and completed_ordinal <= len(steps):
                step = steps[completed_ordinal - 1]
                if item.get('tool') == step['tool'] and item.get('arguments') == step['arguments']:
                    record['step'] = step['index']
            # Returned evidence IDs override ordinal legacy alignment after retries.
            for block in (item.get('result') or {}).get('content', []):
                if block.get('type') != 'text':
                    continue
                try:
                    payload = json.loads(block.get('text', ''))
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get('evidence_id') in result_steps:
                    record['step'] = result_steps[payload['evidence_id']]
                    break
            if item.get('error') and not item.get('result'):
                record['step'] = None

    # A message belongs visually to the next call, but remains in its raw position.
    next_step = {}
    following = None
    for i in range(len(events) - 1, -1, -1):
        item = events[i].get('item', {})
        if item.get('type') == 'mcp_tool_call':
            following = calls[item.get('id') or f'event_{i}']['step']
        next_step[i] = following

    emitted = set()
    entries = []
    public_summaries = Counter()
    for i, event in enumerate(events):
        item = event.get('item', {})
        kind = item.get('type')
        common = {'source_event_index': i, 'source_id': item.get('id'),
                  'source': 'model_events.jsonl', 'alignment': 'source_event_order',
                  'step': next_step[i]}
        if kind in {'agent_message', 'reasoning'} and event.get('type') == 'item.completed':
            text = item.get('text')
            if not isinstance(text, str):
                continue
            entries.append({**common, 'kind': 'assistant' if kind == 'agent_message' else 'provider_summary',
                            'text': text})
            if kind == 'reasoning':
                public_summaries[text] += 1
        elif kind == 'mcp_tool_call':
            key = item.get('id') or f'event_{i}'
            call = calls[key]
            common.update(step=call['step'], tool=item.get('tool'), call_id=key)
            if key not in emitted:
                emitted.add(key)
                entries.append({**common, 'kind': 'tool_call',
                                'arguments': copy.deepcopy(call['item'].get('arguments')),
                                'arguments_source': 'completed_item' if item != call['item'] else 'this_item'})
            if event.get('type') == 'item.completed':
                result = item.get('result') or {}
                # Do not inline large image/base64 payloads; original JSONL retains them.
                content = result.get('content', [])
                entries.append({**common, 'kind': 'tool_result',
                                'text_blocks': [c.get('text', '') for c in content if c.get('type') == 'text'],
                                'image_count': sum(c.get('type') == 'image' for c in content),
                                'structured_content': copy.deepcopy(result.get('structuredContent')),
                                'error': copy.deepcopy(item.get('error')),
                                'is_error': result.get('isError'), 'status': item.get('status')})

    for summary in summaries:
        if public_summaries[summary['text']]:
            public_summaries[summary['text']] -= 1
            continue  # Same returned summary recorded through both interfaces.
        step = next((s['index'] for s in steps if summary in s.get('model_reasoning_summaries', [])), None)
        entry = {'kind': 'provider_summary', 'text': summary['text'], 'step': step,
                 'source': 'model_reasoning_summaries.jsonl', 'at': summary.get('at'),
                 'source_line': summary.get('source_line'), 'alignment': 'next_tool_by_recorded_timestamp'}
        position = next((i for i, e in enumerate(entries) if step is not None and e['step'] == step), len(entries))
        # Preserve session summary order at a common boundary.
        while position < len(entries) and entries[position].get('alignment') == entry['alignment'] and entries[position]['step'] == step:
            position += 1
        entries.insert(position, entry)
    for i, entry in enumerate(entries, 1):
        entry['sequence'] = i
    return entries
