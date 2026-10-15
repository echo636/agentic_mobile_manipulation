"""Read-only project overview; keep attempts, cohorts and evidence levels separate."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import write_json

ARMS = ('hierarchical', 'unguided')
FINAL = {'passed', 'failed', 'blocked'}
SPECS = (
    ('original', '原 100 任务对照', 'hierarchical_goal_ablation_20261014', 100,
     '/hierarchical_goal_ablation_20261014/'),
    ('placement', '放置定向复测', 'long_horizon_execution_20261015/placement_retest', 28,
     '/long_horizon_execution_20261015/placement_retest/'),
    ('unscored', '无评分任务补测', 'long_horizon_execution_20261015/unscored_retest', 6,
     '/long_horizon_execution_20261015/unscored_retest/'),
)


def counts(rows):
    ended = [r for r in rows if r.get('status') in FINAL]
    return {'total': len(rows), 'completed': len(ended),
            'successes': sum(r.get('task_success') is True for r in ended),
            'scored': sum(type(r.get('task_success')) is bool for r in ended),
            'unscored': sum(type(r.get('task_success')) is not bool for r in ended),
            'io_faults': sum(bool(r.get('io_fault')) for r in ended),
            'running': sum(r.get('status') == 'running' for r in rows),
            'planned': sum(r.get('status') == 'planned' for r in rows)}


def compact_row(row, base, io_runs, protected_commits):
    commit = (row.get('run_source') or row.get('source') or {}).get('commit')
    if row.get('status') == 'planned':
        commit = None
    result = {k: row.get(k) for k in (
        'index', 'run_id', 'task', 'name', 'instruction', 'instruction_source', 'status', 'stage',
        'task_success', 'q_score', 'agent_steps', 'termination_reason', 'worker_id',
        'video_validation', 'evidence_alignment', 'updated_at')}
    result['source_commit'] = commit
    result['io_protected'] = commit in protected_commits if commit else None
    result['io_fault'] = row.get('run_id') in io_runs or any(
        'Input/output error' in str(row.get(k, '')) for k in ('failure', 'archive_failure'))
    result['planning'] = {k: (row.get('planning_metrics') or {}).get(k) for k in
                          ('notes_writes', 'goal_tree_versions', 'notes_before_first_action')}
    result['replay_url'] = urljoin(base, row['replay_url']) if row.get('replay_url') else None
    result['instruction_kind'] = 'detailed' if row.get('index', 0) < 50 else 'short'
    return result


class Overview:
    def __init__(self, workspace):
        self.root = Path(workspace)
        self.cache = {}
        self.warnings = []

    def read(self, path, default=None, *, optional=False):
        try:
            value = json.loads(path.read_text())
            self.cache[path] = value
            return deepcopy(value)
        except (OSError, ValueError) as exc:
            if not optional:
                self.warnings.append({'source': str(path.relative_to(self.root)) if path.is_relative_to(self.root)
                                      else path.name, 'error': type(exc).__name__,
                                      'retained_previous': path in self.cache})
            return deepcopy(self.cache.get(path, default))

    def build(self):
        self.warnings = []
        ops = self.root / 'operations'
        long = ops / 'long_horizon_execution_20261015'
        placement = long / 'placement_retest'
        deployment = self.read(placement / 'io_isolation_repair/deployment_validation.json', {}, optional=True)
        protected = {a['source']['commit'] for a in deployment.get('arms', [])}
        io_runs = set()
        audit_path = placement / 'io_fault_audit.jsonl'
        try:
            for line in audit_path.read_text().splitlines():
                audit = json.loads(line)
                for run in audit.get('run_records', []):
                    if '_io' in run.get('diagnosis', ''):
                        io_runs.add(run['run_id'])
            self.cache[audit_path] = io_runs
        except (OSError, ValueError):
            io_runs = self.cache.get(audit_path, set())
        cohorts = []
        active = []
        for key, title, relative, expected, url in SPECS:
            cohort = {'id': key, 'title': title, 'url': url, 'expected_per_arm': expected, 'arms': {}}
            for arm in ARMS:
                path = ops / relative / arm / 'progress.json'
                progress = self.read(path)
                if progress is None:
                    cohort['arms'][arm] = {'available': False, 'summary': None, 'rows': []}
                    continue
                rows = [compact_row(r, url + arm + '/', io_runs, protected) for r in progress['tasks']]
                entry = {'available': True, 'updated_at': progress.get('updated_at'), 'summary': counts(rows),
                         'url': url + arm + '/behavior100.html', 'rows': rows,
                         'source_commits': dict(Counter(r['source_commit'] for r in rows if r['source_commit'])),
                         'protocol': {k: progress.get('protocol', {}).get(k) for k in
                                      ('model', 'model_reasoning_effort', 'max_agent_steps', 'goal_skill')}}
                if key == 'original':
                    entry['instruction_cohorts'] = {kind: counts([r for r in rows if r['instruction_kind'] == kind])
                                                    for kind in ('detailed', 'short')}
                if key == 'placement':
                    entry['io_phases'] = {phase: counts([r for r in rows if r['io_protected'] is value])
                                          for phase, value in [('before', False), ('after', True)]}
                    # This file is local to the coordinator host; publish only
                    # compact health fields, not private paths or environments.
                    arm_deployment = next((a for a in deployment.get('arms', []) if a['arm'] == arm), {})
                    spool = arm_deployment.get('local_spool')
                    sync = self.read(Path(spool) / 'sync_status.json', {}, optional=True) if spool else {}
                    entry['storage_sync'] = {k: sync.get(k) for k in
                                             ('status', 'failures', 'updated_at', 'revision', 'synced_revision')}
                cohort['arms'][arm] = entry
                active.extend({**r, 'cohort': key, 'cohort_title': title, 'arm': arm, 'arm_url': entry['url']}
                              for r in rows if r['status'] == 'running')
            cohorts.append(cohort)
        diagnostics = self.read(self.root / 'reports/long_horizon_execution_20261015/status.json', {}, optional=True)
        custom = {}
        for arm in ARMS:
            progress = self.read(long / 'custom_pilot' / arm / 'progress.json', optional=True)
            custom[arm] = {'status': 'planned'} if progress is None else {
                'status': 'running', 'summary': counts(progress.get('tasks', [])),
                'url': '/long_horizon_execution_20261015/custom_pilot/'}
        return {'updated_at': datetime.now(timezone.utc).isoformat(), 'cohorts': cohorts, 'active': active,
                'custom_autonomous': custom,
                'diagnostics_updated_at': diagnostics.get('updated_at'),
                'diagnostics': [{k: r.get(k) for k in ('run_id', 'initial_state', 'latest_result', 'service')}
                                for r in diagnostics.get('diagnostics', [])],
                'warnings': self.warnings, 'io_fix_commits': sorted(protected),
                'notes': {'failure_audit_snapshot': '2026-10-15 15:06 CST',
                          'success_rule': 'Independent evaluator success among ended attempts; cohorts are not merged.'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    args.reports.mkdir(parents=True, exist_ok=True)
    page = Path(__file__).with_name('project_dashboard.html').read_text()
    temporary = args.reports / 'index.html.tmp'
    temporary.write_text(page); temporary.replace(args.reports / 'index.html')
    overview = Overview(args.workspace)
    while True:
        try:
            result = overview.build()
            write_json(args.reports / 'project_status.json', result)
            print(json.dumps({'at': result['updated_at'], 'active': len(result['active']),
                              'warnings': len(result['warnings'])}), flush=True)
        except (OSError, ValueError) as exc:
            print(json.dumps({'status': 'refresh_failed', 'error': str(exc)}), file=sys.stderr, flush=True)
            if not args.watch:
                raise
        if not args.watch:
            break
        time.sleep(20)


if __name__ == '__main__':
    main()
