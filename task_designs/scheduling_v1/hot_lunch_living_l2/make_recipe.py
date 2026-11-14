#!/usr/bin/env python3
"""Generate recipe.json for custom task `hot_lunch_living_l2` (Benchmark side).

Task: 双人热午餐 L2（跨厅载具分批 + 并行加热）
Base scene: `preparing_lunch_box` instance 301, seed 0 (`house_single_floor`).

Design evidence & adherence to the 5 benchmark task criteria (AGENTS.md):
  1. Feasible on current executor:
     - Reuses proven enlarged plates (`25.6 x 25.6 cm`, scale `[1.5, 1.5, 1.0]`), widened forks
       (`17.2 x 6.6 cm`, scale `[0.85, 2.2, 1.5]`), and widened knife (`7.8 cm` wide, scale `[1.0, 2.5, 2.0]`).
     - Adds a square enlarged serving tray (`tray/coqeme`, scale `[3.38, 2.16, 1.5]` -> `48 x 48 x 2.7 cm`)
       on the kitchen island (`bar_udatjt_0`), orientation-invariant and <6 cm thin-support compatible.
     - Delivers to the open-plan living-room coffee table (`coffee_table_rlsebe_0`, `98 x 98 cm`,
       wide-open north & east approaches, ~9.5 m one-way from the microwave, already validated in `hotdrink_v4`).
  2. Zero ambiguity or perception traps:
     - Excludes duplicate `water_glass_bbpraa_0`.
     - Tucks unused base-task objects (`half_apple_212/213`, `packing_box_210`, `chocolate_chip_cookie_207`,
       `club_sandwich_209`) onto the 2.09 m roof of `bottom_cabinet_fancyy_0`, out of camera view.
     - Removes artificial `fv2_stage` block and random dust particles.
     - Places tableware/tray in plain view on the kitchen island (`bar_udatjt_0`, z=1.052) and cutting items
       in plain view on the window counter (`countertop_kelzer_0`, z=0.888, no overhead cabinets).
  3. Planning complexity (not motor fussiness):
     - Microwave batch heating takes 180 s of `robot_clock` (`timed_processes`), advancing during travel
       and manipulation elsewhere as well as `wait`.
     - 11 items (`2 plates + 2 cooked hotdogs + 2 apple halves + 2 cups + 2 forks + 1 tea bottle`) cross ~9.5 m
       from kitchen to living room; optional 46x46 cm tray batches the 5 cold tableware/drink items into 1 trip.
     - Public instruction states only the initial layout and final goal (items on coffee table or on the tray
       resting on the coffee table; knife & board stored in pantry cabinet; doors closed & microwave off),
       never prescribing operation order or carrier usage.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / 'src'))
from manipulation_agent.tasks.custom_tasks import validate_spec  # noqa: E402


def served_on_table_or_tray(item_on_table: str, item_on_tray: str) -> dict:
    return {
        'any': [
            item_on_table,
            {'all': [item_on_tray, 'tray_ontop_table']},
        ]
    }


def build_recipe() -> dict:
    req1_text = (
        'Cook both hotdogs from the refrigerator in the microwave without burning them, cut the whole apple '
        'into two actual halves, and serve two plates at the living-room coffee table with one cooked hotdog '
        'and one apple half on each plate (each plate placed directly on the coffee table or on the serving '
        'tray resting on the coffee table). The refrigerator is the tall white appliance between the sink '
        'counter and the black built-in oven; the microwave is on the west preparation counter beside the '
        'stove; the cutting board, whole apple, and knife start on the counter between the sink and the '
        'refrigerator; the two plates start on the kitchen island. The living-room coffee table is the low '
        'square table in front of the sofas east of the kitchen.'
    )
    req2_text = (
        'Serve the two cups, two forks, and tea bottle at the living-room coffee table, placed either '
        'directly on the coffee table or on the serving tray resting on the coffee table. The two cups, '
        'two forks, tea bottle, and serving tray start on the kitchen island.'
    )
    req3_text = (
        'Store the knife and cutting board inside the tall pantry cabinet to the right of the built-in oven, '
        'close the pantry cabinet, refrigerator, and microwave, and leave the microwave switched off.'
    )
    req4_text = 'Finish with no object held.'

    spec = {
        'schema_version': 3,
        'id': 'hot_lunch_living_l2',
        'instance_id': 'hot_lunch_living_l2_seed0_r1',
        'status': 'complete_recipe_physics_pending',
        'benchmark_ready': False,
        'official_submission_eligible': False,
        'base': {
            'task': 'preparing_lunch_box',
            'instance': 301,
            'seed': 0,
        },
        'roles': {
            'prep': 'countertop_kelker_0',
            'storage': 'countertop_kelzer_0',
            'island': 'bar_udatjt_0',
            'fridge': 'fridge_dszchb_0',
            'microwave': 'microwave_abzvij_0',
            'cabinet': 'bottom_cabinet_fancyy_0',
            'table': 'coffee_table_rlsebe_0',
            'floor': 'floors_qgmjvd_0',
            'tea': 'bottle_of_tea_208',
            'board': 'chopping_board_211',
            'tray': 'hl2_tray',
            'apple': 'hl2_apple',
            'knife': 'hl2_knife',
            'hotdog1': 'hl2_hotdog1',
            'hotdog2': 'hl2_hotdog2',
            'plate1': 'hl2_plate1',
            'plate2': 'hl2_plate2',
            'fork1': 'hl2_fork1',
            'fork2': 'hl2_fork2',
            'cup1': 'hl2_cup1',
            'cup2': 'hl2_cup2',
            'apple_half0': 'half_hl2_apple_0',
            'apple_half1': 'half_hl2_apple_1',
            'spare_half_apple_a': 'half_apple_212',
            'spare_half_apple_b': 'half_apple_213',
            'unused_cookie': 'chocolate_chip_cookie_207',
            'unused_sandwich': 'club_sandwich_209',
            'unused_box': 'packing_box_210',
        },
        'scene_exclusions': [
            'water_glass_bbpraa_0',
        ],
        'construction_asset_scales': [
            {
                'object': 'board',
                'scale': [1.0, 1.0, 1.0],
                'reason': 'Use native afwefw cutting board size (25 x 37 cm) so it fits cleanly inside the pantry cabinet.',
            }
        ],
        'asset_imports': [
            {
                'name': 'hl2_hotdog1',
                'source_task': 'cook_hot_dogs',
                'source_object': 'hotdog_207',
                'source_sha256': '70c847d8bb48d9aa9ec505be60745636b2bee6b8eb82ffff439a78bb31d278d0',
                'position': [113.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'hl2_hotdog2',
                'source_task': 'cook_hot_dogs',
                'source_object': 'hotdog_208',
                'source_sha256': '70c847d8bb48d9aa9ec505be60745636b2bee6b8eb82ffff439a78bb31d278d0',
                'position': [116.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'hl2_plate1',
                'source_task': 'setting_the_table',
                'source_object': 'plate_243',
                'source_sha256': '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66',
                'position': [119.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.5, 1.5, 1.0],
            },
            {
                'name': 'hl2_plate2',
                'source_task': 'setting_the_table',
                'source_object': 'plate_244',
                'source_sha256': '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66',
                'position': [122.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.5, 1.5, 1.0],
            },
            {
                'name': 'hl2_fork1',
                'source_task': 'setting_the_table',
                'source_object': 'tablefork_247',
                'source_sha256': '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66',
                'position': [125.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.85, 2.2, 1.5],
            },
            {
                'name': 'hl2_fork2',
                'source_task': 'setting_the_table',
                'source_object': 'tablefork_248',
                'source_sha256': '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66',
                'position': [128.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.85, 2.2, 1.5],
            },
            {
                'name': 'hl2_cup1',
                'source_task': 'setting_the_table',
                'source_object': 'water_glass_bbpraa_0',
                'source_sha256': '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66',
                'position': [131.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.25, 1.25, 1.15],
            },
            {
                'name': 'hl2_cup2',
                'source_task': 'setting_the_table',
                'source_object': 'water_glass_bbpraa_0',
                'source_sha256': '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66',
                'position': [134.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.25, 1.25, 1.15],
            },
        ],
        'native_assets': [
            {
                'name': 'hl2_tray',
                'category': 'tray',
                'model': 'coqeme',
                'usd_sha256': 'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd',
                'prim_type': 0,
                'position': [95.0, 50.0, 2.0],
                'scale': [3.38, 2.16, 1.5],
            },
            {
                'name': 'hl2_apple',
                'category': 'apple',
                'model': 'agveuv',
                'usd_sha256': '4e1786f1e30519bb22ece59149f11d56a3d9212a94fd70b820ffc160629c5abb',
                'prim_type': 0,
                'position': [98.0, 50.0, 2.0],
            },
            {
                'name': 'hl2_knife',
                'category': 'carving_knife',
                'model': 'ghhbgo',
                'usd_sha256': 'f60f7200a713a180f7a9e9bb2f0576541e161d3e317ff9de5d11ba33ddc9ca82',
                'prim_type': 0,
                'position': [101.0, 50.0, 2.0],
                'scale': [1.0, 2.5, 2.0],
            },
        ],
        'timed_processes': [
            {
                'id': 'microwave_heat',
                'appliance': 'microwave',
                'items': ['hotdog1', 'hotdog2'],
                'containment': 'Inside',
                'require_closed': True,
                'require_toggled_on': True,
                'duration_seconds': 180.0,
                'set_object_state': 'Cooked',
                'running_message': 'The microwave is running; the food inside is still heating.',
                'completed_message': 'The microwave chime sounded: heating is complete.',
                'stopped_early_message': 'The microwave stopped before heating finished.',
            }
        ],
        'initial_layout': [
            # Raw hotdogs inside refrigerator
            {
                'object': 'fridge',
                'state': 'Open',
                'value': True,
                'kwargs': {'fully': True},
            },
            {
                'object': 'hotdog1',
                'target': 'fridge',
                'place_on_support': True,
                'xy': [7.68, -1.81],
                'support_probe_height': 1.0,
                'initial_relation': 'Inside',
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'hotdog1',
                'state': 'Cooked',
                'value': False,
            },
            {
                'object': 'hotdog2',
                'target': 'fridge',
                'place_on_support': True,
                'xy': [7.94, -1.81],
                'support_probe_height': 1.0,
                'initial_relation': 'Inside',
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'hotdog2',
                'state': 'Cooked',
                'value': False,
            },
            {
                'object': 'fridge',
                'state': 'Open',
                'value': False,
                'kwargs': {'fully': True},
            },
            # Cutting items on bright window storage counter (east of sink, west of fridge)
            {
                'object': 'board',
                'target': 'storage',
                'place_on_support': True,
                'xy': [6.55, -1.94],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'apple',
                'target': 'storage',
                'place_on_support': True,
                'xy': [6.98, -1.92],
            },
            {
                'object': 'knife',
                'target': 'storage',
                'place_on_support': True,
                'xy': [7.28, -1.92],
                'orientation': [0.0, 0.0, 0.70710678, 0.70710678],
            },
            # Tableware, drink, and square 48x48 cm tray on the kitchen island in plain view
            {
                'object': 'plate1',
                'target': 'island',
                'place_on_support': True,
                'xy': [6.02, 0.05],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'plate2',
                'target': 'island',
                'place_on_support': True,
                'xy': [6.42, 0.05],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'tray',
                'target': 'island',
                'place_on_support': True,
                'xy': [7.00, 0.08],
                'orientation': [0.0, 0.0, 0.70710678, 0.70710678],
            },
            {
                'object': 'cup1',
                'target': 'island',
                'place_on_support': True,
                'xy': [7.50, 0.05],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'cup2',
                'target': 'island',
                'place_on_support': True,
                'xy': [7.76, 0.05],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'fork1',
                'target': 'island',
                'place_on_support': True,
                'xy': [8.02, 0.05],
                'orientation': [0.0, 0.0, 0.70710678, 0.70710678],
            },
            {
                'object': 'fork2',
                'target': 'island',
                'place_on_support': True,
                'xy': [8.26, 0.05],
                'orientation': [0.0, 0.0, 0.70710678, 0.70710678],
            },
            {
                'object': 'tea',
                'target': 'island',
                'place_on_support': True,
                'xy': [8.54, 0.05],
            },
            # Unused base-task objects tucked onto 2.09 m tall cabinet top out of camera view
            {
                'object': 'spare_half_apple_a',
                'target': 'cabinet',
                'place_on_support': True,
                'xy': [8.22, -1.92],
            },
            {
                'object': 'spare_half_apple_b',
                'target': 'cabinet',
                'place_on_support': True,
                'xy': [8.42, -1.92],
            },
            {
                'object': 'unused_cookie',
                'target': 'cabinet',
                'place_on_support': True,
                'xy': [8.62, -1.92],
            },
            {
                'object': 'unused_sandwich',
                'target': 'cabinet',
                'place_on_support': True,
                'xy': [8.86, -1.92],
            },
            {
                'object': 'unused_box',
                'target': 'cabinet',
                'place_on_support': True,
                'xy': [9.22, -1.92],
            },
            # Ensure kitchen doors closed and microwave off
            {
                'object': 'cabinet',
                'state': 'Open',
                'value': False,
                'kwargs': {'fully': True},
            },
            {
                'object': 'microwave',
                'state': 'ToggledOn',
                'value': False,
            },
            {
                'object': 'microwave',
                'state': 'Open',
                'value': False,
                'kwargs': {'fully': True},
            },
        ],
        'initial_assertions': {
            'hotdog1_in_fridge': 'initial_hotdog1_Inside',
            'hotdog2_in_fridge': 'initial_hotdog2_Inside',
            'hotdog1_raw': {'not': 'hotdog1_cooked'},
            'hotdog2_raw': {'not': 'hotdog2_cooked'},
            'fridge_closed': {'not': 'fridge_open'},
            'cabinet_closed': {'not': 'cabinet_open'},
            'microwave_closed': {'not': 'microwave_open'},
            'microwave_off': {'not': 'microwave_on'},
            'board_on_storage': 'initial_board_OnTop',
            'apple_on_storage': 'initial_apple_OnTop',
            'knife_on_storage': 'initial_knife_OnTop',
            'plate1_on_island': 'initial_plate1_OnTop',
            'plate2_on_island': 'initial_plate2_OnTop',
            'tray_on_island': 'initial_tray_OnTop',
            'cup1_on_island': 'initial_cup1_OnTop',
            'cup2_on_island': 'initial_cup2_OnTop',
            'fork1_on_island': 'initial_fork1_OnTop',
            'fork2_on_island': 'initial_fork2_OnTop',
            'tea_on_island': 'initial_tea_OnTop',
            'intact_apple': {'not': 'apple_replaced'},
            'hand_empty': 'hand_empty',
        },
        'future_roles': [
            'apple_half0',
            'apple_half1',
        ],
        'replacements': [
            {
                'parent': 'apple',
                'children': [
                    {'role': 'apple_half0', 'category': 'half_apple', 'model': 'sguztn'},
                    {'role': 'apple_half1', 'category': 'half_apple', 'model': 'sguztn'},
                ],
            }
        ],
        'public_instruction': (
            'Prepare and serve a hot lunch with fresh fruit and tea for two people on the living-room '
            'coffee table, then put the cutting tools away and leave the kitchen closed and safe.'
        ),
        'public_context': [req1_text, req2_text, req3_text, req4_text],
        'requirements': [
            {
                'id': 'serve_hot_meals',
                'text': req1_text,
                'terminal': ['complete_servings'],
            },
            {
                'id': 'serve_tableware_and_tea',
                'text': req2_text,
                'terminal': ['tableware_served'],
            },
            {
                'id': 'reset_kitchen',
                'text': req3_text,
                'terminal': ['tools_stored', 'appliances_safe'],
            },
            {
                'id': 'hand_empty',
                'text': req4_text,
                'terminal': ['hand_empty'],
            },
        ],
        'evaluation': {
            'predicates': {
                'hotdog1_cooked': {'object': 'hotdog1', 'state': 'Cooked'},
                'hotdog2_cooked': {'object': 'hotdog2', 'state': 'Cooked'},
                'hotdog1_heated': {'object': 'hotdog1', 'state': 'ProcessComplete', 'process': 'microwave_heat'},
                'hotdog2_heated': {'object': 'hotdog2', 'state': 'ProcessComplete', 'process': 'microwave_heat'},
                'hotdog1_burnt': {'object': 'hotdog1', 'state': 'Burnt'},
                'hotdog2_burnt': {'object': 'hotdog2', 'state': 'Burnt'},
                'apple_replaced': {'object': 'apple', 'state': 'Replaced'},
                'fridge_open': {'object': 'fridge', 'state': 'Open'},
                'cabinet_open': {'object': 'cabinet', 'state': 'Open'},
                'microwave_open': {'object': 'microwave', 'state': 'Open'},
                'microwave_on': {'object': 'microwave', 'state': 'ToggledOn'},
                'hotdog1_ontop_plate1': {'object': 'hotdog1', 'state': 'OnTop', 'target': 'plate1'},
                'hotdog2_ontop_plate2': {'object': 'hotdog2', 'state': 'OnTop', 'target': 'plate2'},
                'hotdog1_ontop_plate2': {'object': 'hotdog1', 'state': 'OnTop', 'target': 'plate2'},
                'hotdog2_ontop_plate1': {'object': 'hotdog2', 'state': 'OnTop', 'target': 'plate1'},
                'apple_half0_ontop_plate1': {'object': 'apple_half0', 'state': 'OnTop', 'target': 'plate1'},
                'apple_half1_ontop_plate2': {'object': 'apple_half1', 'state': 'OnTop', 'target': 'plate2'},
                'apple_half0_ontop_plate2': {'object': 'apple_half0', 'state': 'OnTop', 'target': 'plate2'},
                'apple_half1_ontop_plate1': {'object': 'apple_half1', 'state': 'OnTop', 'target': 'plate1'},
                'tray_ontop_table': {'object': 'tray', 'state': 'OnTop', 'target': 'table'},
                'plate1_ontop_table': {'object': 'plate1', 'state': 'OnTop', 'target': 'table'},
                'plate2_ontop_table': {'object': 'plate2', 'state': 'OnTop', 'target': 'table'},
                'plate1_ontop_tray': {'object': 'plate1', 'state': 'OnTop', 'target': 'tray'},
                'plate2_ontop_tray': {'object': 'plate2', 'state': 'OnTop', 'target': 'tray'},
                'cup1_ontop_table': {'object': 'cup1', 'state': 'OnTop', 'target': 'table'},
                'cup2_ontop_table': {'object': 'cup2', 'state': 'OnTop', 'target': 'table'},
                'fork1_ontop_table': {'object': 'fork1', 'state': 'OnTop', 'target': 'table'},
                'fork2_ontop_table': {'object': 'fork2', 'state': 'OnTop', 'target': 'table'},
                'tea_ontop_table': {'object': 'tea', 'state': 'OnTop', 'target': 'table'},
                'cup1_ontop_tray': {'object': 'cup1', 'state': 'OnTop', 'target': 'tray'},
                'cup2_ontop_tray': {'object': 'cup2', 'state': 'OnTop', 'target': 'tray'},
                'fork1_ontop_tray': {'object': 'fork1', 'state': 'OnTop', 'target': 'tray'},
                'fork2_ontop_tray': {'object': 'fork2', 'state': 'OnTop', 'target': 'tray'},
                'tea_ontop_tray': {'object': 'tea', 'state': 'OnTop', 'target': 'tray'},
                'knife_inside_cabinet': {'object': 'knife', 'state': 'Inside', 'target': 'cabinet'},
                'board_inside_cabinet': {'object': 'board', 'state': 'Inside', 'target': 'cabinet'},
                'hand_empty': {'object': 'tea', 'state': 'HandEmpty'},
                # Initial-only verification predicates
                'initial_hotdog1_Inside': {'object': 'hotdog1', 'state': 'Inside', 'target': 'fridge'},
                'initial_hotdog2_Inside': {'object': 'hotdog2', 'state': 'Inside', 'target': 'fridge'},
                'initial_board_OnTop': {'object': 'board', 'state': 'OnTop', 'target': 'storage'},
                'initial_apple_OnTop': {'object': 'apple', 'state': 'OnTop', 'target': 'storage'},
                'initial_knife_OnTop': {'object': 'knife', 'state': 'OnTop', 'target': 'storage'},
                'initial_plate1_OnTop': {'object': 'plate1', 'state': 'OnTop', 'target': 'island'},
                'initial_plate2_OnTop': {'object': 'plate2', 'state': 'OnTop', 'target': 'island'},
                'initial_tray_OnTop': {'object': 'tray', 'state': 'OnTop', 'target': 'island'},
                'initial_cup1_OnTop': {'object': 'cup1', 'state': 'OnTop', 'target': 'island'},
                'initial_cup2_OnTop': {'object': 'cup2', 'state': 'OnTop', 'target': 'island'},
                'initial_fork1_OnTop': {'object': 'fork1', 'state': 'OnTop', 'target': 'island'},
                'initial_fork2_OnTop': {'object': 'fork2', 'state': 'OnTop', 'target': 'island'},
                'initial_tea_OnTop': {'object': 'tea', 'state': 'OnTop', 'target': 'island'},
            },
            'terminal': {
                'complete_servings': {
                    'all': [
                        'apple_replaced',
                        'hotdog1_cooked',
                        'hotdog2_cooked',
                        'hotdog1_heated',
                        'hotdog2_heated',
                        {'not': 'hotdog1_burnt'},
                        {'not': 'hotdog2_burnt'},
                        {
                            'any': [
                                {'all': ['hotdog1_ontop_plate1', 'hotdog2_ontop_plate2']},
                                {'all': ['hotdog1_ontop_plate2', 'hotdog2_ontop_plate1']},
                            ]
                        },
                        {
                            'any': [
                                {'all': ['apple_half0_ontop_plate1', 'apple_half1_ontop_plate2']},
                                {'all': ['apple_half0_ontop_plate2', 'apple_half1_ontop_plate1']},
                            ]
                        },
                        served_on_table_or_tray('plate1_ontop_table', 'plate1_ontop_tray'),
                        served_on_table_or_tray('plate2_ontop_table', 'plate2_ontop_tray'),
                    ]
                },
                'tableware_served': {
                    'all': [
                        served_on_table_or_tray('cup1_ontop_table', 'cup1_ontop_tray'),
                        served_on_table_or_tray('cup2_ontop_table', 'cup2_ontop_tray'),
                        served_on_table_or_tray('fork1_ontop_table', 'fork1_ontop_tray'),
                        served_on_table_or_tray('fork2_ontop_table', 'fork2_ontop_tray'),
                        served_on_table_or_tray('tea_ontop_table', 'tea_ontop_tray'),
                    ]
                },
                'tools_stored': {
                    'all': [
                        'knife_inside_cabinet',
                        'board_inside_cabinet',
                    ]
                },
                'appliances_safe': {
                    'all': [
                        {'not': 'fridge_open'},
                        {'not': 'cabinet_open'},
                        {'not': 'microwave_open'},
                        {'not': 'microwave_on'},
                    ]
                },
                'hand_empty': 'hand_empty',
            },
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': [
                'optimal_parallel_tray',
                'habit_sequential',
            ],
            'physical_negative_cases': [
                'omit_hot_delivery',
                'omit_cold_delivery',
                'omit_kitchen_reset',
            ],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 300,
        },
        'reference_plan': {
            'kind': 'full_workflow',
            'allow_strategy_variants': True,
            'phases': {
                'start_microwave': {
                    'actions': [
                        {'primitive': 'open', 'role': 'microwave'},
                        {'primitive': 'open', 'role': 'fridge'},
                        {'primitive': 'grasp', 'role': 'hotdog1', 'approach_role': 'fridge'},
                        {'primitive': 'place_inside', 'role': 'microwave'},
                        {'primitive': 'grasp', 'role': 'hotdog2', 'approach_role': 'fridge'},
                        {'primitive': 'place_inside', 'role': 'microwave'},
                        {'primitive': 'close', 'role': 'microwave'},
                        {'primitive': 'toggle_on', 'role': 'microwave'},
                        {'primitive': 'close', 'role': 'fridge'},
                    ],
                    'after': [],
                },
                'idle_wait_microwave': {
                    'actions': [
                        {
                            'primitive': 'wait_for',
                            'condition': {'all': ['hotdog1_cooked', 'hotdog2_cooked', 'hotdog1_heated', 'hotdog2_heated']},
                            'seconds': 60.0,
                            'attempts': 4,
                        }
                    ],
                    'after': ['start_microwave'],
                },
                'prep_fruit': {
                    'actions': [
                        {'primitive': 'open', 'role': 'cabinet'},
                        {'primitive': 'grasp', 'role': 'knife'},
                        {'primitive': 'cut', 'role': 'apple'},
                        {'primitive': 'place_inside', 'role': 'cabinet'},
                    ],
                    'after': [],
                },
                'reset_kitchen_cabinet': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'board'},
                        {'primitive': 'place_inside', 'role': 'cabinet'},
                        {'primitive': 'close', 'role': 'cabinet'},
                    ],
                    'after': ['prep_fruit'],
                },
                'deliver_cold_batch_on_tray': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'cup1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.22, 0.24]},
                        {'primitive': 'grasp', 'role': 'tea'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.50, 0.24]},
                        {'primitive': 'grasp', 'role': 'cup2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.78, 0.24]},
                        {'primitive': 'grasp', 'role': 'fork1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.27, 0.73]},
                        {'primitive': 'grasp', 'role': 'fork2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.73, 0.73]},
                        {'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.71, 0.50]},
                    ],
                    'after': [],
                },
                'deliver_cold_single_trips': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'cup1'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.62, 0.80]},
                        {'primitive': 'grasp', 'role': 'cup2'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.84, 0.80]},
                        {'primitive': 'grasp', 'role': 'tea'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.73, 0.52]},
                        {'primitive': 'grasp', 'role': 'fork1'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.62, 0.24]},
                        {'primitive': 'grasp', 'role': 'fork2'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.84, 0.24]},
                    ],
                    'after': [],
                },
                'unload_cooked_hotdogs': {
                    'actions': [
                        {
                            'primitive': 'wait_for',
                            'condition': {'all': ['hotdog1_cooked', 'hotdog2_cooked', 'hotdog1_heated', 'hotdog2_heated']},
                            'seconds': 30.0,
                            'attempts': 6,
                        },
                        {'primitive': 'toggle_off', 'role': 'microwave'},
                        {'primitive': 'open', 'role': 'microwave'},
                        {'primitive': 'grasp', 'role': 'hotdog1', 'approach_role': 'microwave'},
                        {'primitive': 'place_on_top', 'role': 'plate1', 'uv': [0.40, 0.50]},
                        {'primitive': 'grasp', 'role': 'hotdog2', 'approach_role': 'microwave'},
                        {'primitive': 'place_on_top', 'role': 'plate2', 'uv': [0.40, 0.50]},
                        {'primitive': 'close', 'role': 'microwave'},
                    ],
                    'after': ['start_microwave'],
                },
                'finish_plates_and_deliver': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'apple_half0'},
                        {'primitive': 'place_on_top', 'role': 'plate1', 'uv': [0.70, 0.50]},
                        {'primitive': 'grasp', 'role': 'apple_half1'},
                        {'primitive': 'place_on_top', 'role': 'plate2', 'uv': [0.70, 0.50]},
                        {'primitive': 'grasp', 'role': 'plate1'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.20, 0.74]},
                        {'primitive': 'grasp', 'role': 'plate2'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.20, 0.26]},
                    ],
                    'after': ['unload_cooked_hotdogs', 'prep_fruit'],
                },
            },
            'orders': {
                'optimal_parallel_tray': [
                    'start_microwave',
                    'prep_fruit',
                    'reset_kitchen_cabinet',
                    'deliver_cold_batch_on_tray',
                    'unload_cooked_hotdogs',
                    'finish_plates_and_deliver',
                ],
                'habit_sequential': [
                    'start_microwave',
                    'idle_wait_microwave',
                    'unload_cooked_hotdogs',
                    'prep_fruit',
                    'reset_kitchen_cabinet',
                    'finish_plates_and_deliver',
                    'deliver_cold_single_trips',
                ],
            },
            'negative_omissions': {
                'omit_hot_delivery': 'finish_plates_and_deliver',
                'omit_cold_delivery': 'deliver_cold_batch_on_tray',
                'omit_kitchen_reset': 'reset_kitchen_cabinet',
            },
        },
        'settle_steps': 30,
    }
    return spec


def main() -> None:
    out = HERE / 'recipe.json'
    spec = build_recipe()
    validation = validate_spec(spec)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n')
    print('WROTE:', out)
    print('VALIDATION:', json.dumps(validation, indent=2))


if __name__ == '__main__':
    main()
