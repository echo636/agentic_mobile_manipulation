"""CPU-only unit tests for the Dual-Layer Benchmark Suite (`dual_benchmark_suite_20261115.json`)
and Tier-2 zero-wait task T7 (`blind_cabinet_exact_restock_unlock`).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'src'))

from manipulation_agent.references.full_workflow_reference import validate_workflow_plan  # noqa: E402
from manipulation_agent.tasks.custom_tasks import sha256, validate_spec  # noqa: E402
from task_designs.scheduling_v1.blind_cabinet_exact_restock_unlock.make_recipe import (  # noqa: E402
    build_recipe,
    one_of_three_partition,
)

FORBIDDEN_STRATEGY_HINTS = (
    r'\bbatch\b',
    r'\bbatching\b',
    r'\bin one trip\b',
    r'\bsingle trip\b',
    r'\binspect first\b',
    r'\bopen first\b',
    r'\bcheck first\b',
    r'\bbefore going\b',
    r'\bbefore walking\b',
    r'\boptimal\b',
    r'\befficient\b',
    r'\bsave trips\b',
    r'\bin order\b',
    r'\bwhile waiting\b',
)


def _eval_formula(formula: str | dict, valuation: dict[str, bool]) -> bool:
    if isinstance(formula, str):
        return bool(valuation[formula])
    if 'not' in formula:
        return not _eval_formula(formula['not'], valuation)
    if 'all' in formula:
        return all(_eval_formula(sub, valuation) for sub in formula['all'])
    if 'any' in formula:
        return any(_eval_formula(sub, valuation) for sub in formula['any'])
    raise ValueError(f'Unsupported formula: {formula!r}')


def test_t7_blind_cabinet_exact_restock_unlock_recipe_and_frozen_task() -> None:
    spec = build_recipe()
    validation = validate_spec(spec, require_frozen=False)
    assert validation['status'] == 'passed'
    assert validation['task'] == 'blind_cabinet_exact_restock_unlock'
    assert validation['score_components'] == {'terminal': 9, 'milestones': 0, 'constraints': 1}
    assert 'timed_processes' not in spec, 'T7 must be a strict 0-second zero-wait planning task'

    plan_val = validate_workflow_plan(spec)
    assert plan_val['phase_count'] == 11
    assert set(spec['reference_plan']['orders']) == {
        'optimal_inspect_unlock_carrier_loop',
        'habit_blind_overfetch_sequential',
    }
    assert set(spec['acceptance_cases']['physical_negative_cases']) == {
        'violate_toolbox_book_guard',
        'omit_cabinet_restock',
        'omit_guest_lamp_lighting',
        'omit_cabinet_doors_closing',
    }
    assert set(spec['reference_plan']['negative_omissions']) == set(
        spec['acceptance_cases']['physical_negative_cases']
    )

    guards = spec['evaluation']['transition_guards']
    assert [g['id'] for g in guards] == ['toolbox_open_requires_books_cleared']

    frozen_path = REPO_ROOT / 'task_designs/scheduling_v1/blind_cabinet_exact_restock_unlock/task.json'
    assert frozen_path.is_file()
    frozen_spec = json.loads(frozen_path.read_text(encoding='utf-8'))
    assert frozen_spec['frozen_instance']['path'] == 'initial_scene.json'
    assert len(frozen_spec['frozen_instance']['sha256']) == 64
    unfrozen_copy = {k: v for k, v in frozen_spec.items() if k != 'frozen_instance'}
    assert validate_spec(unfrozen_copy, require_frozen=False)['status'] == 'passed'


def test_t7_public_instruction_is_strategy_neutral() -> None:
    spec = build_recipe()
    texts = [
        spec['public_instruction'],
        *spec.get('public_context', []),
        *(req['text'] for req in spec.get('requirements', [])),
    ]
    pattern = re.compile('|'.join(FORBIDDEN_STRATEGY_HINTS), re.IGNORECASE)
    for text in texts:
        match = pattern.search(text)
        assert match is None, f'Forbidden strategy hint {match.group(0)!r} found in T7 public text: {text!r}'


def test_t7_exact_three_way_partition_rejects_overstock_and_missing_surplus() -> None:
    formula = one_of_three_partition(
        ('e1', 'e2', 'e3'),
        ('w1', 'w2', 'w3'),
        ('s1', 's2', 's3'),
    )
    all_keys = ('e1', 'e2', 'e3', 'w1', 'w2', 'w3', 's1', 's2', 's3')

    # All 6 exact 1-east, 1-west, 1-south permutations must pass
    for i, j, k in ((1, 2, 3), (1, 3, 2), (2, 1, 3), (2, 3, 1), (3, 1, 2), (3, 2, 1)):
        val = {key: False for key in all_keys}
        val[f'e{i}'] = True
        val[f'w{j}'] = True
        val[f's{k}'] = True
        assert _eval_formula(formula, val) is True

    # Over-stocking north cabinet (2 in east, 1 in west, 0 left on south utility counter) must fail
    overstock = {key: False for key in all_keys}
    overstock['e1'] = True
    overstock['e3'] = True
    overstock['w2'] = True
    assert _eval_formula(formula, overstock) is False

    # Leaving surplus off the south utility counter (1 in east, 1 in west, 0 on south) must fail
    missing_surplus = {key: False for key in all_keys}
    missing_surplus['e1'] = True
    missing_surplus['w2'] = True
    assert _eval_formula(formula, missing_surplus) is False


def test_no_large_initial_scene_json_tracked_under_task_designs() -> None:
    leaked = list((REPO_ROOT / 'task_designs').rglob('initial_scene.json'))
    assert leaked == [], f'Large initial_scene.json must stay outside Git, found: {leaked}'


def test_dual_benchmark_suite_manifest_integrity() -> None:
    manifest_path = REPO_ROOT / 'task_designs/dual_benchmark_suite_20261115.json'
    doc_path = REPO_ROOT / 'docs/dual_benchmark_suite_20261115.md'
    assert manifest_path.is_file()
    assert doc_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['schema_version'] == 1

    # Verify 3-tier evaluation ladder & 6-metric schema
    ladder = manifest['evaluation_ladder']
    assert [arm['tier_index'] for arm in ladder] == [1, 2, 3]
    for arm in ladder:
        assert arm['chinese_name']
        assert 'vision_cli_args' in arm
        assert 'mcp_server_args' in arm
        assert 'controller_args' in arm

    metrics = manifest['metrics_schema']['required_metrics']
    assert len(metrics) == 6
    assert {m['id'] for m in metrics} == {
        'completion_and_subgoals',
        'agent_steps',
        'robot_time_seconds',
        'trajectory_length_meters',
        'wall_clock_seconds',
        'token_usage',
    }

    # Verify Tier 1 native BEHAVIOR-1K tasks against src/manipulation_agent/tasks.json
    tasks_catalog = json.loads((REPO_ROOT / 'src/manipulation_agent/tasks.json').read_text(encoding='utf-8'))['tasks']
    tier1_native = manifest['tier1_original_behavior1k']['native_bddl_tasks']
    assert len(tier1_native) == 4
    for entry in tier1_native:
        task_name = entry['task']
        assert task_name in tasks_catalog
        assert entry['instance'] == 301
        assert entry['seed'] == 0
        official_instr = tasks_catalog[task_name]['instruction']
        assert entry['instruction'] == official_instr
        assert entry['instruction_sha256'] == hashlib.sha256(official_instr.encode('utf-8')).hexdigest()
        assert len(entry['bddl_sha256']) == 64
        assert len(entry['instance_301_sha256']) == 64

    stress_anchor = manifest['tier1_original_behavior1k']['ultra_long_stress_anchor']
    assert stress_anchor['id'] == 'kitchen_v2_tidy'
    assert len(stress_anchor['task_sha256']) == 64
    assert len(stress_anchor['scene_sha256']) == 64

    # Verify Tier 2 multi-mode tasks T1-T7 against repo task.json files & SHA-256 hashes
    tier2 = manifest['tier2_multimode_long_horizon']['tasks']
    assert [t['code'] for t in tier2] == ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7']
    zero_wait = [t for t in tier2 if t['appliance_wait_seconds'] == 0]
    assert [t['code'] for t in zero_wait] == ['T4', 'T5', 'T6', 'T7']

    for entry in tier2:
        spec_path = REPO_ROOT / entry['repo_task_json']
        assert spec_path.is_file(), f'Missing repo task.json: {spec_path}'
        assert sha256(spec_path) == entry['task_sha256']
        spec_data = json.loads(spec_path.read_text(encoding='utf-8'))
        assert spec_data['id'] == entry['task_id']
        assert spec_data['frozen_instance']['sha256'] == entry['scene_sha256']

    t7_entry = next(t for t in tier2 if t['code'] == 'T7')
    ref_cmp = t7_entry['privileged_reference_comparison']
    assert ref_cmp['optimal']['task_success'] is True
    assert ref_cmp['habit']['task_success'] is True
    assert ref_cmp['robot_time_saving_pct'] >= 20.0
    assert ref_cmp['travel_distance_saving_pct'] >= 35.0
    assert ref_cmp['cross_zone_one_way_astar_m'] >= 15.0
