#!/usr/bin/env python3
"""Build matched custom-task manifests only after the release evidence passes.

These are inputs to run_behavior100.py with a separate custom-task batch config;
the historical runner name does not make this cohort a BEHAVIOR100 result.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.references.custom_readiness import validate_release
from manipulation_agent.tasks.custom_tasks import load_spec, sha256


def prepare(release_path, asset_manifest_path, output, tag):
    if not re.fullmatch(r'[a-z0-9_]+', tag):
        raise ValueError('Batch tag must contain lowercase letters, digits and underscores')
    gate = validate_release(release_path)
    if not gate['benchmark_ready']:
        raise ValueError('Release is not ready for evaluation: ' + json.dumps(gate['tasks']))
    release = json.loads(release_path.read_text())
    assets = json.loads(asset_manifest_path.read_text())
    rows = []
    budgets = set()
    versions = set()
    for index, row in enumerate(release['tasks']):
        path = (release_path.parent / row['spec']['path']).resolve()
        spec, _ = load_spec(path, require_frozen=True)
        budget = spec.get('design_ledger', {}).get('proposed_model_act_look_budget', 200)
        if type(budget) is not int or budget <= 0:
            raise ValueError('Task model act/look budget must be a positive integer')
        budgets.add(budget)
        versions.add(spec['schema_version'])
        asset = next(r for r in assets['tasks'] if r['task'] == spec['id'])
        if (asset['custom_task_spec_sha256'] != sha256(path)
                or asset['custom_instance_sha256'] != spec['frozen_instance']['sha256']):
            raise ValueError('Asset manifest does not bind this frozen task')
        public = '\n'.join([spec['public_instruction'], *spec['public_context']])
        rows.append(dict(index=index, task=spec['id'], name=spec['public_instruction'],
                         scene=asset['scene'], instance=spec['base']['instance'],
                         custom_instance_id=spec['instance_id'], seed=spec['base']['seed'],
                         split=f'custom_long_horizon_v{spec["schema_version"]}_controlled', instruction=public,
                         instruction_sha256=hashlib.sha256(public.encode()).hexdigest(),
                         instruction_source='custom_public_context', bddl_sha256=asset['bddl_sha256'],
                         asset_sha256=asset['asset_sha256'], custom_task_spec=str(path),
                         custom_task_spec_sha256=sha256(path),
                         custom_instance_sha256=spec['frozen_instance']['sha256'],
                         status='planned', stage='queued', attempt=1,
                         source_commit=release['source_commit'], full_feasibility_validated=True))
    if len(budgets) != 1 or len(versions) != 1:
        raise ValueError('Split tasks with different model budgets or schema versions into separate cohorts')
    budget = budgets.pop()
    task_version = versions.pop()
    common = dict(schema_version=2, benchmark=f'custom_long_horizon_v{task_version}', evaluation_stage='benchmark',
                  official_submission_eligible=False,
                  source_commit=release['source_commit'], readiness_release=str(release_path.resolve()),
                  readiness_manifest_sha256=sha256(release_path), readiness_validation=gate,
                  model='gpt-6.1-sol', model_reasoning_effort='low', agent_profile='skills',
                  goal_skill=None, require_task_notes=True, model_timeout_seconds=None,
                  episode_budget_policy='agent_steps', max_agent_steps=budget,
                  max_actions=budget, max_sim_steps=1000000, startup_timeout_seconds=1200,
                  simulator_runtime_max_seconds=None, policy_retries=0,
                  observation_protocol='initialize_then_action_feedback', actuator_mode='idealized',
                  executor_profile='rgb_gt_assisted_checked_executor',
                  historical_results_imported=False, full_feasibility_validated=True,
                  scope=f'{len(rows)} frozen custom instances; no generalization claim', tasks=rows)
    output.mkdir(parents=True, exist_ok=False)
    for arm in ('hierarchical', 'unguided'):
        manifest = copy.deepcopy(common)
        manifest.update(goal_skill='none' if arm == 'unguided' else 'hierarchical',
                        comparison_arm=arm, comparison_parent=tag,
                        batch_title=f'Custom long-horizon v{task_version} / '+arm)
        for row in manifest['tasks']:
            row['run_id'] = f'mas_{tag}_{arm}_{row["index"]:03d}_{row["task"]}_r1'
        directory = output/arm
        directory.mkdir()
        (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (output/'readiness_validation.json').write_text(json.dumps(gate, indent=2)+'\n')
    return {'status': 'passed', 'output': str(output), 'arms': 2, 'tasks_per_arm': len(rows),
            'launch_status': 'not_started'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--asset-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.release, args.asset_manifest, args.output, args.tag)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
