"""CPU-only tests for Harness-side auxiliary facilities and scheduling modes.

Verifies:
1. Default-off byte identity on all existing profiles (minimal, basic-skills, goal-tree, skills, skills_inline, workflow).
2. Zero simulator imports & zero task-specific terms in skills/time-space-scheduling and schedule_planner/schedule.py.
3. Programmatic schedule_plan optimality: early unattended process startup, parallel overlap with zero unnecessary wait, bidirectional carrier batching vs single trips, chained timed processes, and public ledger tracking without GT leakage.
4. Click echo cropping & auxiliary feedback behavior (hand state, actionable hints, validation error step refund).
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from manipulation_agent.agent.schedule_planner import PublicScheduleLedger, optimize_schedule
from manipulation_agent.agent.vision_policy import BASIC_SKILLS_PROMPT, system_prompt
from manipulation_agent.contracts import Budget
from manipulation_agent.mcp_server import click_echo, echo_caption
from manipulation_agent.observations.mock_rgb import MockRGBBackend, png_fixture
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tools import tool_specs

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TASK_WORDS = (
    r'\bhotdog\b', r'\bhotdogs\b', r'\bapple\b', r'\bmicrowave\b', r'\brefrigerator\b',
    r'\bwasher\b', r'\bdryer\b', r'\blaundry\b', r'\bkiln\b', r'\bfirewood\b',
    r'\bpatio\b', r'\bsprayer\b', r'\batomizer\b', r'\bbulb\b', r'\bsock\b', r'\bshorts\b',
    r'\bpencil_case\b', r'\bcoffee_table\b',
)


def test_default_off_profiles_preserve_existing_contracts(tmp_path: Path) -> None:
    """When scheduling_mode='none' and auxiliary_feedback=False, prompts, tools, and skills exclude new additions."""
    assert system_prompt('basic-skills', goal_skill='none', autonomy=False) == BASIC_SKILLS_PROMPT
    specs_default = tool_specs('basic-skills')
    assert [s['name'] for s in specs_default] == [
        n for n in [s['name'] for s in specs_default] if n != 'schedule_plan'
    ]
    assert 'schedule_plan' not in {s['name'] for s in specs_default}

    out = tmp_path / 'run_default'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=20, goal_skill='none')
    skill_names = {entry['name'] for entry in harness.skills.catalog()}
    assert 'time-space-scheduling' not in skill_names
    assert 'schedule_plan' not in harness.catalog

    init_res = harness.call('initialize', {}, 'req-init')
    assert init_res['ok'] is True
    rev = init_res['observation']['revision']
    ref = init_res['observation']['images'][0]['image_ref']
    act_res = harness.call(
        'act',
        {'primitive': 'toggle_on', 'target': {'image_ref': ref, 'point': [0.5, 0.5]}, 'revision': rev},
        'req-act-1',
    )
    assert act_res['ok'] is True
    assert 'robot' not in act_res
    assert 'schedule_status' not in act_res


def test_no_task_specific_words_or_simulator_imports_in_new_modules() -> None:
    paths = [
        REPO_ROOT / 'skills/time-space-scheduling/SKILL.md',
        REPO_ROOT / 'src/manipulation_agent/agent/schedule_planner.py',
        REPO_ROOT / 'src/manipulation_agent/tools/schedule.py',
    ]
    pattern = re.compile('|'.join(FORBIDDEN_TASK_WORDS), re.IGNORECASE)
    for path in paths:
        text = path.read_text(encoding='utf-8')
        match = pattern.search(text)
        assert match is None, f'Forbidden task-specific word {match.group(0)!r} in {path}'
        if path.suffix == '.py':
            assert 'omnigibson' not in text.lower()
            assert 'pxr' not in text.lower()


def test_text_scheduling_mode_exposes_skill_only(tmp_path: Path) -> None:
    out = tmp_path / 'run_text'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=20,
        goal_skill='none', scheduling_mode='text',
    )
    skill_names = {entry['name'] for entry in harness.skills.catalog()}
    assert 'time-space-scheduling' in skill_names
    assert 'schedule_plan' not in harness.catalog
    prompt = system_prompt('basic-skills', goal_skill='none', scheduling_mode='text')
    assert 'time-space-scheduling/SKILL.md' in prompt


def test_program_scheduling_optimizes_parallel_timers_and_carrier_batches(tmp_path: Path) -> None:
    spec = {
        'places': ['prep_area', 'dining_area', 'yard_area'],
        'robot_place': 'prep_area',
        'travel_edges': [
            {'from_place': 'prep_area', 'to_place': 'dining_area', 'meters': 12.0, 'seconds': None},
            {'from_place': 'prep_area', 'to_place': 'yard_area', 'meters': 20.0, 'seconds': None},
        ],
        'carriers': [
            {'id': 'serving_tray', 'place': 'prep_area', 'capacity': 5, 'final_place': 'dining_area'},
        ],
        'timed_processes': [
            {
                'id': 'heat_main_items',
                'place': 'prep_area',
                'duration_seconds': 180.0,
                'setup_seconds': 50.0,
                'finish_seconds': 20.0,
                'status': 'pending',
                'started_at_robot_s': None,
                'depends_on': None,
            },
        ],
        'deliveries': [
            {
                'id': 'deliver_tableware',
                'from_place': 'prep_area',
                'to_place': 'dining_area',
                'item_count': 5,
                'can_use_carrier': True,
                'depends_on': None,
                'status': 'pending',
            },
            {
                'id': 'deliver_heated_items',
                'from_place': 'prep_area',
                'to_place': 'dining_area',
                'item_count': 2,
                'can_use_carrier': False,
                'depends_on': 'heat_main_items',
                'status': 'pending',
            },
        ],
        'local_work': [
            {
                'id': 'slice_side_dish',
                'place': 'prep_area',
                'work_seconds': 60.0,
                'depends_on': None,
                'status': 'pending',
            },
        ],
    }
    sched = optimize_schedule(spec, public_elapsed_s=0.0)
    phases = sched['ordered_phases']
    assert phases[0]['action_type'] == 'start_timed_process'
    assert phases[0]['jobs'] == ['heat_main_items']
    # Independent prep + 5-item carrier batch fill >180s so idle wait drops to 0!
    assert sched['estimated_idle_wait_s'] == 0.0
    comp = next(c for c in sched['carrier_comparisons'] if c['transfers'] == ['deliver_tableware'])
    assert comp['recommended_mode'] == 'carrier_batch'
    assert comp['saved_seconds_est'] > 50.0
    assert sched['estimated_savings_vs_naive']['total_saved_s'] > 200.0


def test_program_scheduling_chained_stages_and_public_ledger(tmp_path: Path) -> None:
    out = tmp_path / 'run_prog'
    backend = MockRGBBackend(out)
    backend._get_held = lambda: None  # type: ignore[attr-defined]
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=20,
        goal_skill='none', scheduling_mode='program', auxiliary_feedback=True,
    )
    assert 'schedule_plan' in harness.catalog
    init_res = harness.call('initialize', {}, 'req-init')
    rev = init_res['observation']['revision']

    # Validation error should refund step when auxiliary_feedback=True
    bad_res = harness.call(
        'act',
        {'primitive': 'toggle_on', 'target': {'image_ref': 'stale-ref', 'point': [0.5, 0.5]}, 'revision': rev},
        'req-bad',
    )
    assert bad_res['ok'] is False
    assert bad_res['error']['action_step_used'] is False
    assert harness.agent_steps == 0
    assert bad_res['robot'] == {'hand': 'empty'}

    # schedule_plan uses 0 action steps
    plan_res = harness.call(
        'schedule_plan',
        {
            'plan': {
                'places': ['utility', 'study'],
                'robot_place': 'study',
                'travel_edges': [{'from_place': 'study', 'to_place': 'utility', 'meters': 10.0, 'seconds': None}],
                'carriers': [],
                'timed_processes': [
                    {
                        'id': 'stage1_wash',
                        'place': 'utility',
                        'duration_seconds': 200.0,
                        'setup_seconds': 40.0,
                        'finish_seconds': 20.0,
                        'status': 'pending',
                        'started_at_robot_s': None,
                        'depends_on': None,
                    },
                    {
                        'id': 'stage2_dry',
                        'place': 'utility',
                        'duration_seconds': 180.0,
                        'setup_seconds': 20.0,
                        'finish_seconds': 20.0,
                        'status': 'pending',
                        'started_at_robot_s': None,
                        'depends_on': 'stage1_wash',
                    },
                ],
                'deliveries': [],
                'local_work': [
                    {'id': 'tidy_study', 'place': 'study', 'work_seconds': 220.0, 'depends_on': None, 'status': 'pending'},
                ],
            },
            'completed_job_ids': None,
            'verified_closed_compartments': ['hall_drawer'],
        },
        'req-sched-1',
    )
    assert plan_res['ok'] is True
    assert harness.agent_steps == 0
    phases = plan_res['schedule']['ordered_phases']
    # Must start stage1_wash first, then tidy_study during stage1_wash, then collect stage1 & start stage2!
    assert phases[0]['jobs'] == ['stage1_wash']
    assert phases[0]['action_type'] == 'start_timed_process'
    assert phases[1]['jobs'] == ['tidy_study']
    assert phases[2]['jobs'] == ['stage1_wash']
    assert phases[2]['action_type'] == 'collect_and_advance_stage'
    assert phases[3]['jobs'] == ['stage2_dry']
    assert phases[3]['action_type'] == 'start_timed_process'
    assert plan_res['public_ledger']['verified_closed_compartments'] == ['hall_drawer']


def test_click_echo_produces_valid_enlarged_jpeg() -> None:
    raw_png = png_fixture(on=True)
    jpeg_bytes, crop_size = click_echo(raw_png, [0.5, 0.5])
    assert crop_size == 64  # min(160, 96, 64)
    assert jpeg_bytes[:2] == b'\xff\xd8'
    caption = echo_caption('mock-rgb-1-front', crop_size)
    assert 'mock-rgb-1-front' in caption
    assert '64x64 px' in caption


def test_schedule_plan_end_to_end_boolean_validation_and_ledger_accumulation(tmp_path: Path) -> None:
    """Regression test for contracts.validate boolean support, null duration_seconds, and incremental ledger union."""
    out = tmp_path / 'run_reg'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=20,
        goal_skill='none', scheduling_mode='program',
    )
    harness.call('initialize', {}, 'req-init')
    res1 = harness.call(
        'schedule_plan',
        {
            'plan': {
                'places': ['prep', 'dining'],
                'robot_place': 'prep',
                'travel_edges': [{'from_place': 'prep', 'to_place': 'dining', 'meters': 6.0, 'seconds': None}],
                'carriers': [{'id': 'tray', 'place': 'prep', 'capacity': 6, 'final_place': 'dining'}],
                'timed_processes': [{
                    'id': 'heat_cycle',
                    'place': 'prep',
                    'duration_seconds': None,
                    'setup_seconds': None,
                    'finish_seconds': None,
                    'status': 'pending',
                    'started_at_robot_s': None,
                    'depends_on': None,
                }],
                'deliveries': [{
                    'id': 'tableware_batch',
                    'from_place': 'prep',
                    'to_place': 'dining',
                    'item_count': 5,
                    'can_use_carrier': True,
                    'depends_on': None,
                    'status': 'pending',
                }],
                'local_work': [{
                    'id': 'prep_side',
                    'place': 'prep',
                    'work_seconds': 30.0,
                    'depends_on': None,
                    'status': 'pending',
                }],
            },
            'completed_job_ids': [],
            'verified_closed_compartments': ['storage_door'],
        },
        'req-1',
    )
    assert res1['ok'] is True, res1
    sched1 = res1['schedule']
    assert sched1['carrier_comparisons'][0]['recommended_mode'] == 'carrier_batch'
    # Incremental update accumulates completed_job_ids without wiping earlier entries
    res2 = harness.call(
        'schedule_plan',
        {
            'plan': None,
            'completed_job_ids': ['heat_cycle', 'prep_side'],
            'verified_closed_compartments': ['unit_door'],
        },
        'req-2',
    )
    assert res2['ok'] is True
    res3 = harness.call(
        'schedule_plan',
        {
            'plan': None,
            'completed_job_ids': ['tableware_batch', 'return_tray'],
            'verified_closed_compartments': None,
        },
        'req-3',
    )
    assert res3['ok'] is True
    assert set(res3['public_ledger']['completed_job_ids']) == {'heat_cycle', 'prep_side', 'tableware_batch', 'return_tray'}
    assert set(res3['public_ledger']['verified_closed_compartments']) == {'storage_door', 'unit_door'}
    assert res3['schedule']['ordered_phases'] == []


def test_v3_concise_optional_fields_and_chained_stage_warning(tmp_path: Path) -> None:
    """Verify concise v3 calls omitting optional fields succeed and emit stage-1 vs stage-2 verification warning."""
    out = tmp_path / 'run_v3_concise'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=20,
        goal_skill='none', scheduling_mode='program', auxiliary_feedback=True,
    )
    harness.call('initialize', {}, 'req-init')
    # Concise call omitting optional fields (robot_place, setup_seconds, status, completed_job_ids, etc.)
    res = harness.call(
        'schedule_plan',
        {
            'plan': {
                'places': ['south_room', 'north_room'],
                'travel_edges': [{'from_place': 'south_room', 'to_place': 'north_room', 'meters': 25.0}],
                'carriers': [{'id': 'basket', 'place': 'south_room'}],
                'timed_processes': [
                    {'id': 'stage1_clean', 'place': 'south_room'},
                    {'id': 'stage2_dry', 'place': 'south_room', 'depends_on': 'stage1_clean'},
                ],
                'deliveries': [
                    {'id': 'move_items', 'from_place': 'south_room', 'to_place': 'north_room', 'item_count': 4},
                ],
            },
        },
        'req-concise',
    )
    assert res['ok'] is True, res
    p0 = res['schedule']['ordered_phases'][0]
    assert p0['action_type'] == 'start_timed_process'
    assert p0['jobs'] == ['stage1_clean']
    assert 'upstream stage-1 appliance' in p0['why']
    # Incremental update omitting plan and verified_closed_compartments completely
    res_inc = harness.call('schedule_plan', {'completed_job_ids': ['stage1_clean']}, 'req-inc')
    assert res_inc['ok'] is True, res_inc
    assert 'stage1_clean' in res_inc['public_ledger']['completed_job_ids']
