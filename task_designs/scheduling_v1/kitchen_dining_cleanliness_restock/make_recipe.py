#!/usr/bin/env python3
"""Generate recipe.json for custom task `kitchen_dining_cleanliness_restock` (Benchmark side).

Task: 餐厨洁净度单调清洁与储物闭柜盲盒盘点补齐
Base scene: `preparing_lunch_box` instance 301, seed 0 (`house_single_floor`).
Device timer waiting: 0.0s (pure spatial, dependency, and tour scheduling).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure local src is in path for contract validation
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / 'src'))

from manipulation_agent.references.full_workflow_reference import validate_workflow_plan  # noqa: E402
from manipulation_agent.tasks.custom_tasks import validate_spec  # noqa: E402

CLEAN_DESK_TEMPLATE_SHA256 = '6096a25139486cb627b5faa7b43c8ed20001d4ba8974bcd5b5e09182991df03f'


def build_recipe() -> dict:
    req_clean_surfaces_text = (
        'Clean all six designated surfaces across the dining area and kitchen using the cleaning cloth: '
        'dining table and kitchen prep counter (Tier 1 light dust), kitchen island bar counter and interior '
        'shelf of the dining room storage cabinet (Tier 2 moderate dust), and cooking stove top and kitchen '
        'waste surround counter (Tier 3 heavy stains). Hygiene rule: wiping a surface sets the cloth stain '
        'level to at least that surface tier, and wiping a lower-tier surface while the cloth carries a '
        'higher-tier stain causes cross-contamination unless the cloth is rinsed (soaked) in the kitchen sink '
        'first to reset it to clean. At completion, leave the rinsed cleaning cloth resting on the kitchen prep counter.'
    )
    req_restock_cabinet_text = (
        'Open and inspect the closed storage cabinet in the dining room, and restock it using supplies from '
        'the pantry supply shelf in the living-room hallway until the cabinet holds two bottles of apple juice, '
        'two bottles of tea, and two boxes of crackers: the interior shelf of the cabinet must be wiped clean '
        'before any new supplies are placed inside, and both doors of the cabinet must be closed when finished.'
    )
    req_carrier_and_hand_text = (
        'Leave the wooden utility tray resting on the pantry supply shelf, the dining table, or the kitchen '
        'bar counter, and finish the task with no object held in hand.'
    )

    spec = {
        'schema_version': 3,
        'id': 'kitchen_dining_cleanliness_restock',
        'instance_id': 'kitchen_dining_cleanliness_restock_seed0_r1',
        'status': 'complete_recipe_physics_pending',
        'benchmark_ready': False,
        'official_submission_eligible': False,
        'base': {
            'task': 'preparing_lunch_box',
            'instance': 301,
            'seed': 0,
        },
        'roles': {
            'sink': 'drop_in_sink_awvzkn_0',
            'rag': 'cdkr_rag',
            'tray': 'cdkr_tray',
            'dining_table': 'breakfast_table_rhjoby_0',
            'prep_counter': 'countertop_kelzer_0',
            'bar_counter': 'bar_udatjt_0',
            'target_cabinet': 'cdkr_cabinet',
            'cabinet_shelf': 'cdkr_cabinet_shelf',
            'stove_top': 'countertop_kelker_0',
            'waste_surround': 'cdkr_waste_surround',
            'pantry_shelf': 'cdkr_pantry_shelf',
            'juice_1': 'cdkr_juice_1',
            'juice_2': 'cdkr_juice_2',
            'tea_1': 'cdkr_tea_1',
            'tea_2': 'cdkr_tea_2',
            'snack_1': 'cdkr_snack_1',
            'snack_2': 'cdkr_snack_2',
            'hallway_door': 'door_tonlzz_0',
            # Park unused base task items out of the way
            'unused_cookie': 'chocolate_chip_cookie_207',
            'unused_sandwich': 'club_sandwich_209',
            'unused_box': 'packing_box_210',
            'spare_half_apple_a': 'half_apple_212',
            'spare_half_apple_b': 'half_apple_213',
            'unused_board': 'chopping_board_211',
            'unused_tea': 'bottle_of_tea_208',
        },
        'scene_exclusions': [
            'water_glass_bbpraa_0',
            'bottom_cabinet_fancyy_0',
        ],
        'construction_asset_scales': [],
        'asset_imports': [
            {
                'name': 'cdkr_cabinet',
                'source_task': 'clean_up_your_desk',
                'source_object': 'bottom_cabinet_fancyy_0',
                'source_sha256': CLEAN_DESK_TEMPLATE_SHA256,
                'position': [5.20, 4.20, 0.85],
                'orientation': [0.0, 0.0, -0.70710678, 0.70710678],
                'fixed_base': True,
                'scale': [0.76, 0.70, 0.88],
            },
        ],
        'native_assets': [
            {
                'name': 'cdkr_rag',
                'category': 'rag',
                'model': 'oocmed',
                'usd_sha256': '732677fea4ca1e96c8b71f843249e2b5740287542aae48245871d7a6daeb7068',
                'prim_type': 0,
                'position': [95.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
            {
                'name': 'cdkr_tray',
                'category': 'tray',
                'model': 'coqeme',
                'usd_sha256': 'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd',
                'prim_type': 0,
                'position': [98.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [3.38, 2.16, 1.5],
            },
            {
                'name': 'cdkr_cabinet_shelf',
                'category': 'shelf',
                'model': 'sapyzl',
                'usd_sha256': '9091e0249fa03b4b95904a3c4a7e1b355358f8a6ed2207f1d43406caffd7c6ed',
                'prim_type': 0,
                'fixed_base': True,
                'position': [5.20, 4.20, 0.45],
                'orientation': [0.0, 0.0, -0.70710678, 0.70710678],
                'scale': [0.70, 0.45, 0.05],
            },
            {
                'name': 'cdkr_waste_surround',
                'category': 'countertop',
                'model': 'fjkase',
                'usd_sha256': 'cc3fc937ea174ff463560d44911a3c59f944402a82465f7751bf293c64fdc9b1',
                'prim_type': 0,
                'fixed_base': True,
                'position': [5.00, -0.50, 0.88],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.85, 0.85, 0.88],
            },
            {
                'name': 'cdkr_pantry_shelf',
                'category': 'shelf',
                'model': 'sapyzl',
                'usd_sha256': '9091e0249fa03b4b95904a3c4a7e1b355358f8a6ed2207f1d43406caffd7c6ed',
                'prim_type': 0,
                'fixed_base': True,
                'position': [14.20, 3.52, 0.88],
                'orientation': [0.0, 0.0, -0.70710678, 0.70710678],
                'scale': [3.80, 1.45, 0.88],
            },
            {
                'name': 'cdkr_juice_1',
                'category': 'bottle_of_apple_juice',
                'model': 'xvrbdy',
                'usd_sha256': '6f8e42e987e6f8284c77a60bf5d550b2053c6654ae6be38d2e14b5f46031bf31',
                'prim_type': 0,
                'position': [101.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
            {
                'name': 'cdkr_juice_2',
                'category': 'bottle_of_apple_juice',
                'model': 'xvrbdy',
                'usd_sha256': '6f8e42e987e6f8284c77a60bf5d550b2053c6654ae6be38d2e14b5f46031bf31',
                'prim_type': 0,
                'position': [104.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
            {
                'name': 'cdkr_tea_1',
                'category': 'bottle_of_tea',
                'model': 'iladfg',
                'usd_sha256': 'e49f1856c30e7d7700fd8bd3a1765edc8ede3ce38c3965f284beeef840df3acf',
                'prim_type': 0,
                'position': [107.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
            {
                'name': 'cdkr_tea_2',
                'category': 'bottle_of_tea',
                'model': 'yatmrs',
                'usd_sha256': '0ae3c478db32e8bf78976c5799e3db29ba935548d41eb41b3ca1685a164baf05',
                'prim_type': 0,
                'position': [110.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
            {
                'name': 'cdkr_snack_1',
                'category': 'box_of_crackers',
                'model': 'cmdigf',
                'usd_sha256': 'd1fbcb8229c6120add9c2dec27ab55c9cb17c6b8bf515a8415c9f6123163ff00',
                'prim_type': 0,
                'position': [113.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
            {
                'name': 'cdkr_snack_2',
                'category': 'box_of_crackers',
                'model': 'cmdigf',
                'usd_sha256': 'd1fbcb8229c6120add9c2dec27ab55c9cb17c6b8bf515a8415c9f6123163ff00',
                'prim_type': 0,
                'position': [116.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.0],
            },
        ],
        'fixtures': [],
        'tool_cleanliness': {
            'tool': 'rag',
            'water_sources': ['sink'],
            'surface_levels': {
                'dining_table': 1,
                'prep_counter': 1,
                'bar_counter': 2,
                'cabinet_shelf': 2,
                'stove_top': 3,
                'waste_surround': 3,
            },
        },
        'initial_layout': [
            # 1. Door state: ensure walkway between dining room and kitchen is fully open
            {'object': 'hallway_door', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            # 2. Storage cabinet initial state: closed blind box
            {'object': 'target_cabinet', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            # 3. Cleaning tool & carrier starting stations
            {
                'object': 'rag',
                'target': 'prep_counter',
                'place_on_support': True,
                'xy': [6.70, -1.94],
            },
            {
                'object': 'tray',
                'target': 'pantry_shelf',
                'place_on_support': True,
                'xy': [14.75, 3.52],
                'support_probe_height': 1.10,
            },
            # 4. Blind box initial stock inside target_cabinet (2 teas) resting on cabinet_shelf
            {
                'object': 'tea_1',
                'target': 'cabinet_shelf',
                'place_on_support': True,
                'xy': [5.11, 4.17],
                'support_probe_height': 0.60,
                'initial_relation': 'Inside',
            },
            {
                'object': 'tea_2',
                'target': 'cabinet_shelf',
                'place_on_support': True,
                'xy': [5.11, 4.23],
                'support_probe_height': 0.60,
                'initial_relation': 'Inside',
            },
            # 5. Restock inventory available on pantry_shelf (2 juices + 2 snacks)
            {
                'object': 'juice_1',
                'target': 'pantry_shelf',
                'place_on_support': True,
                'xy': [13.35, 3.52],
                'support_probe_height': 1.10,
            },
            {
                'object': 'juice_2',
                'target': 'pantry_shelf',
                'place_on_support': True,
                'xy': [13.70, 3.52],
                'support_probe_height': 1.10,
            },
            {
                'object': 'snack_1',
                'target': 'pantry_shelf',
                'place_on_support': True,
                'xy': [14.05, 3.52],
                'support_probe_height': 1.10,
            },
            {
                'object': 'snack_2',
                'target': 'pantry_shelf',
                'place_on_support': True,
                'xy': [14.40, 3.52],
                'support_probe_height': 1.10,
            },
            # 6. Initial surface cleanliness states: 4 dusty surfaces (L1/L2) + 2 stained surfaces (L3)
            {
                'object': 'dining_table',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'prep_counter',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'bar_counter',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'cabinet_shelf',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'stove_top',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'waste_surround',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
        ],
        'initial_assertions': {
            'dining_table_dust': 'dining_table_dust',
            'prep_counter_dust': 'prep_counter_dust',
            'bar_counter_dust': 'bar_counter_dust',
            'cabinet_dust': 'cabinet_dust',
            'stove_top_stain': 'stove_top_stain',
            'waste_surround_stain': 'waste_surround_stain',
            'cabinet_closed': {'not': 'cabinet_open'},
            'initial_tea1_inside': 'tea1_inside_cabinet',
            'initial_tea2_inside': 'tea2_inside_cabinet',
            'initial_juice1_ontop': 'juice1_ontop_pantry',
            'initial_juice2_ontop': 'juice2_ontop_pantry',
            'initial_snack1_ontop': 'snack1_ontop_pantry',
            'initial_snack2_ontop': 'snack2_ontop_pantry',
            'initial_rag_ontop': 'rag_ontop_prep',
            'initial_tray_ontop': 'tray_ontop_pantry',
            'no_initial_violation': {'not': 'cross_contamination_violation'},
        },
        'public_instruction': (
            'Clean all six designated dining and kitchen surfaces with the cleaning cloth without causing '
            'cross-contamination across surface stain tiers (rinsing the cloth in the kitchen sink resets its '
            'stain level), inspect and restock the closed dining room storage cabinet from the pantry supply '
            'shelf until it holds two bottles of apple juice, two bottles of tea, and two boxes of crackers '
            '(wiping the interior cabinet shelf clean before placing new items inside), close the cabinet doors, '
            'leave the wooden utility tray on a shelf, table, or counter, and finish empty-handed.'
        ),
        'public_context': [
            req_clean_surfaces_text,
            req_restock_cabinet_text,
            req_carrier_and_hand_text,
        ],
        'requirements': [
            {
                'id': 'clean_dining_and_kitchen_surfaces',
                'text': req_clean_surfaces_text,
                'terminal': ['surfaces_clean', 'rag_returned'],
                'constraints': ['no_cross_contamination'],
            },
            {
                'id': 'restock_dining_cabinet',
                'text': req_restock_cabinet_text,
                'terminal': ['cabinet_stocked', 'cabinet_closed'],
                'constraints': [
                    'cabinet_clean_before_stock_juice1',
                    'cabinet_clean_before_stock_juice2',
                    'cabinet_clean_before_stock_snack1',
                    'cabinet_clean_before_stock_snack2',
                ],
            },
            {
                'id': 'carrier_parking_and_empty_hand',
                'text': req_carrier_and_hand_text,
                'terminal': ['tray_parked', 'hand_empty'],
            },
        ],
        'evaluation': {
            'predicates': {
                'dining_table_dust': {'object': 'dining_table', 'state': 'Covered', 'system': 'dust'},
                'prep_counter_dust': {'object': 'prep_counter', 'state': 'Covered', 'system': 'dust'},
                'bar_counter_dust': {'object': 'bar_counter', 'state': 'Covered', 'system': 'dust'},
                'cabinet_dust': {'object': 'cabinet_shelf', 'state': 'Covered', 'system': 'dust'},
                'stove_top_stain': {'object': 'stove_top', 'state': 'Covered', 'system': 'dust'},
                'waste_surround_stain': {'object': 'waste_surround', 'state': 'Covered', 'system': 'dust'},
                'cabinet_open': {'object': 'target_cabinet', 'state': 'Open'},
                'juice1_inside_cabinet': {'object': 'juice_1', 'state': 'Inside', 'target': 'target_cabinet'},
                'juice2_inside_cabinet': {'object': 'juice_2', 'state': 'Inside', 'target': 'target_cabinet'},
                'tea1_inside_cabinet': {'object': 'tea_1', 'state': 'Inside', 'target': 'target_cabinet'},
                'tea2_inside_cabinet': {'object': 'tea_2', 'state': 'Inside', 'target': 'target_cabinet'},
                'snack1_inside_cabinet': {'object': 'snack_1', 'state': 'Inside', 'target': 'target_cabinet'},
                'snack2_inside_cabinet': {'object': 'snack_2', 'state': 'Inside', 'target': 'target_cabinet'},
                'juice1_ontop_tray': {'object': 'juice_1', 'state': 'OnTop', 'target': 'tray'},
                'juice2_ontop_tray': {'object': 'juice_2', 'state': 'OnTop', 'target': 'tray'},
                'snack1_ontop_tray': {'object': 'snack_1', 'state': 'OnTop', 'target': 'tray'},
                'snack2_ontop_tray': {'object': 'snack_2', 'state': 'OnTop', 'target': 'tray'},
                'rag_ontop_prep': {'object': 'rag', 'state': 'OnTop', 'target': 'prep_counter'},
                'rag_ontop_bar': {'object': 'rag', 'state': 'OnTop', 'target': 'bar_counter'},
                'rag_ontop_sink': {'object': 'rag', 'state': 'OnTop', 'target': 'sink'},
                'rag_inside_sink': {'object': 'rag', 'state': 'Inside', 'target': 'sink'},
                'tray_ontop_table': {'object': 'tray', 'state': 'OnTop', 'target': 'dining_table'},
                'tray_ontop_bar': {'object': 'tray', 'state': 'OnTop', 'target': 'bar_counter'},
                'tray_ontop_prep': {'object': 'tray', 'state': 'OnTop', 'target': 'prep_counter'},
                'tray_ontop_pantry': {'object': 'tray', 'state': 'OnTop', 'target': 'pantry_shelf'},
                'hand_empty': {'object': 'rag', 'state': 'HandEmpty'},
                'cross_contamination_violation': {'object': 'rag', 'state': 'CrossContaminationViolation'},
                # Initial verification predicates
                'juice1_ontop_pantry': {'object': 'juice_1', 'state': 'OnTop', 'target': 'pantry_shelf'},
                'juice2_ontop_pantry': {'object': 'juice_2', 'state': 'OnTop', 'target': 'pantry_shelf'},
                'snack1_ontop_pantry': {'object': 'snack_1', 'state': 'OnTop', 'target': 'pantry_shelf'},
                'snack2_ontop_pantry': {'object': 'snack_2', 'state': 'OnTop', 'target': 'pantry_shelf'},
            },
            'terminal': {
                'surfaces_clean': {
                    'all': [
                        {'not': 'dining_table_dust'},
                        {'not': 'prep_counter_dust'},
                        {'not': 'bar_counter_dust'},
                        {'not': 'cabinet_dust'},
                        {'not': 'stove_top_stain'},
                        {'not': 'waste_surround_stain'},
                    ],
                },
                'rag_returned': {
                    'any': [
                        'rag_ontop_prep',
                        'rag_ontop_bar',
                        'rag_ontop_sink',
                        'rag_inside_sink',
                    ],
                },
                'cabinet_stocked': {
                    'all': [
                        'juice1_inside_cabinet',
                        'juice2_inside_cabinet',
                        'tea1_inside_cabinet',
                        'tea2_inside_cabinet',
                        'snack1_inside_cabinet',
                        'snack2_inside_cabinet',
                    ],
                },
                'cabinet_closed': {'not': 'cabinet_open'},
                'tray_parked': {
                    'any': [
                        'tray_ontop_table',
                        'tray_ontop_bar',
                        'tray_ontop_prep',
                        'tray_ontop_pantry',
                    ],
                },
                'hand_empty': 'hand_empty',
            },
            'milestones': {},
            'state_guards': [
                {
                    'id': 'no_cross_contamination',
                    'until': None,
                    'require': {'not': 'cross_contamination_violation'},
                },
            ],
            'transition_guards': [
                {
                    'id': 'cabinet_clean_before_stock_juice1',
                    'predicate': 'juice1_inside_cabinet',
                    'from_value': False,
                    'to_value': True,
                    'require': {'not': 'cabinet_dust'},
                },
                {
                    'id': 'cabinet_clean_before_stock_juice2',
                    'predicate': 'juice2_inside_cabinet',
                    'from_value': False,
                    'to_value': True,
                    'require': {'not': 'cabinet_dust'},
                },
                {
                    'id': 'cabinet_clean_before_stock_snack1',
                    'predicate': 'snack1_inside_cabinet',
                    'from_value': False,
                    'to_value': True,
                    'require': {'not': 'cabinet_dust'},
                },
                {
                    'id': 'cabinet_clean_before_stock_snack2',
                    'predicate': 'snack2_inside_cabinet',
                    'from_value': False,
                    'to_value': True,
                    'require': {'not': 'cabinet_dust'},
                },
            ],
            'carry_guards': [],
            'motion_guards': [],
        },
        'acceptance_cases': {
            'reference_orders': [
                'optimal_carrier_tour',
                'habit_sequential',
            ],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 260,
            'physical_negative_cases': [
                'omit_sink_soak_contamination',
                'omit_cabinet_wiping',
                'omit_snack_restock',
                'omit_door_closing',
            ],
        },
        'reference_plan': {
            'kind': 'full_workflow',
            'allow_strategy_variants': True,
            'phases': {
                # --- OPTIMAL CARRIER TOUR PHASES ---
                'phase_prep_cabinet': {
                    'actions': [
                        {'primitive': 'open', 'role': 'target_cabinet'},
                    ],
                    'after': [],
                },
                'phase_wipe_clean_surfaces': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'rag'},
                        {'primitive': 'wipe', 'role': 'dining_table'},
                        {'primitive': 'wipe', 'role': 'bar_counter'},
                    ],
                    'after': [],
                },
                'phase_wipe_cabinet_shelf': {
                    'actions': [
                        {'primitive': 'wipe', 'role': 'target_cabinet'},
                    ],
                    'after': ['phase_prep_cabinet', 'phase_wipe_clean_surfaces'],
                },
                'phase_wipe_heavy_stains': {
                    'actions': [
                        {'primitive': 'wipe', 'role': 'waste_surround'},
                        {'primitive': 'wipe', 'role': 'stove_top'},
                    ],
                    'after': ['phase_wipe_cabinet_shelf'],
                },
                'phase_decontaminate_rag': {
                    'actions': [
                        {'primitive': 'soak', 'role': 'sink'},
                    ],
                    'after': ['phase_wipe_heavy_stains'],
                },
                'phase_final_clean_prep_and_park_rag': {
                    'actions': [
                        {'primitive': 'wipe', 'role': 'prep_counter'},
                        {'primitive': 'place_on_top', 'role': 'prep_counter', 'xy': [6.70, -1.94]},
                    ],
                    'after': ['phase_decontaminate_rag'],
                },
                'phase_batch_load_carrier': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'snack_2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.70, 0.68]},
                        {'primitive': 'grasp', 'role': 'snack_1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.70, 0.32]},
                        {'primitive': 'grasp', 'role': 'juice_2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.30, 0.68]},
                        {'primitive': 'grasp', 'role': 'juice_1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.30, 0.32]},
                        {'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'dining_table', 'uv': [0.50, 0.50]},
                    ],
                    'after': ['phase_final_clean_prep_and_park_rag'],
                },
                'phase_restock_juice_into_cabinet': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'juice_1', 'approach_role': 'tray'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                        {'primitive': 'grasp', 'role': 'juice_2', 'approach_role': 'tray'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                    ],
                    'after': ['phase_batch_load_carrier', 'phase_wipe_cabinet_shelf'],
                },
                'phase_restock_snacks_into_cabinet': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'snack_1', 'approach_role': 'tray'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                        {'primitive': 'grasp', 'role': 'snack_2', 'approach_role': 'tray'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                    ],
                    'after': ['phase_batch_load_carrier', 'phase_wipe_cabinet_shelf'],
                },
                'phase_close_cabinet_door': {
                    'actions': [
                        {'primitive': 'close', 'role': 'target_cabinet'},
                    ],
                    'after': ['phase_restock_juice_into_cabinet', 'phase_restock_snacks_into_cabinet'],
                },

                # --- HABIT SEQUENTIAL PHASES (Out-of-order wiping + unbatched restock) ---
                'habit_prep_cabinet': {
                    'actions': [
                        {'primitive': 'open', 'role': 'target_cabinet'},
                    ],
                    'after': [],
                },
                'habit_wipe_dirty_kitchen_first': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'rag'},
                        {'primitive': 'wipe', 'role': 'stove_top'},
                    ],
                    'after': [],
                },
                'habit_decontaminate_trip_1': {
                    'actions': [
                        {'primitive': 'soak', 'role': 'sink'},
                    ],
                    'after': ['habit_wipe_dirty_kitchen_first'],
                },
                'habit_wipe_dining_and_waste': {
                    'actions': [
                        {'primitive': 'wipe', 'role': 'dining_table'},
                        {'primitive': 'wipe', 'role': 'waste_surround'},
                    ],
                    'after': ['habit_decontaminate_trip_1'],
                },
                'habit_decontaminate_trip_2': {
                    'actions': [
                        {'primitive': 'soak', 'role': 'sink'},
                    ],
                    'after': ['habit_wipe_dining_and_waste'],
                },
                'habit_wipe_bar_and_cabinet': {
                    'actions': [
                        {'primitive': 'wipe', 'role': 'bar_counter'},
                        {'primitive': 'wipe', 'role': 'cabinet_shelf'},
                    ],
                    'after': ['habit_decontaminate_trip_2', 'habit_prep_cabinet'],
                },
                'habit_decontaminate_trip_3': {
                    'actions': [
                        {'primitive': 'soak', 'role': 'sink'},
                    ],
                    'after': ['habit_wipe_bar_and_cabinet'],
                },
                'habit_wipe_prep_and_park_rag': {
                    'actions': [
                        {'primitive': 'wipe', 'role': 'prep_counter'},
                        {'primitive': 'place_on_top', 'role': 'prep_counter', 'xy': [6.70, -1.94]},
                    ],
                    'after': ['habit_decontaminate_trip_3'],
                },
                'habit_single_restock_juice1': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'juice_1'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                    ],
                    'after': ['habit_wipe_bar_and_cabinet'],
                },
                'habit_single_restock_juice2': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'juice_2'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                    ],
                    'after': ['habit_wipe_bar_and_cabinet'],
                },
                'habit_single_restock_snack1': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'snack_1'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                    ],
                    'after': ['habit_wipe_bar_and_cabinet'],
                },
                'habit_single_restock_snack2': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'snack_2'},
                        {'primitive': 'place_inside', 'role': 'target_cabinet'},
                    ],
                    'after': ['habit_wipe_bar_and_cabinet'],
                },
                'habit_close_cabinet_door': {
                    'actions': [
                        {'primitive': 'close', 'role': 'target_cabinet'},
                    ],
                    'after': [
                        'habit_single_restock_juice1',
                        'habit_single_restock_juice2',
                        'habit_single_restock_snack1',
                        'habit_single_restock_snack2',
                    ],
                },
            },
            'orders': {
                'optimal_carrier_tour': [
                    'phase_prep_cabinet',
                    'phase_wipe_clean_surfaces',
                    'phase_wipe_cabinet_shelf',
                    'phase_wipe_heavy_stains',
                    'phase_decontaminate_rag',
                    'phase_final_clean_prep_and_park_rag',
                    'phase_batch_load_carrier',
                    'phase_restock_juice_into_cabinet',
                    'phase_restock_snacks_into_cabinet',
                    'phase_close_cabinet_door',
                ],
                'habit_sequential': [
                    'habit_prep_cabinet',
                    'habit_wipe_dirty_kitchen_first',
                    'habit_decontaminate_trip_1',
                    'habit_wipe_dining_and_waste',
                    'habit_decontaminate_trip_2',
                    'habit_wipe_bar_and_cabinet',
                    'habit_decontaminate_trip_3',
                    'habit_wipe_prep_and_park_rag',
                    'habit_single_restock_juice1',
                    'habit_single_restock_juice2',
                    'habit_single_restock_snack1',
                    'habit_single_restock_snack2',
                    'habit_close_cabinet_door',
                ],
            },
            'negative_omissions': {
                'omit_sink_soak_contamination': 'phase_decontaminate_rag',
                'omit_cabinet_wiping': 'phase_wipe_cabinet_shelf',
                'omit_snack_restock': 'phase_restock_snacks_into_cabinet',
                'omit_door_closing': 'phase_close_cabinet_door',
            },
        },
    }

    validate_spec(spec, require_frozen=False)
    validate_workflow_plan(spec)
    return spec


def main() -> None:
    recipe = build_recipe()
    out_path = HERE / 'recipe.json'
    out_path.write_text(json.dumps(recipe, indent=1) + '\n')
    print(f'STATUS: passed; successfully generated {out_path} ({len(json.dumps(recipe))} bytes)')


if __name__ == '__main__':
    main()
