"""Associate recorded internal Pose parameters with their actual Manip call."""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def enrich_pose_parameters(data: dict, source_dir: Path | None) -> dict:
    """Keep public arguments verbatim; attach derived execution records separately.

    Repeated candidate phases and failures retain their event order. No new tool
    calls are made up, and a missing result remains missing, not successful.
    """
    path = Path(source_dir) / 'model_pose.jsonl' if source_dir is not None else None
    if path is None or not path.is_file():
        return data
    result = dict(data)
    steps = [dict(step) for step in data.get('steps', [])]
    calls = {step['request_id']: step for step in steps if step.get('tool') == 'manip'}
    phases, pending, diagnostics = defaultdict(list), defaultdict(deque), []
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for number, raw in enumerate(stream, 1):
            digest.update(raw)
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                diagnostics.append(dict(line=number, code='malformed_pose_event'))
                continue
            if not isinstance(event, dict) or event.get('kind') not in {'pose_phase_goal', 'pose_phase_result'}:
                continue
            rid, name = event.get('request_id'), event.get('phase_name')
            if not isinstance(rid, str) or rid not in calls or not isinstance(name, str):
                diagnostics.append(dict(line=number, code='unmatched_pose_event'))
                continue
            key = (rid, name)
            if event['kind'] == 'pose_phase_goal':
                pose = event.get('pose_operation')
                if not isinstance(pose, dict) or pose.get('mode') != 'pose':
                    diagnostics.append(dict(line=number, code='invalid_pose_operation'))
                    continue
                phase = dict(name=name, pose_operation=deepcopy(pose),
                             source='model_pose.jsonl', source_line=number,
                             at=event.get('at'), sim_step=event.get('sim_step'),
                             execution_phase=event.get('phase'), outcome=None)
                phases[rid].append(phase)
                pending[key].append(phase)
            elif pending[key]:
                phase = pending[key].popleft()
                phase.update(outcome=deepcopy(event.get('outcome')),
                             result_source_line=number, result_sim_step=event.get('sim_step'))
            else:
                diagnostics.append(dict(line=number, code='unmatched_pose_result'))
    for step in steps:
        if step.get('tool') == 'manip':
            step['resolved_pose_phases'] = phases.get(step['request_id'], [])
    by_index = {step['index']: step for step in steps}
    # build_transcript has already aligned model calls using returned evidence IDs.
    # Model call IDs and native request IDs belong to different namespaces.
    for field in ('model_transcript', 'tool_trace'):
        if field not in data:
            continue
        entries = []
        for original in data[field]:
            entry = dict(original)
            step = by_index.get(entry.get('step'))
            if entry.get('kind') == 'tool_call' and entry.get('tool') == 'manip' and step and step.get('tool') == 'manip':
                entry['resolved_pose_phases'] = deepcopy(step['resolved_pose_phases'])
            entries.append(entry)
        result[field] = entries
    result.update(steps=steps, pose_parameter_source=dict(
        file='model_pose.jsonl', sha256=digest.hexdigest(), diagnostics=diagnostics))
    return result
