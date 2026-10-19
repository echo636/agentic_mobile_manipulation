"""CPU-only validation of public custom-task contracts and frozen instances.

Definitions and scene hashes establish identity, never physical feasibility.
The private requirement mapping is for audits; policies receive only the
instruction and public_context, not this mapping or the evaluator specification.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .long_horizon import TemporalGoalEvaluator, expression


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_spec(spec, *, spec_path=None, require_frozen=False):
    """Reject ambiguous/partly mapped v2 contracts before simulator startup."""
    if spec.get('schema_version') not in (1, 2):
        raise ValueError('Unknown custom task schema')
    for key in ('id', 'instance_id', 'public_instruction'):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError('Missing custom task field: ' + key)
    if spec.get('official_submission_eligible') is not False:
        raise ValueError('Custom tasks must explicitly exclude official submission')
    context = spec.get('public_context')
    if not isinstance(context, list) or not context or any(not isinstance(s, str) or not s.strip() for s in context):
        raise ValueError('Public task context must be nonempty text')
    roles = spec.get('roles', {})
    if not roles or len(set(roles.values())) != len(roles):
        raise ValueError('Custom roles need distinct entity bindings')
    evaluation = spec['evaluation']
    TemporalGoalEvaluator(evaluation)
    for key, row in evaluation['predicates'].items():
        if row.get('object') not in roles or (row.get('target') and row['target'] not in roles):
            raise ValueError('Predicate references unknown entity: ' + key)
        if not isinstance(row.get('state'), str) or not row['state']:
            raise ValueError('Predicate needs an explicit state: ' + key)
    values = dict.fromkeys(evaluation['predicates'], False)
    if not spec.get('initial_assertions'):
        raise ValueError('A custom task needs initial assertions')
    for expr in spec['initial_assertions'].values():
        expression(expr, values)
    for row in evaluation.get('motion_guards', []):
        if row['object'] not in roles:
            raise ValueError('Motion guard references unknown entity')
    counted = {
        'terminal': set(evaluation['terminal']),
        'milestones': set(evaluation.get('milestones', {})),
        'constraints': {row['id'] for kind in ('motion_guards', 'state_guards')
                        for row in evaluation.get(kind, [])},
    }
    if spec['schema_version'] == 2:
        requirements = spec.get('requirements', [])
        if not requirements or len({r['id'] for r in requirements}) != len(requirements):
            raise ValueError('Public requirements need unique IDs')
        if [r['text'] for r in requirements] != context:
            raise ValueError('Every requirement must be present verbatim in public_context')
        mapped = {key: set() for key in counted}
        for row in requirements:
            if not any(row.get(key) for key in counted):
                raise ValueError('Unscored public requirement: ' + row['id'])
            for key, allowed in counted.items():
                refs = row.get(key, [])
                if len(refs) != len(set(refs)) or not set(refs) <= allowed:
                    raise ValueError('Invalid scoring references for ' + row['id'])
                mapped[key].update(refs)
        if mapped != counted:
            raise ValueError('Hidden/unmapped scored requirements')
        checks = spec.get('acceptance_cases', {})
        orders = checks.get('reference_orders', [])
        negatives = checks.get('physical_negative_cases', [])
        if len(set(orders)) < 2 or len(orders) != len(set(orders)):
            raise ValueError('Declare at least two distinct reference orders')
        if not negatives or len(negatives) != len(set(negatives)):
            raise ValueError('Declare unique physical negative cases')
        restores = checks.get('fresh_restore_repetitions')
        budget = checks.get('max_reference_action_attempts')
        if type(restores) is not int or not 2 <= restores <= len(orders):
            raise ValueError('Fresh restore count must be at least two and covered by reference orders')
        if type(budget) is not int or not 1 <= budget <= 200:
            raise ValueError('Reference budget must be a positive integer within the 200-attempt pilot budget')
    frozen = spec.get('frozen_instance')
    scene_path = None
    if frozen:
        if spec_path is None:
            raise ValueError('Resolving a frozen scene requires the specification path')
        scene_path = (Path(spec_path).resolve().parent / frozen['path']).resolve()
        if sha256(scene_path) != frozen['sha256']:
            raise ValueError('Custom initial scene hash mismatch')
    elif require_frozen:
        raise ValueError('Construction recipe has no frozen initial scene')
    return {
        'status': 'passed', 'task': spec['id'], 'instance': spec['instance_id'],
        'schema_version': spec['schema_version'],
        'score_components': {key: len(value) for key, value in counted.items()},
        'scene_path': str(scene_path) if scene_path else None,
        'validation_level': 'static_public_contract_and_optional_scene_hash',
        'physical_feasibility_validated': False,
    }


def load_spec(path, *, require_frozen=False):
    path = Path(path)
    spec = json.loads(path.read_text())
    return spec, validate_spec(spec, spec_path=path, require_frozen=require_frozen)
