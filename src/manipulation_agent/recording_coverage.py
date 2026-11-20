"""Check exported frame ledgers against the final observed simulation step."""
import json
from pathlib import Path


def audit_recording_coverage(segments, required_final_step, *, decoded_frame_counts=None):
    """A playable prefix is incomplete when the final observation lies beyond it.

    Counts supplied by a video decoder take precedence over manifest claims.
    This function reads evidence only; it never renders or advances a simulator.
    """
    errors = []
    cursor = 0
    last_frame_step = None
    decoded_frame_counts = decoded_frame_counts or {}
    for segment in segments:
        root = Path(segment['root'])
        start, end = segment['start_step'], segment['end_step']
        meta = segment['video']
        if start != cursor:
            errors.append(f'Noncontiguous segment: expected {cursor}, got {start}')
        if end < start:
            errors.append(f'Reversed segment: {start}..{end}')
        cursor = end
        ledger = root/'video_frames.jsonl'
        if not ledger.is_file():
            errors.append(f'Missing frame ledger: {root}')
            continue
        rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        count = decoded_frame_counts.get(str(root), meta.get('frame_count', 0))
        if count != len(rows) or count != meta.get('frame_count'):
            errors.append(f'Frame count mismatch at {root}: decoded={count}, ledger={len(rows)}, manifest={meta.get("frame_count")}')
        available = rows[:count]
        if [row['frame_index'] for row in rows] != list(range(len(rows))):
            errors.append(f'Noncontiguous frame indices: {root}')
        recorded = [row['env_step'] for row in available if row['kind'] == 'env_step']
        if recorded != list(range(1, end-start+1)):
            errors.append(f'Missing recorded control steps: {root}')
        if not available or available[-1]['env_step'] != end-start:
            errors.append(f'Missing terminal frame: {root}')
        elif count:
            last_frame_step = start+available[-1]['env_step']
        if meta.get('status') != 'passed' or not meta.get('every_env_step_recorded'):
            errors.append(f'Unsealed or incomplete segment: {root}')
    if last_frame_step is None or last_frame_step < required_final_step:
        errors.append(f'Video ends at {last_frame_step}; final observation requires {required_final_step}')
    return dict(passed=not errors, required_final_step=required_final_step,
                last_exported_env_step=last_frame_step, errors=errors,
                frame_counts_verified_by_decoder=bool(decoded_frame_counts))


def require_complete_recording(coverage):
    """Refuse to publish a partial recording as a complete result."""
    if not coverage['passed']:
        raise ValueError('Incomplete recording: '+'; '.join(coverage['errors']))
