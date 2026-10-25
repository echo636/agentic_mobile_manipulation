"""A past one-off success must not inflate the current demo reliability cohort."""
import json
from pathlib import Path

from scripts.demo_reliability_report import report


def trial(root, attempt, source, passed):
    path = root / f'01_turning_on_radio_camera_r{attempt}'
    (path / 'run').mkdir(parents=True)
    (path / 'controller').mkdir()
    (path / 'demo_audit.json').write_text(json.dumps({
        'passed': passed, 'model': 'gpt-6-astra',
        'reasoning_effort': 'low', 'motion_segments': 2}))
    (path / 'run' / 'run.json').write_text(json.dumps({
        'source': {'commit': source, 'source_sha256': source},
        'skill_bundle_sha256': 'skills',
        'backend': {'image_size': 512, 'camera_rig': {'pitch_down_degrees': 35},
                    'demo_motion': True},
        'config': {'instance': 301, 'seed': 0},
        'evaluation': {'official_task_success': passed},
        'agent_outcome': 'achieved' if passed else 'blocked',
        'wall_seconds': 30, 'video': {'fps': 30, 'frame_count': 90},
    }))
    (path / 'controller' / 'controller.json').write_text(json.dumps({'status': 'passed'}))
    (path / 'run' / 'events.jsonl').write_text(json.dumps({
        'kind': 'tool_result', 'name': 'act',
        'result': {'ok': False, 'error': {'code': 'fixed_object'}}}) + '\n')


def test_report_separates_source_versions_and_counts_failed_actions(tmp_path: Path):
    trial(tmp_path, 1, 'old-source', True)
    trial(tmp_path, 2, 'new-source', False)
    result = report(tmp_path)
    task = result['tasks'][0]
    assert task['historical_passing_attempts'] == [1]
    assert task['latest_configuration']['audited_attempts'] == 1
    assert task['latest_configuration']['passing_attempts'] == 0
    assert task['latest']['action_errors'] == {'fixed_object': 1}
