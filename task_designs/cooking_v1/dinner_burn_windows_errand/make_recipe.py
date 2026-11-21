#!/usr/bin/env python3
"""Generate recipe.json for custom task `dinner_burn_windows_errand` (C3, benchmark side).

Task: 有过火窗口的晚餐 + 远处取酒 —— 两碗汤进微波炉 180 s（自动停，不会糊）；一盘蔬菜进烤箱 240 s，熟后继续开着
超过 120 s 会糊；两块牛排在灶台平底锅里煎 120 s，熟后继续开着超过 30 s 会糊；洗衣间台面上的一瓶酒和两个玻璃杯
送到客厅茶几（洗衣间离厨房约 20 m，那里有第二个托盘）。
Base scene: `preparing_lunch_box` instance 301, seed 0 (`house_single_floor`), same base as T1/C1/C2/C4.

Paper design and numbers: experiments/cooking_parallel_tasks_20261120/README.md (C3) and tasks.py
(habit 1408 s / 224 m; trap "start everything, then the errand" 773 s burns steaks and vegetables; trap "oven first,
then the errand" burns the vegetables; optimal 930 s / 142 m).

Changes against the paper design (my call, recorded in the README): the utility-room basket is a second serving
tray (a bottle standing in a basket is a fussy placement, not planning); the reference carries the two steak
plates by hand (nested plate-on-tray payloads are not needed to show the saving).

Planning structure: self-stopping soup first; oven next; the far errand fits inside the oven's done+120 s window;
the steaks (shortest window) last and the cook stays at the stove. Benchmark-side rules (all tiers): timed_processes
v3 overcook window (oven, stove), v4 auto-off (microwave). The griddle pan is fixed on the stove (C1 practice).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / 'src'))
from manipulation_agent.tasks.custom_tasks import validate_spec  # noqa: E402

SETTING = '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66'
TABLE_ITEMS = ['soup1', 'soup2', 'veg', 'plate1', 'plate2', 'wine', 'glass1', 'glass2']


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
    return {'any': [f'{item}_ontop_table'] + [{'all': [f'{item}_ontop_{t}', f'{t}_ontop_table']}
                                              for t in ('tray', 'tray2')]}


def build_recipe() -> dict:
    req_food = (
        'Serve dinner for two on the living-room coffee table (the low square table in front of the sofas east '
        'of the kitchen): the two bowls of soup heated, the dish of vegetables roasted, and the two steaks '
        'fried and served on the two plates, one steak per plate; each item directly on the table or on a '
        'serving tray resting on it. The soup bowls and the steaks start inside the refrigerator, the vegetable '
        'dish on the counter between the sink and the refrigerator, and the two plates and a serving tray on '
        'the kitchen island. Heat the soup in the microwave on the west preparation counter: about 3 minutes '
        'with the door closed and the microwave switched on; it switches itself off when done and the soup '
        'does not burn. Roast the vegetables in the black built-in oven to the right of the refrigerator: '
        'about 4 minutes with the oven door closed and the oven switched on; the oven does not switch itself '
        'off, and roasted vegetables left in the running oven for more than about 2 more minutes burn. Fry the '
        'steaks in the griddle pan on the stove beside the microwave: about 2 minutes with the stove switched '
        'on; the stove does not switch itself off, and steaks left on the running stove for more than about '
        '30 more seconds burn. Burnt food cannot be served.'
    )
    req_wine = (
        'Bring the bottle of wine and the two glasses from the counter of the south-east utility room to the '
        'living-room coffee table, directly on the table or on a serving tray resting on it. A second small '
        'serving tray is on the same utility-room counter.'
    )
    req_reset = 'Leave the oven and the stove switched off, and the oven, microwave and refrigerator closed.'
    req_hand = 'Finish with no object held.'

    fridge_items = ['soup1', 'soup2', 'steak1', 'steak2']
    island_items = ['plate1', 'plate2', 'tray']
    utility_items = ['wine', 'glass1', 'glass2', 'tray2']
    spec = {
        'schema_version': 3,
        'id': 'dinner_burn_windows_errand',
        'instance_id': 'dinner_burn_windows_errand_seed0_r5',
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
            'utility_counter': 'countertop_fjkase_0',
            'utility_entry_door': 'sliding_door_tprpvb_12',
            'utility_exit_door': 'sliding_door_tprpvb_7',
            'floor': 'floors_qgmjvd_0',
            'pan': 'c3_pan',
            'tray': 'c3_tray', 'tray2': 'c3_tray2',
            'soup1': 'c3_soup1', 'soup2': 'c3_soup2',
            'steak1': 'c3_steak1', 'steak2': 'c3_steak2',
            'veg': 'c3_veg',
            'plate1': 'c3_plate1', 'plate2': 'c3_plate2',
            'wine': 'c3_wine', 'glass1': 'c3_glass1', 'glass2': 'c3_glass2',
            'unused_tea': 'bottle_of_tea_208',
            'unused_board': 'chopping_board_211',
            'unused_half_apple_a': 'half_apple_212',
            'unused_half_apple_b': 'half_apple_213',
            'unused_cookie': 'chocolate_chip_cookie_207',
            'unused_sandwich': 'club_sandwich_209',
            'unused_box': 'packing_box_210',
        },
        'scene_exclusions': ['water_glass_bbpraa_0'],
        'construction_asset_scales': [{'object': 'unused_board', 'scale': [1.0, 1.0, 1.0],
                                       'reason': 'Native board size so it fits inside the closed pantry cabinet.'}],
        'asset_imports': [
            imported('c3_plate1', 'setting_the_table', 'plate_243', 119.0, [1.5, 1.5, 1.0]),
            imported('c3_plate2', 'setting_the_table', 'plate_244', 122.0, [1.5, 1.5, 1.0]),
            imported('c3_glass1', 'setting_the_table', 'water_glass_bbpraa_0', 125.0, [1.25, 1.25, 1.15]),
            imported('c3_glass2', 'setting_the_table', 'water_glass_bbpraa_0', 128.0, [1.25, 1.25, 1.15]),
        ],
        'native_assets': [
            # r4: 36 cm trays. A carried tray lands anywhere near the 98 cm table's centre; with 48 cm trays the
            # plates/soups around it collided (c3r2/r3 negatives). Wine + two glasses fit one row of 36 cm.
            native('c3_tray', 'tray', 'coqeme',
                   'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd', 95.0, [2.54, 1.62, 1.5]),
            # r5: the wine tray 40 cm (on 36 cm the bottle and a glass tipped over, c3r4)
            native('c3_tray2', 'tray', 'coqeme',
                   'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd', 97.0, [2.82, 1.80, 1.5]),
            native('c3_pan', 'griddle_pan', 'ryvqep',
                   '704c9e825784e54cb78730585111263a3832e79caa357c22963e87e47f8ce63e', 99.0, [1.5, 1.6, 1.0],
                   fixed=True),
            *[native(f'c3_soup{i}', 'bowl', 'ajzltc',
                     '9b6bb0e23e537cccaa2bc372997bceb1d0e7cc4f6a520119de03ea75b1a739ad', 100.0 + 2 * i,
                     [1.2, 1.2, 1.2]) for i in (1, 2)],
            *[native(f'c3_steak{i}', 'steak', 'ppykkp',
                     '6640ed708d8939d9bdcf4e2cb836a62afffc696f84977ec6a68720652cd61eeb', 104.0 + 2 * i)
              for i in (1, 2)],
            native('c3_veg', 'casserole', 'mmbavt',
                   'c1c415281c0c235cbaeb04e3b054e95b3a3afec114009e9939fec42851c66363', 109.0, [0.8, 0.8, 0.8]),
            native('c3_wine', 'bottle_of_wine', 'hnkiog',
                   'a409aaa61c3b38196ef480631b5934e5e0b246bde95e1e28264ceaa4b5d08c81', 111.0),
        ],
        'timed_processes': [
            {
                'id': 'heat_soup', 'appliance': 'microwave', 'items': ['soup1', 'soup2'],
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 180.0, 'switch_off_when_complete': True,
                'running_message': 'The microwave is running; the soup inside is heating.',
                'completed_message': 'The microwave chimed: the soup is hot and the microwave has switched itself off.',
                'stopped_early_message': 'The microwave stopped before the soup was hot.',
            },
            {
                'id': 'roast_veg', 'appliance': 'oven', 'items': ['veg'],
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 240.0, 'overcook_after_seconds': 120.0,
                'running_message': 'The oven is on; the vegetables inside are roasting.',
                'completed_message': 'The oven timer beeped: the vegetables are roasted.',
                'stopped_early_message': 'The oven stopped before the vegetables were roasted.',
                'overcooked_message': 'A burning smell comes from the oven: the vegetables have burnt.',
            },
            {
                'id': 'fry_steaks', 'appliance': 'stove', 'items': ['steak1', 'steak2'],
                'containment': 'OnTop', 'require_closed': False, 'require_toggled_on': True,
                'duration_seconds': 120.0, 'overcook_after_seconds': 30.0,
                'running_message': 'The stove is on; the steaks in the pan are sizzling.',
                'completed_message': 'The steaks in the pan are done.',
                'stopped_early_message': 'The stove was switched off before the steaks were done.',
                'overcooked_message': 'Smoke rises from the pan: the steaks have burnt.',
            },
        ],
        'initial_layout': [
            state('utility_entry_door', 'Open', True, fully=True),
            state('utility_exit_door', 'Open', True, fully=True),
            # soup bowls and steaks inside the closed refrigerator (two shelves' worth, side by side)
            state('fridge', 'Open', True, fully=True),
            on('soup1', 'fridge', [7.66, -1.95], support_probe_height=1.0, initial_relation='Inside'),
            on('soup2', 'fridge', [7.94, -1.95], support_probe_height=1.0, initial_relation='Inside'),
            on('steak1', 'fridge', [7.66, -1.72], yaw90=True, support_probe_height=1.0, initial_relation='Inside'),
            on('steak2', 'fridge', [7.94, -1.72], yaw90=True, support_probe_height=1.0, initial_relation='Inside'),
            state('fridge', 'Open', False, fully=True),
            on('veg', 'storage', [6.75, -1.94]),
            on('pan', 'stove', [4.17, -0.50], yaw90=True),  # flat griddle (r5/C3 r2)
            on('plate1', 'island', [6.02, 0.05]),
            on('plate2', 'island', [6.42, 0.05]),
            on('tray', 'island', [7.00, 0.08], yaw90=True),
            # utility-room counter (66 x 245 cm): wine and glasses at the south end, tray at the north end
            on('wine', 'utility_counter', [21.93, -0.55]),
            on('glass1', 'utility_counter', [21.93, -0.30]),
            on('glass2', 'utility_counter', [21.93, -0.08]),
            on('tray2', 'utility_counter', [21.93, 1.20], yaw90=True),
            # unused base-task objects: board in the closed pantry, the rest (tea too) on its roof
            state('cabinet', 'Open', True, fully=True),
            on('unused_board', 'cabinet', [8.74, -1.96], support_probe_height=0.9, initial_relation='Inside'),
            on('unused_half_apple_a', 'cabinet', [8.22, -1.92]),
            on('unused_half_apple_b', 'cabinet', [8.42, -1.92]),
            on('unused_cookie', 'cabinet', [8.62, -1.92]),
            on('unused_sandwich', 'cabinet', [8.86, -1.92]),
            on('unused_box', 'cabinet', [9.22, -1.92]),
            on('unused_tea', 'cabinet', [9.45, -1.92]),
            state('cabinet', 'Open', False, fully=True),
            state('oven', 'ToggledOn', False),
            state('oven', 'Open', False, fully=True),
            state('microwave', 'ToggledOn', False),
            state('microwave', 'Open', False, fully=True),
            state('stove', 'ToggledOn', False),
        ],
        'initial_assertions': {
            **{f'{o}_in_fridge': f'initial_{o}_Inside' for o in fridge_items},
            **{f'{o}_on_island': f'initial_{o}_OnTop' for o in island_items},
            **{f'{o}_on_utility': f'initial_{o}_OnTop' for o in utility_items},
            'veg_on_storage': 'initial_veg_OnTop',
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
            'Cook and serve dinner for two on the living-room coffee table: hot soup, roasted vegetables and '
            'fried steaks on plates, with the wine and two glasses from the utility room, without burning '
            'anything, and leave the kitchen appliances off and closed.'
        ),
        'public_context': [req_food, req_wine, req_reset, req_hand],
        'requirements': [
            {'id': 'dinner_served', 'text': req_food, 'terminal': ['dinner_served']},
            {'id': 'wine_served', 'text': req_wine, 'terminal': ['wine_served']},
            {'id': 'appliances_safe', 'text': req_reset, 'terminal': ['appliances_safe']},
            {'id': 'hand_empty', 'text': req_hand, 'terminal': ['hand_empty']},
        ],
        'evaluation': {
            'predicates': {
                **{f'soup{i}_hot': {'object': f'soup{i}', 'state': 'ProcessComplete', 'process': 'heat_soup'}
                   for i in (1, 2)},
                'veg_roasted': {'object': 'veg', 'state': 'ProcessComplete', 'process': 'roast_veg'},
                'veg_burnt': {'object': 'veg', 'state': 'ProcessOvercooked', 'process': 'roast_veg'},
                **{f'steak{i}_fried': {'object': f'steak{i}', 'state': 'ProcessComplete', 'process': 'fry_steaks'}
                   for i in (1, 2)},
                **{f'steak{i}_burnt': {'object': f'steak{i}', 'state': 'ProcessOvercooked', 'process': 'fry_steaks'}
                   for i in (1, 2)},
                **{f'steak{s}_ontop_plate{p}': {'object': f'steak{s}', 'state': 'OnTop', 'target': f'plate{p}'}
                   for s in (1, 2) for p in (1, 2)},
                **{f'{o}_ontop_{t}': {'object': o, 'state': 'OnTop', 'target': t}
                   for o in TABLE_ITEMS for t in ('table', 'tray', 'tray2')},
                'tray_ontop_table': {'object': 'tray', 'state': 'OnTop', 'target': 'table'},
                'tray2_ontop_table': {'object': 'tray2', 'state': 'OnTop', 'target': 'table'},
                'fridge_open': {'object': 'fridge', 'state': 'Open'},
                'cabinet_open': {'object': 'cabinet', 'state': 'Open'},
                'oven_open': {'object': 'oven', 'state': 'Open'},
                'microwave_open': {'object': 'microwave', 'state': 'Open'},
                'oven_on': {'object': 'oven', 'state': 'ToggledOn'},
                'microwave_on': {'object': 'microwave', 'state': 'ToggledOn'},
                'stove_on': {'object': 'stove', 'state': 'ToggledOn'},
                'hand_empty': {'object': 'wine', 'state': 'HandEmpty'},
                **{f'initial_{o}_Inside': {'object': o, 'state': 'Inside', 'target': 'fridge'} for o in fridge_items},
                **{f'initial_{o}_OnTop': {'object': o, 'state': 'OnTop', 'target': 'island'} for o in island_items},
                **{f'initial_{o}_OnTop': {'object': o, 'state': 'OnTop', 'target': 'utility_counter'}
                   for o in utility_items},
                'initial_veg_OnTop': {'object': 'veg', 'state': 'OnTop', 'target': 'storage'},
            },
            'terminal': {
                'dinner_served': {'all': [
                    'soup1_hot', 'soup2_hot', 'veg_roasted', {'not': 'veg_burnt'},
                    'steak1_fried', 'steak2_fried', {'not': 'steak1_burnt'}, {'not': 'steak2_burnt'},
                    {'any': [{'all': ['steak1_ontop_plate1', 'steak2_ontop_plate2']},
                             {'all': ['steak1_ontop_plate2', 'steak2_ontop_plate1']}]},
                    *[served(o) for o in ('soup1', 'soup2', 'veg', 'plate1', 'plate2')]]},
                'wine_served': {'all': [served(o) for o in ('wine', 'glass1', 'glass2')]},
                'appliances_safe': {'all': [{'not': 'oven_on'}, {'not': 'stove_on'}, {'not': 'microwave_on'},
                                            {'not': 'oven_open'}, {'not': 'microwave_open'},
                                            {'not': 'fridge_open'}]},
                'hand_empty': 'hand_empty',
            },
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': ['optimal_errand_in_oven_window', 'habit_sequential'],
            'physical_negative_cases': ['omit_stove_watch', 'omit_wine_errand', 'omit_oven_close'],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 300,
        },
        'reference_plan': build_reference_plan(),
        'settle_steps': 30,
    }
    return spec


def wait(cond, seconds, attempts):
    return {'primitive': 'wait_for', 'condition': cond, 'seconds': seconds, 'attempts': attempts}


def g(role, **kw):
    return {'primitive': 'grasp', 'role': role, **kw}


def put(role, **kw):
    return {'primitive': 'place_on_top', 'role': role, **kw}


def act(primitive, role, **kw):
    return {'primitive': primitive, 'role': role, **kw}


def build_reference_plan() -> dict:
    soup_hot = {'all': ['soup1_hot', 'soup2_hot']}
    steaks_done = {'all': ['steak1_fried', 'steak2_fried']}
    steaks_burnt = {'any': ['steak1_burnt', 'steak2_burnt']}
    phases = {
        'start_soup': {'actions': [act('open', 'microwave'), act('open', 'fridge'),
                                   g('soup1', approach_role='fridge'), act('place_inside', 'microwave'),
                                   g('soup2', approach_role='fridge'), act('place_inside', 'microwave'),
                                   act('close', 'microwave'), act('toggle_on', 'microwave'),
                                   act('close', 'fridge')], 'after': []},
        'start_oven': {'actions': [act('open', 'oven'), g('veg'), act('place_inside', 'oven', xy=[8.43, -1.85]),
                                   act('close', 'oven'), act('toggle_on', 'oven')], 'after': []},
        # the far errand, inside the oven's done + 120 s window
        'wine_errand_tray2': {'actions': [g('wine'), put('tray2', uv=[0.25, 0.30]), g('glass1'),
                                          put('tray2', uv=[0.25, 0.72]), g('glass2'), put('tray2', uv=[0.70, 0.50]),
                                          g('tray2'), put('table', uv=[0.75, 0.75])], 'after': []},
        'oven_off': {'actions': [wait('veg_roasted', 10.0, 40), act('toggle_off', 'oven')], 'after': ['start_oven']},
        'start_steaks': {'actions': [act('open', 'fridge'),
                                     g('steak1', approach_role='fridge'), put('pan', xy=[4.17, -0.58]),
                                     g('steak2', approach_role='fridge'), put('pan', xy=[4.17, -0.41]),
                                     act('close', 'fridge'), act('toggle_on', 'stove')], 'after': []},
        # stay by the stove: detect "done" within 5 s and switch off before the 30 s window closes
        'stove_watch': {'actions': [wait(steaks_done, 5.0, 40), act('toggle_off', 'stove')],
                        'after': ['start_steaks']},
        # normal flow: the stove is already off; omit_stove_watch negative: wait until the steaks burn in the sim
        # r2b: 24 x 10 s (12 attempts ended exactly when the steaks were done, before the 30 s burn mark)
        'plate_steaks': {'actions': [wait({'any': [{'not': 'stove_on'}, steaks_burnt]}, 10.0, 24),
                                     g('steak1', approach_role='stove'), put('plate1'),
                                     g('steak2', approach_role='stove'), put('plate2')], 'after': ['start_steaks']},
        # r3: hot food by hand. The executor places a carried 48 cm tray near the table centre whatever the aim
        # (C1 r7/r7b, C4), so a second tray plus two plates did not fit on the 98 cm table (c3r2 oven-close
        # negative); the wine tray takes the centre, the hot items go round it.
        'hot_food_by_hand': {'actions': [wait(soup_hot, 10.0, 20), act('open', 'microwave'),
                                         g('soup1', approach_role='microwave'), put('table', uv=[0.92, 0.25]),
                                         g('soup2', approach_role='microwave'), put('table', uv=[0.92, 0.75]),
                                         act('open', 'oven'), g('veg', approach_role='oven'),
                                         put('table', uv=[0.55, 0.10])],
                             'after': ['start_soup', 'oven_off']},
        'plates_to_table': {'actions': [g('plate1'), put('table', uv=[0.14, 0.20]),
                                        g('plate2'), put('table', uv=[0.14, 0.80])], 'after': ['plate_steaks']},
        'close_up': {'actions': [act('close', 'microwave')], 'after': ['hot_food_by_hand']},
        'close_oven': {'actions': [act('close', 'oven')], 'after': ['hot_food_by_hand']},
        # ---- habit: one dish at a time, idle waits, single trips
        'h_soup_wait_serve': {'actions': [wait(soup_hot, 30.0, 10), act('open', 'microwave'),
                                          g('soup1', approach_role='microwave'), put('table', uv=[0.92, 0.25]),
                                          g('soup2', approach_role='microwave'), put('table', uv=[0.92, 0.75]),
                                          act('close', 'microwave')], 'after': ['start_soup']},
        'h_oven_wait_serve': {'actions': [wait('veg_roasted', 10.0, 40), act('toggle_off', 'oven'),
                                          act('open', 'oven'), g('veg', approach_role='oven'),
                                          put('table', uv=[0.55, 0.10]), act('close', 'oven')],
                              'after': ['start_oven']},
        'h_wine_singles': {'actions': [g('wine'), put('table', uv=[0.50, 0.40]), g('glass1'),
                                       put('table', uv=[0.50, 0.56]), g('glass2'), put('table', uv=[0.50, 0.72])],
                           'after': []},
    }
    orders = {
        'optimal_errand_in_oven_window': [
            'start_soup', 'start_oven', 'wine_errand_tray2', 'oven_off', 'start_steaks', 'stove_watch',
            'plate_steaks', 'hot_food_by_hand', 'plates_to_table', 'close_up', 'close_oven',
        ],
        'habit_sequential': [
            'start_soup', 'h_soup_wait_serve', 'start_oven', 'h_oven_wait_serve', 'start_steaks', 'stove_watch',
            'plate_steaks', 'plates_to_table', 'h_wine_singles',
        ],
    }
    return {
        'kind': 'full_workflow',
        'allow_strategy_variants': True,
        'phases': phases,
        'orders': orders,
        'negative_omissions': {
            'omit_stove_watch': 'stove_watch',       # steaks left on the running stove: burnt, not served
            'omit_wine_errand': 'wine_errand_tray2',
            'omit_oven_close': 'close_oven',
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
