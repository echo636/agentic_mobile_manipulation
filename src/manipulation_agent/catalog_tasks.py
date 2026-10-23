"""Integrity checks for separately sampled full-catalog BEHAVIOR instances."""
import hashlib
import json
from pathlib import Path


def load_catalog_spec(path):
    path = Path(path).resolve()
    spec = json.loads(path.read_text())
    if spec.get('schema_version') != 1 or spec.get('kind') != 'behavior1k_sampled_instance':
        raise ValueError('Expected a sampled full-catalog BEHAVIOR instance')
    if not spec.get('public_instruction', '').isascii() or not spec['public_instruction'].strip():
        raise ValueError('An English final-goal description is required')
    for key in ('frozen_instance', 'sampling_record'):
        entry = spec[key]
        target = (path.parent / entry['path']).resolve()
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Catalog input hash mismatch: ' + key)
    sampled = json.loads((path.parent / spec['sampling_record']['path']).read_text())
    if (sampled.get('status') != 'passed' or sampled.get('model_used') is not False
            or sampled.get('task') != spec['task'] or sampled.get('scene') != spec['scene']
            or sampled.get('seed') != spec['seed']
            or sampled.get('bddl_sha256') != spec['bddl_sha256']
            or not sampled.get('initial_conditions') or not all(sampled['initial_conditions'])
            or sampled.get('initial_goal_satisfied') is not False
            or spec['frozen_instance']['sha256'] not in sampled.get('saved_files', {}).values()):
        raise ValueError('Catalog instance lacks matching successful sampling evidence')
    config = spec['environment_config']
    if (config['task']['activity_name'] != spec['task']
            or config['task'].get('activity_definition_id', 0) != 0
            or config['scene']['scene_model'] != spec['scene']):
        raise ValueError('Catalog environment does not match its sampled task and scene')
    return spec
