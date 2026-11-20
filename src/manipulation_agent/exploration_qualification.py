"""Operator-owned acceptance for exact frozen bundles, never model self-scoring."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .records import write_json

DEFAULT_QUALIFICATION = {'required_successes': 2, 'metric': 'official_task_success',
                         'distinct_instances': True}


def policy(config):
    value = config.get('qualification')
    # Historical sessions retain their old admission rule, not a success claim.
    if value is None:
        return None
    value = {**DEFAULT_QUALIFICATION, **value}
    if type(value['required_successes']) is not int or value['required_successes'] < 2:
        raise ValueError('New qualification requires at least two independent successes')
    if value['metric'] not in {'official_task_success', 'engineering_success'}:
        raise ValueError('Unknown independent qualification metric')
    if type(value['distinct_instances']) is not bool:
        raise ValueError('distinct_instances must be a boolean')
    if value['metric'] == 'engineering_success':
        if not isinstance(value.get('evaluator_path'), str) or not Path(value['evaluator_path']).is_absolute():
            raise ValueError('Engineering qualification requires an operator-owned absolute evaluator path')
        if not isinstance(value.get('evaluator_sha256'), str) or len(value['evaluator_sha256']) != 64:
            raise ValueError('Engineering evaluator source must be pinned by SHA256')
    return value


def assess(config, record, output):
    Path(output).mkdir(parents=True, exist_ok=True)
    rule = policy(config)
    if rule is None:
        return {'passed': False, 'metric': 'legacy_clean_rehearsal',
                'reason': 'Legacy admission is not independent success qualification'}
    execution = record['execution']
    result = {'passed': False, 'metric': rule['metric'],
              'bundle': record['bundle'], 'run_id': record['run_id']}
    independent = record['evaluation'].get('official_task_success') is True
    if rule['metric'] == 'engineering_success':
        path = Path(rule['evaluator_path']).resolve()
        workspace = Path(config.get('session_workspace', '/nonexistent-workspace')).resolve()
        if path.is_relative_to(workspace):
            raise ValueError('The exploration agent cannot author its own acceptance evaluator')
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != rule['evaluator_sha256']:
                raise ValueError('Independent evaluator changed')
            request = {'run_dir': record['evaluation'].get('run_dir'),
                       'goal': rule.get('goal', {}), 'record': record}
            completed = subprocess.run([sys.executable, str(path)], input=json.dumps(request),
                                       text=True, capture_output=True, timeout=60)
            audit = json.loads(completed.stdout) if completed.returncode == 0 else {
                'engineering_success': False, 'error': completed.stderr[-4000:]}
            independent = audit.get('engineering_success') is True
            result.update(evaluator_sha256=actual, independent_audit=audit)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            independent = False
            result['evaluator_error'] = str(exc)
    # Preserve the raw official score regardless of this stricter code gate.
    result['independent_success'] = independent
    result['passed'] = (independent and execution.get('clean') is True and
                        execution.get('unattended') is True and execution.get('no_live_agent') is True and
                        not any(c.get('ok') is False for c in execution.get('calls', [])))
    result['reason'] = 'independent acceptance and unattended execution' if result['passed'] else 'independent acceptance or unattended execution not established'
    write_json(Path(output) / 'qualification.json', result)
    return result


def qualification(config, bundle, rehearsals):
    rule = policy(config)
    if rule is None:
        admitted = any(r['bundle'] == bundle and r['execution'].get('clean') for r in rehearsals)
        return {'qualified': admitted, 'metric': 'legacy_clean_rehearsal',
                'required_successes': None, 'independent_successes': 0,
                'legacy_admission_only': True}
    seen, instances, accepted = set(), set(), []
    for row in rehearsals:
        if row['bundle'] != bundle or row.get('qualification', {}).get('passed') is not True:
            continue
        episode = row.get('episode_key')
        identity = row.get('episode_identity', {})
        instance = (identity.get('task'), identity.get('instance'))
        run = row['evaluation'].get('run_dir')
        if not episode or not run or episode in seen or run in seen:
            continue
        if rule['distinct_instances'] and (None in instance or instance in instances):
            continue
        seen.update((episode, run))
        instances.add(instance)
        accepted.append(row['run_id'])
    return {'qualified': len(accepted) >= rule['required_successes'],
            'metric': rule['metric'], 'required_successes': rule['required_successes'],
            'independent_successes': len(accepted), 'accepted_run_ids': accepted,
            'distinct_instances_required': rule['distinct_instances'],
            'scope': 'cross-instance' if rule['distinct_instances'] else 'independent repeatability only'}
