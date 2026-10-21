"""Fail-closed acceptance of a frozen custom evaluation release.

This checks preserved evidence, not the task author's declared readiness flag.
Reference feasibility, negative scoring and autonomous interface acceptance are
separate gates. A model need not solve the task to validate its interface.
"""
from __future__ import annotations

import json
from pathlib import Path

from .custom_tasks import load_spec, sha256


def evidence(root, ref):
    path = (root / ref['path']).resolve()
    if sha256(path) != ref['sha256']:
        raise ValueError('Evidence hash mismatch: ' + str(path))
    return json.loads(path.read_text())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identity(record, spec, spec_hash, release):
    expected = dict(task=spec['id'], instance=spec['instance_id'], seed=spec['base']['seed'],
                    spec_sha256=spec_hash, scene_sha256=spec['frozen_instance']['sha256'],
                    source_commit=release['source_commit'], source_sha256=release['source_sha256'])
    _require(all(record.get(k) == v for k, v in expected.items()), 'Reference identity/version mismatch')
    _require(all(record.get(k) for k in ('host', 'interpreter', 'gpu_uuid', 'unit', 'pid', 'output_path')),
             'Incomplete execution provenance')
    _require(record.get('model_used') is False and record.get('autonomous_success_claimed') is False,
             'Reference must be identified as privileged, without a model-success claim')
    _runtime_identity(record['backend'], release)


def _runtime_identity(backend, release):
    for key in ('packages', 'assets', 'omnigibson_source_commit'):
        _require(backend.get(key) and backend[key] == release['runtime'][key],
                 'Dependency/asset runtime changed: ' + key)
    _require(all(value and value != 'unresolved' for value in backend['assets'].values()),
             'Asset release version was not resolved')


def _physical(root, bundle, spec, spec_hash, release, *, negative=False):
    record = evidence(root, bundle['validation'])
    _identity(record, spec, spec_hash, release)
    _require(record['status'] == 'passed' and record.get('cleanup_status') == 'passed',
             'Physical validation or cleanup did not pass')
    launch = evidence(root, bundle['launch'])
    _require(launch['status'] == 'passed' and launch.get('exit_code') == 0
             and launch.get('output_path') == record['output_path'] and launch.get('unit') == record['unit'],
             'Simulator process did not complete successfully')
    initial = evidence(root, bundle['initial'])
    hashes = initial.get('input_hashes', {})
    _require(initial['status'] == 'passed' and hashes.get('custom_task_spec') == spec_hash
             and hashes.get('custom_initial_scene') == spec['frozen_instance']['sha256'],
             'Policy-ready fresh restore not verified for this instance')
    _require(initial['initial_evaluation']['task_success'] is False, 'Task was already solved at initialization')
    resources = evidence(root, bundle['admission'])
    assets = evidence(root, bundle['assets'])
    _require(resources['status'] == assets['status'] == 'passed', 'Resource/dependency/asset admission failed')
    _require(resources['gpu_uuid'] == record['gpu_uuid'] and resources['host'] == record['host'],
             'Admission belongs to a different host/GPU')
    asset_row = next((r for r in assets['tasks'] if r.get('task') == spec['id']), {})
    expected_assets = next(r['asset_sha256'] for r in release['tasks'] if r['task'] == spec['id'])
    _require(asset_row.get('custom_scene_sha256') == record['scene_sha256']
             and asset_row.get('status') == 'passed' and expected_assets
             and all(asset_row['files'][key]['sha256'] == digest for key, digest in expected_assets.items()),
             'Missing or changed pinned custom/base assets')
    _require(0 < record['action_attempts'] <= spec['acceptance_cases']['max_reference_action_attempts'],
             'Reference action budget was not respected')
    level = ('real_simulator_physical_negative_case' if negative else
             'real_simulator_checked_executor_privileged_full_reference')
    _require(record['validation_level'] == level, 'Wrong physical validation level')
    result = record['evaluation']
    _require(result.get('score_type') == spec['evaluation']['score_type']
             and result.get('model_notes_used_for_scoring') is False, 'Wrong evaluator provenance')
    if negative:
        scenario = record['negative_result']
        _require(record['negative_case'] == scenario['case'] and scenario['status'] == 'passed'
                 and scenario['checks'] and all(v is True for v in scenario['checks'].values())
                 and result['task_success'] is False, 'Negative witness was not reached or task was accepted')
    else:
        expected = {'terminal_conditions': set(spec['evaluation']['terminal']),
                    'historical_conditions': set(spec['evaluation'].get('milestones', {})),
                    'constraint_conditions': {r['id'] for kind in ('state_guards', 'motion_guards', 'carry_guards',
                                                                 'transition_guards')
                                              for r in spec['evaluation'].get(kind, [])}}
        _require(result['task_success'] is True and not result.get('missing_required_entities')
                 and not result['public_constraint_violations'], 'Reference did not solve the whole task')
        for section, keys in expected.items():
            _require(set(result[section]) == keys and all(v is True for v in result[section].values()),
                     'Incomplete independent scoring: ' + section)
    return record


