"""Integrity checks for separately sampled full-catalog BEHAVIOR instances."""
import copy
import hashlib
import json
from pathlib import Path

from .native_definition import resolve_definition
from .lineage_goals import validate_relational_goals


def complete_scene_context(frozen, template):
    """Restore omitted surroundings without replacing sampled objects or task state."""
    objects = frozen['objects_info']['init_info']
    states = frozen['state']['registry']['object_registry']
    template_objects = template['objects_info']['init_info']
    template_states = template['state']['registry']['object_registry']
    added = sorted(set(template_objects) - set(objects))
    for name in added:
        objects[name] = copy.deepcopy(template_objects[name])
        states[name] = copy.deepcopy(template_states[name])
    return added


def load_catalog_spec(path):
    path = Path(path).resolve()
    spec = json.loads(path.read_text())
    if spec.get('schema_version') != 1 or spec.get('kind') not in {
            'behavior1k_sampled_instance', 'custom_bddl_sampled_instance'}:
        raise ValueError('Expected a sampled native BDDL instance')
    resolve_definition(spec, path)
    validate_relational_goals(spec)
    support = spec.get('cutting_support')
    if support is not None:
        if (spec['kind'] != 'custom_bddl_sampled_instance' or not isinstance(support, dict)
                or set(support) != {'support_binding', 'target_bindings'}
                or not isinstance(support['support_binding'], str) or not support['support_binding']
                or not isinstance(support['target_bindings'], list) or not support['target_bindings']
                or any(not isinstance(x, str) or not x for x in support['target_bindings'])
                or len(set(support['target_bindings'])) != len(support['target_bindings'])):
            raise ValueError('Invalid custom cutting support constraint')
    if 'use_gpu_dynamics' in spec and type(spec['use_gpu_dynamics']) is not bool:
        raise ValueError('Native dynamics mode must be an explicit boolean')
    if not spec.get('public_instruction', '').isascii() or not spec['public_instruction'].strip():
        raise ValueError('An English final-goal description is required')
    terminal = spec.get('additional_terminal_conditions', [])
    if (not isinstance(terminal, list) or any(c != 'hand_empty' for c in terminal)
            or len(terminal) != len(set(terminal))):
        raise ValueError('Unsupported additional terminal conditions')
    if terminal and spec['kind'] != 'custom_bddl_sampled_instance':
        raise ValueError('Additional terminal conditions require a custom task')
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
