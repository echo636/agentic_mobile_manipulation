"""Unit tests for Milestone 4 (Task 6: tabletop_buffer_nonmonotonic_swap)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'src'))

from manipulation_agent.tasks.custom_tasks import validate_spec
from manipulation_agent.references.full_workflow_reference import validate_workflow_plan
from task_designs.scheduling_v1.tabletop_buffer_nonmonotonic_swap.make_recipe import build_recipe


def test_tabletop_buffer_swap_recipe_spec():
    spec = build_recipe()
    validation = validate_spec(spec)
    assert validation['status'] == 'passed'
    assert validation['task'] == 'tabletop_buffer_nonmonotonic_swap'
    assert validation['score_components']['terminal'] == 7
    assert validation['score_components']['milestones'] == 4
    assert validation['score_components']['constraints'] in (6, 8)


def test_tabletop_buffer_swap_workflow_plan():
    spec = build_recipe()
    plan_val = validate_workflow_plan(spec)
    assert plan_val['phase_count'] == 12
    assert plan_val['reference_actions'] in (79, 82, 83)

    # Verify orders
    orders = spec['reference_plan']['orders']
    assert 'optimal_carrier_tour' in orders
    assert 'habit_sequential' in orders

    # Verify negative omissions cover declared negative cases
    omissions = spec['reference_plan']['negative_omissions']
    neg_cases = spec['acceptance_cases']['physical_negative_cases']
    assert set(omissions.keys()) == set(neg_cases)
    assert 'omit_ws1_wipe' in omissions
    assert 'omit_buffer_restore' in omissions
    assert 'wipe_while_occupied' in omissions


def test_transition_guards_specification():
    spec = build_recipe()
    guards = spec['evaluation']['transition_guards']
    guard_ids = {g['id'] for g in guards}

    assert 'wipe_island_only_when_empty' in guard_ids
    assert 'wipe_dining_only_when_empty' in guard_ids
    assert 'place_island_only_when_clean_bowl' in guard_ids
    assert 'place_island_only_when_clean_teapot' in guard_ids
    assert 'place_dining_only_when_clean_ketchup' in guard_ids
    assert 'place_dining_only_when_clean_mug' in guard_ids