def _policy(root, bundle, spec, spec_hash, release):
    from .audit import audit_episode
    run = evidence(root, bundle['run'])
    controller = evidence(root, bundle['controller'])
    run_dir = (root / bundle['run']['path']).resolve().parent
    controller_dir = (root / bundle['controller']['path']).resolve().parent
    audit = audit_episode(run_dir, controller_dir)
    _require(audit['evidence_alignment'] == audit['model_finish'] == audit['controller_exit'] == 'passed',
             'Autonomous RGB/MCP/finish evidence did not pass')
    _policy_source_identity(run['source'], release)
    _runtime_identity(run['backend'], release)
    inputs = run['evaluation']['input_hashes']
    _require(inputs['custom_task_spec'] == spec_hash
             and inputs['custom_initial_scene'] == spec['frozen_instance']['sha256'], 'Policy used a different instance')
    _require(controller['model'] == 'gpt-6.1-sol' and controller.get('model_reasoning_effort') == 'low',
             'Unexpected acceptance model/effort')
    config = run['config']
    _require(config.get('instruction') == '\n'.join([spec['public_instruction'], *spec['public_context']]),
             'Policy did not receive the complete frozen public requirements')
    _require(config.get('agent_profile') == 'skills' and config.get('require_task_notes') is True
             and config.get('max_agent_steps') == 200 and 0 < run.get('agent_steps', 0) <= 200
             and not config.get('wall_seconds') and not config.get('episode_deadline_unix'),
             'Policy acceptance did not exercise the frozen notes/action-budget protocol')
    observation = evidence(root, bundle['observation'])
    video = evidence(root, bundle['video_validation'])
    _require(observation['status'] == video['status'] == 'passed'
             and observation['run_id'] == video['run_id'] == run['run_id'], 'Observation/video acceptance failed')
    video_meta = evidence(root, bundle['video'])
    _require(video_meta['sha256'] == video['video_sha256'] == sha256(run_dir/video_meta['file']),
             'Video bytes no longer match validation')
    return {'run_id': run['run_id'], 'task_success': run.get('task_success'),
            'scope': 'autonomous interface acceptance; model success reported separately'}


def _policy_source_identity(source, release):
    _require(source.get('commit') == release['source_commit']
             and source.get('release_source_sha256') == release['source_sha256']
             and source.get('dirty') is False,
             'Policy used different or unverified executable source')


def validate_release(path):
    path = Path(path).resolve()
    root = path.parent
    release = json.loads(path.read_text())
    _require(release.get('schema_version') == 1 and release.get('official_submission_eligible') is False,
             'Unsupported custom release manifest')
    rows = []
    tasks = release.get('tasks', [])
    expected = set(release['required_task_ids'])
    _require(expected and {r['task'] for r in tasks} == expected and len(tasks) == len(expected),
             'Release does not cover its exact declared task scope')
    for row in tasks:
        checks = {}
        details = {}
        try:
            spec_path = (root / row['spec']['path']).resolve()
            _require(sha256(spec_path) == row['spec']['sha256'], 'Task contract changed')
            spec, _ = load_spec(spec_path, require_frozen=True)
            _require(row['task'] == spec['id'], 'Task ID mismatch')
            checks['static_contract'] = 'passed'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows.append({'task': row['task'], 'status': 'failed', 'errors': [str(exc)]})
            continue
        for group, negative in [('references', False), ('negatives', True)]:
            try:
                records = [_physical(root, bundle, spec, row['spec']['sha256'], release, negative=negative)
                           for bundle in row.get(group, [])]
                key = 'negative_case' if negative else 'order'
                requested = spec['acceptance_cases']['physical_negative_cases' if negative else 'reference_orders']
                _require({r[key] for r in records} == set(requested), 'Missing declared ' + group)
                _require(len({r['output_path'] for r in records}) == len(records), 'Duplicate evidence reused')
                if not negative:
                    _require(len({(r['host'], r['pid'], r['started_at_unix']) for r in records}) >=
                             spec['acceptance_cases']['fresh_restore_repetitions'], 'Not enough fresh simulator restores')
                checks[group] = 'passed'
            except (OSError, ValueError, KeyError, TypeError) as exc:
                checks[group] = 'failed'
                details[group] = str(exc)
        try:
            details['policy'] = _policy(root, row['policy_acceptance'], spec, row['spec']['sha256'], release)
            checks['policy_interface'] = 'passed'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks['policy_interface'] = 'failed'
            details['policy_interface'] = str(exc)
        rows.append({'task': row['task'], 'status': 'passed' if all(v == 'passed' for v in checks.values()) else 'failed',
                     'checks': checks, 'details': details})
    ready = bool(rows) and all(r['status'] == 'passed' for r in rows)
    return {'status': 'passed' if ready else 'failed', 'benchmark_ready': ready, 'tasks': rows,
            'scope': 'controlled custom evaluation on these exact frozen instances and source',
            'official_submission_eligible': False, 'manifest_sha256': sha256(path)}
