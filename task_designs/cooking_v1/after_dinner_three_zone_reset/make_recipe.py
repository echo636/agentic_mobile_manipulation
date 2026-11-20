#!/usr/bin/env python3
"""Generate recipe.json for custom task `after_dinner_three_zone_reset` (C4, benchmark side).

Task: 饭后三区收尾 —— 洗碗机 300 s（自动停）+ 洗衣机 240 s（自动停）+ 烤箱热派 180 s（热好后继续开着 >120 s 会糊）
Base scene: `preparing_lunch_box` instance 301, seed 0 (`house_single_floor`), same base as T1.

Paper design and numbers: experiments/cooking_parallel_tasks_20261120/README.md (C4) and tasks.py
(habit 1668 s / 314 m, optimal 933 s / 96 m, pie-first trap burns the pie).

Planning structure (not motor fussiness):
  - pickups in three zones (living-room coffee table / utility room / children's-bedroom nightstand,
    island -> nightstand 27.6 m A*), the dishwasher can only start after the last dish is inside;
  - the washer can run while the robot goes north for the mugs;
  - the pie has the shortest burn window, so it should go in last, while the dishwasher runs;
  - a 48 x 48 cm tray batches 6 living-room items and later the 2 mugs.

Benchmark-side rules used (all tiers): timed_processes v4 (overcook window for the oven,
switch_off_when_complete for the dishwasher and the washer: real machines end their programme).
Public text states the appliance facts (cycle lengths, self-stopping, the pie's burn window) as a person
who owns these appliances knows them; it never prescribes order, batching or tray use.

Criteria self-check (CLAUDE.md five criteria):
  1. Feasible: rigid assets already used in T1/T5/T6 (tray coqeme x[3.38,2.16,1.5], bowl ajzltc x1.3,
     mug ppdqbj, water glass bbpraa x[1.25,1.25,1.15], rag oocmed); new: apple_pie ejrgdj x1.4,
     first use of dishwasher_ynrhuk_0 and the built-in oven_ffitak_0 -> to be measured in the build.
  2. No ambiguity: all six dishes go to the same dishwasher, both napkins to the same washer; the lamp
     on the bedroom nightstand is excluded so the tray fits; unused base-task objects are tucked away
     as in T1 (cabinet roof) or inside the closed fridge.
  3. Difficulty = start order, burn window, zone route and tray batching.
  4./5. Savings are what a person does: load the slow machines first, run errands while they run, use
     a tray, put the pie in last and stay close to the oven.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / 'src'))
from manipulation_agent.tasks.custom_tasks import validate_spec  # noqa: E402

# r4: glasses dropped; 6 dishes crowded the 57 x 53 cm dishwasher and the last one fell out (r3b napkin negative)
DISHES = ['bowl1', 'bowl2', 'mug1', 'mug2']
NAPKINS = ['napkin1', 'napkin2']
TRAY_SLOTS = {  # tray-local uv, 3 x 2 grid on the 48 x 48 cm tray
    'bowl1': [0.27, 0.27], 'bowl2': [0.27, 0.73], 'napkin1': [0.73, 0.27], 'napkin2': [0.73, 0.73],
    'mug1': [0.73, 0.27], 'mug2': [0.73, 0.73],
}

STAGE_SHA_SETTING = '9a85e78695003b54e9486555a4d50830c477ddc81550fd2fdf4640923ea09b66'


def native(name, category, model, sha, x, scale=None):
    row = {'name': name, 'category': category, 'model': model, 'usd_sha256': sha, 'prim_type': 0,
           'position': [x, 50.0, 2.0], 'orientation': [0.0, 0.0, 0.0, 1.0]}
    if scale is not None:
        row['scale'] = scale
    return row


def on(obj, target, xy, yaw90=False):
    row = {'object': obj, 'target': target, 'place_on_support': True, 'xy': xy}
    row['orientation'] = [0.0, 0.0, 0.70710678, 0.70710678] if yaw90 else [0.0, 0.0, 0.0, 1.0]
    return row


def state(obj, name, value, fully=False):
    row = {'object': obj, 'state': name, 'value': value}
    if fully:
        row['kwargs'] = {'fully': True}
    return row


def served(item):
    return {'any': [f'{item}_ontop_table', {'all': [f'{item}_ontop_tray', 'tray_ontop_table']}]}


def build_recipe() -> dict:
    req_dishes = (
        'Wash all four used dishes in the kitchen dishwasher until its wash cycle has finished: the two bowls '
        'on the living-room coffee table, and the two mugs on the low nightstand '
        "in the children's bedroom (the north-east bedroom with the single bed, off the long east "
        'corridor). The dishwasher is built in under the counter between the sink and the refrigerator. '
        'A wash cycle takes about 5 minutes once the dishwasher door is closed and it is switched on; it '
        'switches itself off when the cycle ends, and only the dishes inside during that cycle are washed. '
        'The dishes may stay in the dishwasher afterwards.'
    )
    req_napkins = (
        'Wash both cloth napkins from the living-room coffee table in the white washing machine under the '
        'counter in the south-east utility room until its wash cycle has finished. A cycle takes about '
        '4 minutes once the door is closed and it is switched on, and the machine switches itself off at '
        'the end. The napkins may stay in the washing machine afterwards.'
    )
    req_pie = (
        'Warm the apple pie from the kitchen island in the black built-in oven to the right of the '
        'refrigerator and serve it on the living-room coffee table (directly on the table or on the '
        'serving tray resting on the table). Warming takes about 3 minutes with the oven door closed and '
        'the oven switched on. The oven does not switch itself off: if the warm pie stays in the running '
        'oven for more than about 2 more minutes it burns, and a burnt pie cannot be served.'
    )
    req_reset = (
        'Leave the oven switched off and the oven, dishwasher and washing machine doors closed. A '
        '48 x 48 cm serving tray is available on the kitchen island.'
    )
    req_hand = 'Finish with no object held.'

    spec = {
        'schema_version': 3,
        'id': 'after_dinner_three_zone_reset',
        'instance_id': 'after_dinner_three_zone_reset_seed0_r4',
        'status': 'complete_recipe_physics_pending',
        'benchmark_ready': False,
        'official_submission_eligible': False,
        'base': {'task': 'preparing_lunch_box', 'instance': 301, 'seed': 0},
        'roles': {
            # kitchen
            'island': 'bar_udatjt_0',
            'storage': 'countertop_kelzer_0',
            'fridge': 'fridge_dszchb_0',
            'oven': 'oven_ffitak_0',
            'dishwasher': 'c4_dishwasher',
            'kitchen_floor': 'floors_kxcpgy_0',
            'cabinet': 'bottom_cabinet_fancyy_0',
            'floor': 'floors_qgmjvd_0',
            # living room
            'table': 'coffee_table_rlsebe_0',
            # utility room
            'utility_counter': 'countertop_fjkase_0',
            'washer': 'washer_ynwamu_0',
            # children's bedroom (childs_room_1)
            'nightstand': 'nightstand_wbxekb_2',
            'bed': 'bed_rrcvaq_1',
            # doors on the routes
            'utility_entry_door': 'sliding_door_tprpvb_12',
            'utility_exit_door': 'sliding_door_tprpvb_7',
            'bedroom_door': 'sliding_door_tprpvb_1',
            # task items
            'tray': 'c4_tray',
            'pie': 'c4_pie',
            'bowl1': 'c4_bowl1',
            'bowl2': 'c4_bowl2',
            'mug1': 'c4_mug1',
            'mug2': 'c4_mug2',
            'napkin1': 'c4_napkin1',
            'napkin2': 'c4_napkin2',
            # unused base-task objects (preparing_lunch_box BDDL scope)
            'unused_tea': 'bottle_of_tea_208',
            'unused_board': 'chopping_board_211',
            'unused_half_apple_a': 'half_apple_212',
            'unused_half_apple_b': 'half_apple_213',
            'unused_cookie': 'chocolate_chip_cookie_207',
            'unused_sandwich': 'club_sandwich_209',
            'unused_box': 'packing_box_210',
        },
        'scene_exclusions': [
            'water_glass_bbpraa_0',      # duplicate glass (as in T1)
            'table_lamp_uupwiq_0',       # frees the bedroom nightstand for the tray
            # r2: the scene dishwasher's wire racks let released dishes slide out over the open door
            # (c4ref_habit r1b: 4 of 6 dishes ended on the floor); replaced in place by a rack-less model
            'dishwasher_ynrhuk_0',
        ],
        'construction_asset_scales': [],
        'asset_imports': [],
        'native_assets': [
            {'name': 'c4_dishwasher', 'category': 'dishwasher', 'model': 'dugpby',
             'usd_sha256': 'b3b9bff0c97770c4f25dc43b421bc6d93b447180d6347e755d2ca6552ea80dee', 'prim_type': 0,
             'fixed_base': True, 'scale': [1.05, 1.08, 1.2], 'position': [92.0, 50.0, 2.0],
             'orientation': [0.0, 0.0, 0.70710678, 0.70710678]},
            native('c4_tray', 'tray', 'coqeme',
                   'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd', 95.0, [3.38, 2.16, 1.5]),
            native('c4_pie', 'apple_pie', 'ejrgdj',
                   '7aac29938fc20a2163025b318bc37b916ed3aeb91f765bca7e9a8143582d6e30', 98.0, [1.4, 1.4, 1.4]),
            native('c4_bowl1', 'bowl', 'ajzltc',
                   '9b6bb0e23e537cccaa2bc372997bceb1d0e7cc4f6a520119de03ea75b1a739ad', 101.0, [1.3, 1.3, 1.3]),
            native('c4_bowl2', 'bowl', 'ajzltc',
                   '9b6bb0e23e537cccaa2bc372997bceb1d0e7cc4f6a520119de03ea75b1a739ad', 104.0, [1.3, 1.3, 1.3]),
            native('c4_mug1', 'mug', 'ppdqbj',
                   '13d62119f1ef0d3e44f105335d2a967b98e727f2ebf02d5e6524035767b45efa', 107.0),
            native('c4_mug2', 'mug', 'ppdqbj',
                   '13d62119f1ef0d3e44f105335d2a967b98e727f2ebf02d5e6524035767b45efa', 110.0),
            native('c4_napkin1', 'rag', 'oocmed',
                   '732677fea4ca1e96c8b71f843249e2b5740287542aae48245871d7a6daeb7068', 113.0, [0.5, 0.5, 10.0]),
            native('c4_napkin2', 'rag', 'oocmed',
                   '732677fea4ca1e96c8b71f843249e2b5740287542aae48245871d7a6daeb7068', 116.0, [0.5, 0.5, 10.0]),
        ],
        'timed_processes': [
            {
                'id': 'wash_dishes',
                'appliance': 'dishwasher',
                'items': list(DISHES),
                'containment': 'Inside',
                'require_closed': True,
                'require_toggled_on': True,
                'duration_seconds': 300.0,
                'switch_off_when_complete': True,
                'running_message': 'The dishwasher is running a wash cycle.',
                'completed_message': 'The dishwasher beeped: the wash cycle is finished and it has switched itself off.',
                'stopped_early_message': 'The dishwasher stopped before the wash cycle finished.',
            },
            {
                'id': 'wash_napkins',
                'appliance': 'washer',
                'items': list(NAPKINS),
                'containment': 'Inside',
                'require_closed': True,
                'require_toggled_on': True,
                'duration_seconds': 240.0,
                'switch_off_when_complete': True,
                'running_message': 'The washing machine is running a wash cycle.',
                'completed_message': 'The washing machine beeped: the wash cycle is finished and it has switched itself off.',
                'stopped_early_message': 'The washing machine stopped before the wash cycle finished.',
            },
            {
                'id': 'warm_pie',
                'appliance': 'oven',
                'items': ['pie'],
                'containment': 'Inside',
                'require_closed': True,
                'require_toggled_on': True,
                'duration_seconds': 180.0,
                'overcook_after_seconds': 120.0,
                'running_message': 'The oven is on; the pie inside is warming.',
                'completed_message': 'The oven timer beeped: the pie is warm.',
                'stopped_early_message': 'The oven stopped before the pie was warm.',
                'overcooked_message': 'A burning smell comes from the oven: the apple pie has burnt.',
            },
        ],
        'initial_layout': [
            # doors on the routes open, appliances closed and off
            state('utility_entry_door', 'Open', True, fully=True),
            state('utility_exit_door', 'Open', True, fully=True),
            state('bedroom_door', 'Open', True, fully=True),
            # unused base-task objects: tea inside the closed fridge, the board inside the closed pantry
            # cabinet (r0 put the 37 cm board in the fridge and it kept the door from closing), the rest
            # on the cabinet roof as in T1
            state('fridge', 'Open', True, fully=True),
            {'object': 'unused_tea', 'target': 'fridge', 'place_on_support': True, 'xy': [7.82, -1.85],
             'support_probe_height': 1.0, 'initial_relation': 'Inside'},
            state('fridge', 'Open', False, fully=True),
            state('cabinet', 'Open', True, fully=True),
            {'object': 'unused_board', 'target': 'cabinet', 'place_on_support': True, 'xy': [8.74, -1.96],
             'support_probe_height': 0.9, 'initial_relation': 'Inside', 'orientation': [0.0, 0.0, 0.0, 1.0]},
            on('unused_half_apple_a', 'cabinet', [8.22, -1.92]),
            on('unused_half_apple_b', 'cabinet', [8.42, -1.92]),
            on('unused_cookie', 'cabinet', [8.62, -1.92]),
            on('unused_sandwich', 'cabinet', [8.86, -1.92]),
            on('unused_box', 'cabinet', [9.22, -1.92]),
            state('cabinet', 'Open', False, fully=True),
            # kitchen island: pie and tray in plain view
            on('pie', 'island', [6.30, 0.05]),
            on('tray', 'island', [7.00, 0.08], yaw90=True),
            # living-room coffee table (98 x 98 cm): west half used, east half free
            on('bowl1', 'table', [12.22, 0.30]),
            on('bowl2', 'table', [12.22, 0.61]),
            on('napkin1', 'table', [12.47, 0.30]),
            on('napkin2', 'table', [12.47, 0.61]),
            # children's-bedroom nightstand (48 x 78 cm, lamp removed): mugs at the north end
            on('mug1', 'nightstand', [20.44, 14.14]),
            on('mug2', 'nightstand', [20.66, 14.14]),
            # appliances (rack-less dishwasher in the original under-counter slot)
            # probe from below the counter: from above the ray hits the wall shelf (r3 build)
            {'object': 'dishwasher', 'target': 'kitchen_floor', 'place_on_support': True, 'xy': [7.205, -1.955],
             'support_probe_height': 0.5, 'orientation': [0.0, 0.0, 0.70710678, 0.70710678]},
            state('dishwasher', 'ToggledOn', False),
            state('dishwasher', 'Open', False, fully=True),
            state('washer', 'ToggledOn', False),
            state('washer', 'Open', False, fully=True),
            state('oven', 'ToggledOn', False),
            state('oven', 'Open', False, fully=True),
        ],
        'initial_assertions': {
            **{f'{d}_initial': f'initial_{d}_OnTop' for d in DISHES + NAPKINS},
            'pie_on_island': 'initial_pie_OnTop',
            'tray_on_island': 'initial_tray_OnTop',
            'dishwasher_closed': {'not': 'dishwasher_open'},
            'washer_closed': {'not': 'washer_open'},
            'oven_closed': {'not': 'oven_open'},
            'oven_off': {'not': 'oven_on'},
            'dishwasher_off': {'not': 'dishwasher_on'},
            'washer_off': {'not': 'washer_on'},
            'fridge_closed': {'not': 'fridge_open'},
            'hand_empty': 'hand_empty',
        },
        'future_roles': [],
        'replacements': [],
        'public_instruction': (
            'After dinner, wash the used dishes and the cloth napkins, warm the apple pie and serve it on the '
            'living-room coffee table, and leave the appliances off and closed.'
        ),
        'public_context': [req_dishes, req_napkins, req_pie, req_reset, req_hand],
        'requirements': [
            {'id': 'dishes_washed', 'text': req_dishes, 'terminal': ['dishes_washed']},
            {'id': 'napkins_washed', 'text': req_napkins, 'terminal': ['napkins_washed']},
            {'id': 'pie_served', 'text': req_pie, 'terminal': ['pie_served']},
            {'id': 'appliances_safe', 'text': req_reset, 'terminal': ['appliances_safe']},
            {'id': 'hand_empty', 'text': req_hand, 'terminal': ['hand_empty']},
        ],
        'evaluation': {
            'predicates': {
                **{f'{d}_washed': {'object': d, 'state': 'ProcessComplete', 'process': 'wash_dishes'}
                   for d in DISHES},
                **{f'{n}_washed': {'object': n, 'state': 'ProcessComplete', 'process': 'wash_napkins'}
                   for n in NAPKINS},
                'pie_warmed': {'object': 'pie', 'state': 'ProcessComplete', 'process': 'warm_pie'},
                'pie_overcooked': {'object': 'pie', 'state': 'ProcessOvercooked', 'process': 'warm_pie'},
                'pie_ontop_table': {'object': 'pie', 'state': 'OnTop', 'target': 'table'},
                'pie_ontop_tray': {'object': 'pie', 'state': 'OnTop', 'target': 'tray'},
                'tray_ontop_table': {'object': 'tray', 'state': 'OnTop', 'target': 'table'},
                **{f'{d}_inside_dishwasher': {'object': d, 'state': 'Inside', 'target': 'dishwasher'}
                   for d in DISHES},
                **{f'{n}_inside_washer': {'object': n, 'state': 'Inside', 'target': 'washer'} for n in NAPKINS},
                **{f'{i}_ontop_tray': {'object': i, 'state': 'OnTop', 'target': 'tray'} for i in DISHES + NAPKINS},
                'dishwasher_open': {'object': 'dishwasher', 'state': 'Open'},
                'washer_open': {'object': 'washer', 'state': 'Open'},
                'oven_open': {'object': 'oven', 'state': 'Open'},
                'fridge_open': {'object': 'fridge', 'state': 'Open'},
                'oven_on': {'object': 'oven', 'state': 'ToggledOn'},
                'dishwasher_on': {'object': 'dishwasher', 'state': 'ToggledOn'},
                'washer_on': {'object': 'washer', 'state': 'ToggledOn'},
                'hand_empty': {'object': 'pie', 'state': 'HandEmpty'},
                # initial-only verification predicates
                **{f'initial_{d}_OnTop': {'object': d, 'state': 'OnTop', 'target': 'table'}
                   for d in ['bowl1', 'bowl2'] + NAPKINS},
                **{f'initial_{m}_OnTop': {'object': m, 'state': 'OnTop', 'target': 'nightstand'}
                   for m in ['mug1', 'mug2']},
                'initial_pie_OnTop': {'object': 'pie', 'state': 'OnTop', 'target': 'island'},
                'initial_tray_OnTop': {'object': 'tray', 'state': 'OnTop', 'target': 'island'},
            },
            'terminal': {
                'dishes_washed': {'all': [f'{d}_washed' for d in DISHES]},
                'napkins_washed': {'all': [f'{n}_washed' for n in NAPKINS]},
                'pie_served': {'all': ['pie_warmed', {'not': 'pie_overcooked'}, served('pie')]},
                'appliances_safe': {'all': [{'not': 'oven_on'}, {'not': 'oven_open'},
                                            {'not': 'dishwasher_open'}, {'not': 'washer_open'},
                                            {'not': 'fridge_open'}]},
                'hand_empty': 'hand_empty',
            },
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': ['optimal_tray_late_pie', 'habit_sequential'],
            'physical_negative_cases': ['omit_pie_unload', 'omit_napkin_wash', 'omit_oven_close'],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 300,
        },
        'reference_plan': build_reference_plan(),
        'settle_steps': 30,
    }
    return spec


def build_reference_plan() -> dict:
    living = ['bowl1', 'bowl2', 'napkin1', 'napkin2']
    dishes_washed = {'all': [f'{d}_washed' for d in DISHES]}
    phases = {
        'open_dishwasher': {'actions': [{'primitive': 'open', 'role': 'dishwasher'}], 'after': []},
        # ---- optimal: one tray round, washer on the way, mugs, then dishwasher, pie last
        'tray_to_living_table': {
            'actions': [{'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'table', 'xy': [12.84, 0.61]}],
            'after': [],
        },
        'load_tray_living': {
            'actions': [a for item in living for a in (
                {'primitive': 'grasp', 'role': item},
                {'primitive': 'place_on_top', 'role': 'tray', 'uv': TRAY_SLOTS[item]})],
            'after': ['tray_to_living_table'],
        },
        'tray_to_utility': {
            'actions': [{'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'utility_counter', 'xy': [21.95, 1.20]}],
            'after': ['load_tray_living'],
        },
        'napkins_into_washer_from_tray': {
            'actions': [{'primitive': 'open', 'role': 'washer'},
                        {'primitive': 'grasp', 'role': 'napkin1'},
                        {'primitive': 'place_inside', 'role': 'washer'},
                        {'primitive': 'grasp', 'role': 'napkin2'},
                        {'primitive': 'place_inside', 'role': 'washer'}],
            'after': ['tray_to_utility'],
        },
        'start_washer': {
            'actions': [{'primitive': 'close', 'role': 'washer'},
                        {'primitive': 'toggle_on', 'role': 'washer'}],
            'after': ['napkins_into_washer_from_tray'],
        },
        'collect_mugs_on_tray': {
            'actions': [{'primitive': 'grasp', 'role': 'tray'},
                        # r3: the 48 cm nightstand next to the bed left tray items touching the bed and
                        # they stayed behind when the tray was lifted; the bed top is wide and free
                        {'primitive': 'place_on_top', 'role': 'bed', 'xy': [21.25, 13.45]},
                        {'primitive': 'grasp', 'role': 'mug1'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': TRAY_SLOTS['mug1']},
                        {'primitive': 'grasp', 'role': 'mug2'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': TRAY_SLOTS['mug2']}],
            'after': ['napkins_into_washer_from_tray'],
        },
        'tray_to_kitchen': {
            'actions': [{'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'storage', 'xy': [6.30, -1.95]}],
            'after': ['collect_mugs_on_tray'],
        },
        'load_dishwasher_from_tray': {
            'actions': [a for d in DISHES for a in (
                {'primitive': 'grasp', 'role': d},
                {'primitive': 'place_inside', 'role': 'dishwasher'})]
                       + [{'primitive': 'close', 'role': 'dishwasher'},
                          {'primitive': 'toggle_on', 'role': 'dishwasher'}],
            'after': ['open_dishwasher', 'tray_to_kitchen'],
        },
        # ---- habit: instruction order, one item per trip, idle waits
        'dishes_single_trips': {
            'actions': [a for d in DISHES for a in (
                {'primitive': 'grasp', 'role': d},
                {'primitive': 'place_inside', 'role': 'dishwasher'})],
            'after': ['open_dishwasher'],
        },
        'start_dishwasher': {
            'actions': [{'primitive': 'close', 'role': 'dishwasher'},
                        {'primitive': 'toggle_on', 'role': 'dishwasher'}],
            'after': ['dishes_single_trips'],
        },
        'idle_wait_dishwasher': {
            'actions': [{'primitive': 'wait_for', 'condition': {'all': [f'{d}_washed' for d in DISHES]},
                         'seconds': 60.0, 'attempts': 8}],
            'after': ['start_dishwasher'],
        },
        'napkins_single_trips': {
            'actions': [{'primitive': 'open', 'role': 'washer'},
                        {'primitive': 'grasp', 'role': 'napkin1'},
                        {'primitive': 'place_inside', 'role': 'washer'},
                        {'primitive': 'grasp', 'role': 'napkin2'},
                        {'primitive': 'place_inside', 'role': 'washer'},
                        {'primitive': 'close', 'role': 'washer'},
                        {'primitive': 'toggle_on', 'role': 'washer'}],
            'after': [],
        },
        'idle_wait_washer': {
            'actions': [{'primitive': 'wait_for', 'condition': {'all': [f'{n}_washed' for n in NAPKINS]},
                         'seconds': 60.0, 'attempts': 8}],
            'after': ['napkins_single_trips'],
        },
        # ---- shared
        'start_pie': {
            'actions': [{'primitive': 'open', 'role': 'oven'},
                        {'primitive': 'grasp', 'role': 'pie'},
                        {'primitive': 'place_inside', 'role': 'oven'},
                        {'primitive': 'close', 'role': 'oven'},
                        {'primitive': 'toggle_on', 'role': 'oven'}],
            'after': [],
        },
        'unload_and_serve_pie': {
            'actions': [{'primitive': 'wait_for', 'condition': 'pie_warmed', 'seconds': 20.0, 'attempts': 15},
                        {'primitive': 'toggle_off', 'role': 'oven'},
                        {'primitive': 'open', 'role': 'oven'},
                        {'primitive': 'grasp', 'role': 'pie', 'approach_role': 'oven'},
                        {'primitive': 'place_on_top', 'role': 'table', 'xy': [12.40, 0.61]}],
            'after': ['start_pie'],
        },
        'close_oven': {
            'actions': [{'primitive': 'close', 'role': 'oven'}],
            'after': ['unload_and_serve_pie'],
        },
        # dishes only (the omit_napkin_wash negative must still run to its end); r3c: if the pie was never
        # taken out, keep waiting until it burns, so the omit_pie_unload negative exercises the overcook
        # rule in the simulator (r3b: that negative ended after 250 s of oven time, before the 300 s mark).
        # Equivalent to the r3b condition whenever the pie has been served.
        'final_wait_dishes_washed': {
            'actions': [{'primitive': 'wait_for',
                         'condition': {'any': ['pie_overcooked', {'all': [dishes_washed, served('pie')]}]},
                         'seconds': 30.0, 'attempts': 20}],
            'after': [],
        },
    }
    orders = {
        'optimal_tray_late_pie': [
            'tray_to_living_table', 'load_tray_living', 'tray_to_utility',
            'napkins_into_washer_from_tray', 'start_washer', 'collect_mugs_on_tray', 'tray_to_kitchen',
            # r3: open right before loading (r1b: opened at the start, found closed after the tour)
            'open_dishwasher', 'load_dishwasher_from_tray',
            'start_pie', 'unload_and_serve_pie', 'close_oven', 'final_wait_dishes_washed',
        ],
        'habit_sequential': [
            'open_dishwasher', 'dishes_single_trips', 'start_dishwasher', 'idle_wait_dishwasher',
            'napkins_single_trips', 'idle_wait_washer', 'start_pie', 'unload_and_serve_pie',
            'close_oven', 'final_wait_dishes_washed',
        ],
    }
    return {
        'kind': 'full_workflow',
        'allow_strategy_variants': True,
        'phases': phases,
        'orders': orders,
        'negative_omissions': {
            'omit_pie_unload': 'unload_and_serve_pie',      # pie stays in the running oven: burns, not served
            'omit_napkin_wash': 'start_washer',              # napkins in the washer, never washed
            'omit_oven_close': 'close_oven',                 # oven door left open
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
