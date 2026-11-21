#!/usr/bin/env python3
"""Generate recipe.json for custom task `three_appliance_brunch` (C1, benchmark side).

Task: 三设备异构时长的双人早午餐 —— 烤箱烤小乳蛋饼 360 s（r2 起替换土豆） + 灶台平底锅煎香肠 150 s + 微波炉热贝果 90 s（自动停）
Base scene: `preparing_lunch_box` instance 301, seed 0 (`house_single_floor`), same base as T1/C4.

Paper design and numbers: experiments/cooking_parallel_tasks_20261120/README.md (C1) and tasks.py
(habit 1483 s / 176 m, optimal 793 s / 122 m; accepted 2026-11-20 on time saving, route saving 31%).

Changes against the paper design (my call under "less trouble for the agent", recorded in the README):
  - eggs -> sausages: the egg assets are 5-7 cm (too small to click reliably); bratwurst is 23 x 11 cm;
  - toaster oven -> the scene microwave (validated in T1); importing a new appliance was the main build risk;
    the microwave sits next to the stove where the paper toaster was, so routes are unchanged;
  - the griddle pan is fixed on the stove: moving the pan is not part of the planning structure.

Planning structure: three processes of very different length (360 / 150 / 90 s) -> start the longest first;
fill the waits with the cold tray run to the living room (8 m); plate in completion order.
Benchmark-side rules (all tiers): timed_processes v4 (microwave switch_off_when_complete; the oven and the
stove do not stop by themselves, stated in the public text as appliance facts). No overcook window in C1.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / 'src'))
from manipulation_agent.tasks.custom_tasks import validate_spec  # noqa: E402

FOODS = ['sausage', 'bagel']   # one each per plate; the quiche is served whole
SETTING = '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66'
PLATE_UV = {'sausage': [0.50, 0.30], 'bagel': [0.50, 0.72]}
# r3: ffitak's racks are only 41 x 22 cm; 16 cm potatoes (r1) and mini quiches (r2) slid off onto the cavity floor,
# where native Inside is false. One 24 cm quiche, the size of the C4 pie that rests on the rack, at the pie's aim.
OVEN_AIM = [8.43, -1.85]
# r4: the pan's bowl is its north end (y ~ -0.39); without an aim the reference clicked the south rim and the
# sausage landed on the bare burner beside the pan (c1r3 optimal). Reference tooling only.
PAN_AIM = {'sausage1': [4.17, -0.58], 'sausage2': [4.17, -0.42]}


def native(name, category, model, sha, x, scale=None, fixed=False):
    row = {'name': name, 'category': category, 'model': model, 'usd_sha256': sha, 'prim_type': 0,
           'position': [x, 50.0, 2.0], 'orientation': [0.0, 0.0, 0.0, 1.0]}
    if scale is not None:
        row['scale'] = scale
    if fixed:
        row['fixed_base'] = True
    return row


def imported(name, task, obj, x, scale):
    return {'name': name, 'source_task': task, 'source_object': obj, 'source_sha256': SETTING,
            'position': [x, 50.0, 2.0], 'orientation': [0.0, 0.0, 0.0, 1.0], 'scale': scale}


def on(obj, target, xy, yaw90=False, **extra):
    row = {'object': obj, 'target': target, 'place_on_support': True, 'xy': xy,
           'orientation': [0.0, 0.0, 0.70710678, 0.70710678] if yaw90 else [0.0, 0.0, 0.0, 1.0]}
    row.update(extra)
    return row


def state(obj, name, value, fully=False):
    row = {'object': obj, 'state': name, 'value': value}
    if fully:
        row['kwargs'] = {'fully': True}
    return row


def served(item):
    return {'any': [f'{item}_ontop_table', {'all': [f'{item}_ontop_tray', 'tray_ontop_table']}]}


def one_each(food):
    return {'any': [{'all': [f'{food}1_ontop_plate1', f'{food}2_ontop_plate2']},
                    {'all': [f'{food}1_ontop_plate2', f'{food}2_ontop_plate1']}]}


def build_recipe() -> dict:
    req_hot = (
        'Serve brunch at the living-room coffee table: two plates, each with one fried sausage and one warm '
        'bagel, and the baked quiche served whole (each plate and the quiche directly on the coffee table or on '
        'the serving tray resting on it). Bake the quiche in the black built-in oven to the right of the refrigerator: about 6 '
        'minutes with the oven door closed and the oven switched on. Fry both sausages in the griddle pan on '
        'the stove on the west preparation counter: about 2.5 minutes with the stove switched on. Warm both '
        'bagels in the microwave beside the stove: about 1.5 minutes with the door closed and the microwave '
        'switched on; the microwave switches itself off when it is done, the oven and the stove do not. The '
        'quiche and the bagels start on the counter between the sink and the refrigerator, the sausages inside '
        'the refrigerator, and the two plates on the kitchen island. The living-room coffee table is the low '
        'square table in front of the sofas east of the kitchen.'
    )
    req_cold = (
        'Serve the two cups, two forks, and tea bottle at the living-room coffee table, placed either '
        'directly on the coffee table or on the serving tray resting on the coffee table. The two cups, '
        'two forks, tea bottle, and serving tray start on the kitchen island.'
    )
    req_reset = 'Leave the oven and the stove switched off, and the oven, microwave and refrigerator closed.'
    req_hand = 'Finish with no object held.'

    items = [f'{f}{i}' for f in FOODS for i in (1, 2)]
    cold = ['cup1', 'cup2', 'fork1', 'fork2', 'tea']
    island_items = ['plate1', 'plate2', 'tray'] + cold
    spec = {
        'schema_version': 3,
        'id': 'three_appliance_brunch',
        'instance_id': 'three_appliance_brunch_seed0_r7b',
        'status': 'complete_recipe_physics_pending',
        'benchmark_ready': False,
        'official_submission_eligible': False,
        'base': {'task': 'preparing_lunch_box', 'instance': 301, 'seed': 0},
        'roles': {
            'storage': 'countertop_kelzer_0',
            'island': 'bar_udatjt_0',
            'fridge': 'fridge_dszchb_0',
            'oven': 'oven_ffitak_0',
            'microwave': 'microwave_abzvij_0',
            'stove': 'burner_mjvqii_0',
            'cabinet': 'bottom_cabinet_fancyy_0',
            'table': 'coffee_table_rlsebe_0',
            'floor': 'floors_qgmjvd_0',
            'pan': 'c1_pan',
            'tea': 'bottle_of_tea_208',
            'tray': 'c1_tray',
            'plate1': 'c1_plate1', 'plate2': 'c1_plate2',
            'fork1': 'c1_fork1', 'fork2': 'c1_fork2',
            'cup1': 'c1_cup1', 'cup2': 'c1_cup2',
            'quiche': 'c1_quiche',
            'sausage1': 'c1_sausage1', 'sausage2': 'c1_sausage2',
            'bagel1': 'c1_bagel1', 'bagel2': 'c1_bagel2',
            # unused base-task objects (preparing_lunch_box BDDL scope)
            'unused_board': 'chopping_board_211',
            'unused_half_apple_a': 'half_apple_212',
            'unused_half_apple_b': 'half_apple_213',
            'unused_cookie': 'chocolate_chip_cookie_207',
            'unused_sandwich': 'club_sandwich_209',
            'unused_box': 'packing_box_210',
        },
        'scene_exclusions': ['water_glass_bbpraa_0'],
        # T1 practice: native 25 x 37 cm board (the scene copy is ~52 x 75 cm and fell out of the pantry, c1 r0)
        'construction_asset_scales': [{'object': 'unused_board', 'scale': [1.0, 1.0, 1.0],
                                       'reason': 'Native board size so it fits inside the closed pantry cabinet.'}],
        'asset_imports': [
            # r6: 34 cm plates; on 25.6 cm plates the 23 cm sausage reached the rim, touched the island and was
            # left behind when the plate was lifted (c1r5 habit, grasp payload left in place)
            # r7: 30 cm (34 cm plates + tray + quiche crowded the 98 cm table, c1r6)
            imported('c1_plate1', 'setting_the_table', 'plate_243', 119.0, [1.75, 1.75, 1.0]),
            imported('c1_plate2', 'setting_the_table', 'plate_244', 122.0, [1.75, 1.75, 1.0]),
            imported('c1_fork1', 'setting_the_table', 'tablefork_247', 125.0, [0.85, 2.2, 1.5]),
            imported('c1_fork2', 'setting_the_table', 'tablefork_248', 128.0, [0.85, 2.2, 1.5]),
            imported('c1_cup1', 'setting_the_table', 'water_glass_bbpraa_0', 131.0, [1.25, 1.25, 1.15]),
            imported('c1_cup2', 'setting_the_table', 'water_glass_bbpraa_0', 134.0, [1.25, 1.25, 1.15]),
        ],
        'native_assets': [
            native('c1_tray', 'tray', 'coqeme',
                   'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd', 95.0, [3.38, 2.16, 1.5]),
            native('c1_pan', 'griddle_pan', 'ryvqep',
                   '704c9e825784e54cb78730585111263a3832e79caa357c22963e87e47f8ce63e', 98.0, [1.5, 1.6, 1.0],
                   fixed=True),
            # r3: one 24 x 24 x 4 cm quiche (pie-sized; potatoes r1 and 16 cm mini quiches r2 slid off the rack)
            native('c1_quiche', 'quiche', 'borxmv',
                   '1fa9c0efc74208bf9f94d4c2ec0da8994f9dc0e371bdaf33d2b134fce5bff8a6', 100.0, [1.1, 1.1, 1.4]),
            *[native(f'c1_sausage{i}', 'bratwurst', 'pqfrrn',
                     '86266778cc1596287bb28195075443d7bdeb2a73c4d3815ac6232a0ecb6fc69c', 104.0 + 2 * i)
              for i in (1, 2)],
            *[native(f'c1_bagel{i}', 'bagel', 'zlxkry',
                     '57467aa695bccaa4ffce63eaac4b507b25c2837c2d537883e2415c783a4b66ab', 108.0 + 2 * i,
                     [0.8, 0.8, 0.8]) for i in (1, 2)],
        ],
        # r6: the oven's Open state is its door only (its racks are openable joints too; see C2 r3)
        'open_state_joints': {'oven': ['j_door']},
        'timed_processes': [
            {
                'id': 'bake_quiche', 'appliance': 'oven', 'items': ['quiche'],
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 360.0,
                'running_message': 'The oven is on; the quiche inside is baking.',
                'completed_message': 'The oven timer beeped: the quiche is baked.',
                'stopped_early_message': 'The oven stopped before the quiche was baked.',
            },
            {
                'id': 'fry_sausages', 'appliance': 'stove', 'items': ['sausage1', 'sausage2'],
                'containment': 'OnTop', 'require_closed': False, 'require_toggled_on': True,
                'duration_seconds': 150.0,
                'running_message': 'The stove is on; the sausages in the pan are frying.',
                'completed_message': 'The sausages in the pan are done.',
                'stopped_early_message': 'The stove was switched off before the sausages were done.',
            },
            {
                'id': 'warm_bagels', 'appliance': 'microwave', 'items': ['bagel1', 'bagel2'],
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 90.0, 'switch_off_when_complete': True,
                'running_message': 'The microwave is running; the bagels inside are warming.',
                'completed_message': 'The microwave chimed: the bagels are warm and it has switched itself off.',
                'stopped_early_message': 'The microwave stopped before the bagels were warm.',
            },
        ],
        'initial_layout': [
            # sausages inside the closed refrigerator
            state('fridge', 'Open', True, fully=True),
            on('sausage1', 'fridge', [7.68, -1.85], yaw90=True, support_probe_height=1.0,
               initial_relation='Inside'),
            on('sausage2', 'fridge', [7.94, -1.85], yaw90=True, support_probe_height=1.0,
               initial_relation='Inside'),
            state('fridge', 'Open', False, fully=True),
            # quiche and bagels on the counter between the sink and the refrigerator
            on('quiche', 'storage', [6.62, -1.94]),
            on('bagel1', 'storage', [7.06, -1.94]),
            on('bagel2', 'storage', [7.32, -1.94]),
            # griddle pan fixed on the stove, handle along the counter
            on('pan', 'stove', [4.17, -0.50], yaw90=True),  # flat griddle (r5/C3 r2)
            # tableware, drink and tray on the kitchen island in plain view
            on('plate1', 'island', [6.00, 0.08]),
            on('plate2', 'island', [6.40, 0.08]),
            on('tray', 'island', [7.00, 0.08], yaw90=True),
            on('cup1', 'island', [7.50, 0.05]),
            on('cup2', 'island', [7.76, 0.05]),
            on('fork1', 'island', [8.02, 0.05], yaw90=True),
            on('fork2', 'island', [8.26, 0.05], yaw90=True),
            on('tea', 'island', [8.54, 0.05]),
            # unused base-task objects: board inside the closed pantry cabinet, the rest on its roof (as T1/C4)
            state('cabinet', 'Open', True, fully=True),
            on('unused_board', 'cabinet', [8.74, -1.96], support_probe_height=0.9, initial_relation='Inside'),
            on('unused_half_apple_a', 'cabinet', [8.22, -1.92]),
            on('unused_half_apple_b', 'cabinet', [8.42, -1.92]),
            on('unused_cookie', 'cabinet', [8.62, -1.92]),
            on('unused_sandwich', 'cabinet', [8.86, -1.92]),
            on('unused_box', 'cabinet', [9.22, -1.92]),
            state('cabinet', 'Open', False, fully=True),
            # appliances closed and off
            state('oven', 'ToggledOn', False),
            state('oven', 'Open', False, fully=True),
            state('microwave', 'ToggledOn', False),
            state('microwave', 'Open', False, fully=True),
            state('stove', 'ToggledOn', False),
        ],
        'initial_assertions': {
            **{f'{s}_in_fridge': f'initial_{s}_Inside' for s in ('sausage1', 'sausage2')},
            **{f'{o}_on_storage': f'initial_{o}_OnTop' for o in ('quiche', 'bagel1', 'bagel2')},
            **{f'{o}_on_island': f'initial_{o}_OnTop' for o in island_items},
            'fridge_closed': {'not': 'fridge_open'},
            'cabinet_closed': {'not': 'cabinet_open'},
            'oven_closed': {'not': 'oven_open'},
            'microwave_closed': {'not': 'microwave_open'},
            'oven_off': {'not': 'oven_on'},
            'microwave_off': {'not': 'microwave_on'},
            'stove_off': {'not': 'stove_on'},
            'hand_empty': 'hand_empty',
        },
        'future_roles': [],
        'replacements': [],
        'public_instruction': (
            'Cook and serve brunch for two on the living-room coffee table: a baked quiche, fried sausages and '
            'warm bagels on two plates, with cups, forks and tea, and leave the kitchen appliances off and closed.'
        ),
        'public_context': [req_hot, req_cold, req_reset, req_hand],
        'requirements': [
            {'id': 'serve_hot_plates', 'text': req_hot, 'terminal': ['plates_served']},
            {'id': 'serve_tableware_and_tea', 'text': req_cold, 'terminal': ['tableware_served']},
            {'id': 'appliances_safe', 'text': req_reset, 'terminal': ['appliances_safe']},
            {'id': 'hand_empty', 'text': req_hand, 'terminal': ['hand_empty']},
        ],
        'evaluation': {
            'predicates': {
                **{f'{f}{i}_done': {'object': f'{f}{i}', 'state': 'ProcessComplete', 'process': p}
                   for f, p in (('sausage', 'fry_sausages'),
                                ('bagel', 'warm_bagels')) for i in (1, 2)},
                'quiche_done': {'object': 'quiche', 'state': 'ProcessComplete', 'process': 'bake_quiche'},
                **{f'{o}_ontop_plate{p}': {'object': o, 'state': 'OnTop', 'target': f'plate{p}'}
                   for o in items for p in (1, 2)},
                **{f'{o}_ontop_table': {'object': o, 'state': 'OnTop', 'target': 'table'}
                   for o in ['plate1', 'plate2', 'quiche'] + cold},
                **{f'{o}_ontop_tray': {'object': o, 'state': 'OnTop', 'target': 'tray'}
                   for o in ['plate1', 'plate2', 'quiche'] + cold},
                'tray_ontop_table': {'object': 'tray', 'state': 'OnTop', 'target': 'table'},
                'fridge_open': {'object': 'fridge', 'state': 'Open'},
                'cabinet_open': {'object': 'cabinet', 'state': 'Open'},
                'oven_open': {'object': 'oven', 'state': 'Open'},
                'microwave_open': {'object': 'microwave', 'state': 'Open'},
                'oven_on': {'object': 'oven', 'state': 'ToggledOn'},
                'microwave_on': {'object': 'microwave', 'state': 'ToggledOn'},
                'stove_on': {'object': 'stove', 'state': 'ToggledOn'},
                'hand_empty': {'object': 'tea', 'state': 'HandEmpty'},
                # initial-only verification predicates
                **{f'initial_{s}_Inside': {'object': s, 'state': 'Inside', 'target': 'fridge'}
                   for s in ('sausage1', 'sausage2')},
                **{f'initial_{o}_OnTop': {'object': o, 'state': 'OnTop', 'target': 'storage'}
                   for o in ('quiche', 'bagel1', 'bagel2')},
                **{f'initial_{o}_OnTop': {'object': o, 'state': 'OnTop', 'target': 'island'}
                   for o in island_items},
            },
            'terminal': {
                'plates_served': {'all': [f'{o}_done' for o in items] + [one_each(f) for f in FOODS]
                                  + [served('plate1'), served('plate2'), 'quiche_done', served('quiche')]},
                'tableware_served': {'all': [served(o) for o in cold]},
                'appliances_safe': {'all': [{'not': 'oven_on'}, {'not': 'stove_on'}, {'not': 'microwave_on'},
                                            {'not': 'oven_open'}, {'not': 'microwave_open'},
                                            {'not': 'fridge_open'}]},
                'hand_empty': 'hand_empty',
            },
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': ['optimal_longest_first_tray', 'habit_sequential'],
            'physical_negative_cases': ['omit_plate_delivery', 'omit_cold_delivery', 'omit_stove_off'],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 300,
        },
        'reference_plan': build_reference_plan(),
        'settle_steps': 30,
    }
    return spec


def wait(cond, seconds, attempts):
    return {'primitive': 'wait_for', 'condition': cond, 'seconds': seconds, 'attempts': attempts}


def build_reference_plan() -> dict:
    done = lambda f: {'all': [f'{f}1_done', f'{f}2_done']}  # noqa: E731
    to_plates = lambda f, approach: [a for i in (1, 2) for a in (  # noqa: E731
        {'primitive': 'grasp', 'role': f'{f}{i}', 'approach_role': approach},
        {'primitive': 'place_on_top', 'role': f'plate{i}', 'uv': PLATE_UV[f]})]
    phases = {
        'start_oven': {'actions': [{'primitive': 'open', 'role': 'oven'}]
                       + [{'primitive': 'grasp', 'role': 'quiche'},
                          {'primitive': 'place_inside', 'role': 'oven', 'xy': OVEN_AIM}]
                       + [{'primitive': 'close', 'role': 'oven'}, {'primitive': 'toggle_on', 'role': 'oven'}],
                       'after': []},
        'start_microwave': {'actions': [{'primitive': 'open', 'role': 'microwave'}]
                            + [a for b in ('bagel1', 'bagel2') for a in (
                                {'primitive': 'grasp', 'role': b},
                                {'primitive': 'place_inside', 'role': 'microwave'})]
                            + [{'primitive': 'close', 'role': 'microwave'},
                               {'primitive': 'toggle_on', 'role': 'microwave'}],
                            'after': []},
        'start_stove': {'actions': [{'primitive': 'open', 'role': 'fridge'}]
                        + [a for s in ('sausage1', 'sausage2') for a in (
                            {'primitive': 'grasp', 'role': s, 'approach_role': 'fridge'},
                            {'primitive': 'place_on_top', 'role': 'pan', 'xy': PAN_AIM[s]})]
                        + [{'primitive': 'close', 'role': 'fridge'}, {'primitive': 'toggle_on', 'role': 'stove'}],
                        'after': []},
        'deliver_cold_batch_on_tray': {
            'actions': [{'primitive': 'grasp', 'role': 'cup1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.20, 0.20]},
                        {'primitive': 'grasp', 'role': 'tea'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.50, 0.20]},
                        {'primitive': 'grasp', 'role': 'cup2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.80, 0.20]},
                        {'primitive': 'grasp', 'role': 'fork1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.16, 0.70]},
                        {'primitive': 'grasp', 'role': 'fork2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.32, 0.70]},
                        {'primitive': 'grasp', 'role': 'tray'},
                        # r7b: near T1's aim; at the edge (0.95, r7) the tray slid off the table
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.80, 0.50]}],
            'after': []},
        'deliver_cold_single_trips': {
            'actions': [{'primitive': 'grasp', 'role': 'cup1'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.62, 0.80]},
                        {'primitive': 'grasp', 'role': 'cup2'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.84, 0.80]},
                        {'primitive': 'grasp', 'role': 'tea'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.73, 0.52]},
                        {'primitive': 'grasp', 'role': 'fork1'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.62, 0.24]},
                        {'primitive': 'grasp', 'role': 'fork2'},
                        {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.84, 0.24]}],
            'after': []},
        'idle_wait_oven': {'actions': [wait('quiche_done', 60.0, 8)], 'after': ['start_oven']},
        'idle_wait_stove': {'actions': [wait(done('sausage'), 30.0, 8)], 'after': ['start_stove']},
        'idle_wait_microwave': {'actions': [wait(done('bagel'), 30.0, 6)], 'after': ['start_microwave']},
        'plate_bagels': {'actions': [wait(done('bagel'), 20.0, 10), {'primitive': 'open', 'role': 'microwave'}]
                         + to_plates('bagel', 'microwave') + [{'primitive': 'close', 'role': 'microwave'}],
                         'after': ['start_microwave']},
        'plate_sausages': {'actions': [wait(done('sausage'), 20.0, 12)] + to_plates('sausage', 'stove'),
                           'after': ['start_stove']},
        # separate so the omit_stove_off negative leaves only the stove running
        'stove_off': {'actions': [{'primitive': 'toggle_off', 'role': 'stove'}], 'after': ['plate_sausages']},
        'serve_quiche': {'actions': [wait('quiche_done', 20.0, 20), {'primitive': 'toggle_off', 'role': 'oven'},
                                     {'primitive': 'open', 'role': 'oven'},
                                     {'primitive': 'grasp', 'role': 'quiche', 'approach_role': 'oven'},
                                     {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.25, 0.50]}],
                         'after': ['start_oven']},
        # optimal: onto the tray already on the table (free bottom-right quadrant)
        'serve_quiche_tray': {'actions': [wait('quiche_done', 20.0, 20), {'primitive': 'toggle_off', 'role': 'oven'},
                                          {'primitive': 'open', 'role': 'oven'},
                                          {'primitive': 'grasp', 'role': 'quiche', 'approach_role': 'oven'},
                                          {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.72, 0.72]}],
                              'after': ['start_oven', 'deliver_cold_batch_on_tray']},
        'close_oven': {'actions': [{'primitive': 'close', 'role': 'oven'}], 'after': ['start_oven']},
        'deliver_plates': {'actions': [{'primitive': 'grasp', 'role': 'plate1'},
                                       {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.18, 0.80]},
                                       {'primitive': 'grasp', 'role': 'plate2'},
                                       {'primitive': 'place_on_top', 'role': 'table', 'uv': [0.18, 0.20]}],
                           'after': ['plate_bagels', 'plate_sausages']},
    }
    orders = {
        'optimal_longest_first_tray': [
            'start_oven', 'start_microwave', 'start_stove', 'deliver_cold_batch_on_tray',
            'plate_bagels', 'plate_sausages', 'stove_off', 'serve_quiche_tray', 'close_oven', 'deliver_plates',
        ],
        'habit_sequential': [
            'start_oven', 'idle_wait_oven', 'serve_quiche', 'close_oven',
            'start_stove', 'idle_wait_stove', 'plate_sausages', 'stove_off',
            'start_microwave', 'idle_wait_microwave', 'plate_bagels',
            'deliver_plates', 'deliver_cold_single_trips',
        ],
    }
    return {
        'kind': 'full_workflow',
        'allow_strategy_variants': True,
        'phases': phases,
        'orders': orders,
        'negative_omissions': {
            'omit_plate_delivery': 'deliver_plates',
            'omit_cold_delivery': 'deliver_cold_batch_on_tray',
            'omit_stove_off': 'stove_off',
        },
    }


def main() -> None:
    out = HERE / 'recipe.json'
    spec = build_recipe()
    validation = validate_spec(spec)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n')
    print('WROTE:', out)
    print('VALIDATION:', json.dumps(validation, indent=2))


if __name__ == '__main__':
    main()
