"""Malformed model calls must not prevent publication of a failed trial."""
import importlib.util
import json
from pathlib import Path
import sys


def test_publish_retains_rejected_act_without_primitive(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / 'scripts/publish_demo_trials.py'
    spec = importlib.util.spec_from_file_location('publish_failed_trial', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cohort = tmp_path / 'cohort'
    trial = cohort / 'failed_attempt'
    (trial / 'run').mkdir(parents=True)
    summary = {'task': 'carrying_in_groceries', 'model': 'fixture',
               'reasoning_effort': 'low', 'demo_motion': False,
               'observation_mode': 'rgb_only',
               'task_definition_origin': 'custom_bddl_overlay'}
    audit = {'passed': False, 'model_act_sequence': ['grasp', None, 'navigate_to']}
    (trial / 'summary.json').write_text(json.dumps(summary))
    (trial / 'demo_audit.json').write_text(json.dumps(audit))
    site = tmp_path / 'site'
    monkeypatch.setattr(sys, 'argv', ['publish_demo_trials', '--trials', str(cohort),
        '--site-root', str(site), '--index-name', 'failure.html',
        '--cohort-title', 'Failed trial evidence'])

    module.main()

    page = (site / 'failure.html').read_text()
    assert 'grasp、缺少 primitive、navigate_to' in page
    assert '未通过' in page
    assert json.loads((site / 'cohort/failed_attempt/demo_audit.json').read_text()) == audit
    assert json.loads((trial / 'demo_audit.json').read_text()) == audit
