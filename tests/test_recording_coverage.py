import json

import pytest

from manipulation_agent.recording_coverage import audit_recording_coverage, require_complete_recording


def segment(tmp_path, start, end):
    root = tmp_path/f'{start}-{end}'
    root.mkdir()
    rows = [dict(frame_index=i-1, env_step=i, kind='env_step') for i in range(1, end-start+1)]
    rows.append(dict(frame_index=len(rows), env_step=end-start, kind='observation_boundary'))
    (root/'video_frames.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return dict(root=str(root), start_step=start, end_step=end,
                video=dict(frame_count=len(rows), status='passed', every_env_step_recorded=True))


def test_playable_release_prefix_cannot_pass_final_settling_coverage(tmp_path):
    prefix = segment(tmp_path, 0, 1210)
    coverage = audit_recording_coverage([prefix], 1360)
    assert not coverage['passed']
    assert coverage['last_exported_env_step'] == 1210
    with pytest.raises(ValueError, match='1360'):
        require_complete_recording(coverage)


def test_sealed_tail_passes_without_closing_simulator(tmp_path):
    segments = [segment(tmp_path, 0, 1210), segment(tmp_path, 1210, 1300), segment(tmp_path, 1300, 1360)]
    coverage = audit_recording_coverage(segments, 1360,
        decoded_frame_counts={row['root']: row['video']['frame_count'] for row in segments})
    assert coverage['passed'] and coverage['frame_counts_verified_by_decoder']
    require_complete_recording(coverage)


def test_buffered_encoder_frames_cannot_be_counted_as_exported(tmp_path):
    tail = segment(tmp_path, 0, 150)
    coverage = audit_recording_coverage([tail], 150, decoded_frame_counts={tail['root']: 60})
    assert not coverage['passed']
    assert any('Frame count mismatch' in error for error in coverage['errors'])


def test_missing_segment_is_rejected_even_when_final_frame_exists(tmp_path):
    segments = [segment(tmp_path, 0, 1210), segment(tmp_path, 1300, 1360)]
    coverage = audit_recording_coverage(segments, 1360)
    assert not coverage['passed']
    assert any('Noncontiguous segment' in error for error in coverage['errors'])
