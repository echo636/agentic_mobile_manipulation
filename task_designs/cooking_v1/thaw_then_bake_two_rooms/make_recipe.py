#!/usr/bin/env python3
"""Generate recipe.json for custom task `thaw_then_bake_two_rooms` (C2, benchmark side).

Task: 先解冻、后烘烤的两道菜，送往两个地方 —— 千层面（解冻 120 s → 烤 600 s）、鱼（解冻 360 s → 烤 240 s）；
微波炉一次只放一个烤盘，烤箱可同时放两个；鱼 + 叉子 + 茶送到儿童房床头柜，千层面 + 两个盘子 + 两把叉子送到客厅茶几。
Base scene: `preparing_lunch_box` instance 301, seed 0 (`house_single_floor`), same base as T1/C1/C4.

Paper design and numbers: experiments/cooking_parallel_tasks_20261120/README.md (C2) and tasks.py
(habit 2393 s / 314 m, defrost-order trap 1515 s, optimal 1161 s / 229 m: -51.5 % time, -27 % route; the route is
dominated by the necessary trips north, so C2 is accepted on time like C1).

Planning structure: a two-machine flow shop (microwave -> oven) with Johnson's rule (short-thaw / long-bake dish
first; the instruction mentions the fish first, so following the text order is the slow order), plus two
destinations (living room 8 m, children's bedroom 27.6 m) and an optional tray.
Benchmark-side rules (all tiers): timed_processes v5 (`requires_completed`: a frozen dish does not bake;
`appliance_capacities`: the microwave takes one baking dish), v4 auto-off for the microwave.
Assets: two casseroles (rectangular mmbavt x0.8 = 18.6 x 25.7 cm, round ncbsee x1.0 = 24.5 cm), told apart by shape.
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
# ffitak's racks are 41 x 22 cm: dishes must be >= ~24 cm deep or wide to rest on them (C1 r1/r2 lesson)
OVEN_AIM = {'lasagna': [8.30, -1.85], 'fish': [8.57, -1.85]}
DISHES = ['lasagna', 'fish']


def native(name, category, model, sha, x, scale=None):
    row = {'name': name, 'category': category, 'model': model, 'usd_sha256': sha, 'prim_type': 0,
           'position': [x, 50.0, 2.0], 'orientation': [0.0, 0.0, 0.0, 1.0]}
    if scale is not None:
        row['scale'] = scale
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


FORKS = ['fork1', 'fork2', 'fork3']
FORK_PAIRS = [('fork1', 'fork2'), ('fork1', 'fork3'), ('fork2', 'fork3')]


def served(item, dest):
    return {'any': [f'{item}_ontop_{dest}', {'all': [f'{item}_ontop_tray', f'tray_ontop_{dest}']}]}


def build_recipe() -> dict:
    req_cook = (
        'Two frozen dishes are inside the refrigerator: a fish casserole in the round baking dish and a '
        'lasagna in the rectangular baking dish. Each must first be thawed in the microwave on the west '
        'preparation counter and then baked in the black built-in oven to the right of the refrigerator; '
        'a dish that is still frozen does not bake in the oven. The fish casserole thaws in about 6 minutes '
        'and then bakes in about 4 minutes; the lasagna thaws in about 2 minutes and then bakes in about '
        '10 minutes. Thawing and baking only progress while the appliance door is closed and the appliance '
        'is switched on. The microwave takes only one baking dish at a time and switches itself off when it '
        'is done; the oven holds both dishes at once and does not switch itself off.'
    )
    req_fish = (
        "Serve the baked fish casserole with one fork and the bottle of tea on the single bed in the "
        "children's bedroom whose sliding door stands open (off the long east corridor; the doors of the other "
        "bedrooms are closed), for the person resting there: directly on the bed or on the serving tray "
        "resting on it."
    )
    req_lasagna = (
        'Serve the baked lasagna with two plates and two forks on the living-room coffee table (the low '
        'square table in front of the sofas east of the kitchen): directly on the table or on the serving '
        'tray resting on it. The plates, the three forks, the bottle of tea and a 48 x 48 cm serving tray '
        'start on the kitchen island.'
    )
    req_reset = 'Leave the oven and the microwave switched off, and the oven, microwave and refrigerator closed.'
    req_hand = 'Finish with no object held.'

    living = ['plate1', 'plate2', 'fork1', 'fork2']
    north = ['fork3', 'tea']
    island_items = living + north + ['tray']
    spec = {
        'schema_version': 3,
        'id': 'thaw_then_bake_two_rooms',
        'instance_id': 'thaw_then_bake_two_rooms_seed0_r4',
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
            'cabinet': 'bottom_cabinet_fancyy_0',
            'table': 'coffee_table_rlsebe_0',
            'nightstand': 'nightstand_wbxekb_2',
            'bed': 'bed_rrcvaq_1',
            'bedroom_door': 'sliding_door_tprpvb_1',
            'floor': 'floors_qgmjvd_0',
            'tea': 'bottle_of_tea_208',
            'tray': 'c2_tray',
            'lasagna': 'c2_lasagna', 'fish': 'c2_fish',
            'plate1': 'c2_plate1', 'plate2': 'c2_plate2',
            'fork1': 'c2_fork1', 'fork2': 'c2_fork2', 'fork3': 'c2_fork3',
            'unused_board': 'chopping_board_211',
            'unused_half_apple_a': 'half_apple_212',
            'unused_half_apple_b': 'half_apple_213',
            'unused_cookie': 'chocolate_chip_cookie_207',
            'unused_sandwich': 'club_sandwich_209',
            'unused_box': 'packing_box_210',
        },
        'scene_exclusions': ['water_glass_bbpraa_0', 'table_lamp_uupwiq_0'],
        # T1 practice: native 25 x 37 cm board (the scene copy is ~52 x 75 cm and fell out of the pantry, c1 r0)
        'construction_asset_scales': [{'object': 'unused_board', 'scale': [1.0, 1.0, 1.0],
                                       'reason': 'Native board size so it fits inside the closed pantry cabinet.'}],
        'asset_imports': [
            imported('c2_plate1', 'setting_the_table', 'plate_243', 119.0, [1.5, 1.5, 1.0]),
            imported('c2_plate2', 'setting_the_table', 'plate_244', 122.0, [1.5, 1.5, 1.0]),
            imported('c2_fork1', 'setting_the_table', 'tablefork_247', 125.0, [0.85, 2.2, 1.5]),
            imported('c2_fork2', 'setting_the_table', 'tablefork_248', 128.0, [0.85, 2.2, 1.5]),
            imported('c2_fork3', 'setting_the_table', 'tablefork_247', 131.0, [0.85, 2.2, 1.5]),
        ],
        'native_assets': [
            native('c2_tray', 'tray', 'coqeme',
                   'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd', 95.0, [3.38, 2.16, 1.5]),
            native('c2_lasagna', 'casserole', 'mmbavt',
                   'c1c415281c0c235cbaeb04e3b054e95b3a3afec114009e9939fec42851c66363', 98.0, [0.8, 0.8, 0.8]),
            native('c2_fish', 'casserole', 'ncbsee',
                   '14c40dfabcac6ca15fe71623625e7201deac519c2e0082e3727504b735475824', 101.0, [1.0, 1.0, 0.8]),
        ],
        'timed_processes': [
            {
                'id': 'thaw', 'appliance': 'microwave', 'items': ['lasagna'],
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 120.0, 'switch_off_when_complete': True,
                'running_message': 'The microwave is running; the lasagna inside is thawing.',
                'completed_message': 'The microwave chimed: the lasagna is thawed and the microwave has switched itself off.',
                'stopped_early_message': 'The microwave stopped before the lasagna was thawed.',
            },
            {
                'id': 'thaw_fish', 'appliance': 'microwave', 'items': ['fish'],
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 360.0, 'switch_off_when_complete': True,
                'running_message': 'The microwave is running; the fish casserole inside is thawing.',
                'completed_message': 'The microwave chimed: the fish casserole is thawed and the microwave has switched itself off.',
                'stopped_early_message': 'The microwave stopped before the fish casserole was thawed.',
            },
            {
                'id': 'bake_lasagna', 'appliance': 'oven', 'items': ['lasagna'], 'requires_completed': 'thaw',
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 600.0,
                'running_message': 'The oven is on; the lasagna inside is baking.',
                'completed_message': 'The oven timer beeped: the lasagna is baked.',
                'stopped_early_message': 'The oven stopped before the lasagna was baked.',
                'not_ready_message': 'The lasagna in the oven is still frozen, so it is not baking.',
            },
            {
                'id': 'bake_fish', 'appliance': 'oven', 'items': ['fish'], 'requires_completed': 'thaw_fish',
                'containment': 'Inside', 'require_closed': True, 'require_toggled_on': True,
                'duration_seconds': 240.0,
                'running_message': 'The oven is on; the fish casserole inside is baking.',
                'completed_message': 'The oven timer beeped: the fish casserole is baked.',
                'stopped_early_message': 'The oven stopped before the fish casserole was baked.',
                'not_ready_message': 'The fish casserole in the oven is still frozen, so it is not baking.',
            },
        ],
        # r3: the oven's Open state is its door only (ffitak's racks are openable joints too; opening pulled them
        # out and the loaded rack bounced back, so the closed oven stayed "open" - c2r2v8 optimal)
        'open_state_joints': {'oven': ['j_door']},
        'appliance_capacities': [
            {'appliance': 'microwave', 'capacity': 1, 'counted_items': list(DISHES),
             'full_message': 'The microwave takes only one baking dish at a time, and one is already inside.'},
        ],
        'initial_layout': [
            state('bedroom_door', 'Open', True, fully=True),
            # frozen dishes inside the closed refrigerator
            state('fridge', 'Open', True, fully=True),
            # r1: one dish per shelf (the 24.5 cm round dish and the rectangular one do not fit side by side)
            on('lasagna', 'fridge', [7.82, -1.85], yaw90=True, support_probe_height=1.0, initial_relation='Inside'),
            on('fish', 'fridge', [7.82, -1.85], support_probe_height=0.6, initial_relation='Inside'),
            state('fridge', 'Open', False, fully=True),
            # tableware, tea and tray on the kitchen island in plain view
            on('plate1', 'island', [6.02, 0.05]),
            on('plate2', 'island', [6.42, 0.05]),
            on('tray', 'island', [7.00, 0.08], yaw90=True),
            on('fork1', 'island', [7.50, 0.05], yaw90=True),
            on('fork2', 'island', [7.74, 0.05], yaw90=True),
            on('fork3', 'island', [7.98, 0.05], yaw90=True),
            on('tea', 'island', [8.40, 0.05]),
            # unused base-task objects: board inside the closed pantry cabinet, the rest on its roof (as C1/C4)
            state('cabinet', 'Open', True, fully=True),
            on('unused_board', 'cabinet', [8.74, -1.96], support_probe_height=0.9, initial_relation='Inside'),
            on('unused_half_apple_a', 'cabinet', [8.22, -1.92]),
            on('unused_half_apple_b', 'cabinet', [8.42, -1.92]),
            on('unused_cookie', 'cabinet', [8.62, -1.92]),
            on('unused_sandwich', 'cabinet', [8.86, -1.92]),
            on('unused_box', 'cabinet', [9.22, -1.92]),
            state('cabinet', 'Open', False, fully=True),
            state('oven', 'ToggledOn', False),
            state('oven', 'Open', False, fully=True),
            state('microwave', 'ToggledOn', False),
            state('microwave', 'Open', False, fully=True),
        ],
        'initial_assertions': {
            **{f'{d}_in_fridge': f'initial_{d}_Inside' for d in DISHES},
            **{f'{o}_on_island': f'initial_{o}_OnTop' for o in island_items},
            'fridge_closed': {'not': 'fridge_open'},
            'cabinet_closed': {'not': 'cabinet_open'},
            'oven_closed': {'not': 'oven_open'},
            'microwave_closed': {'not': 'microwave_open'},
            'oven_off': {'not': 'oven_on'},
            'microwave_off': {'not': 'microwave_on'},
            'hand_empty': 'hand_empty',
        },
        'future_roles': [],
        'replacements': [],
        'public_instruction': (
            "Thaw and bake the frozen fish casserole and the frozen lasagna; serve the fish with a fork and tea "
            "in the children's bedroom and the lasagna with plates and forks in the living room, and leave the "
            "kitchen appliances off and closed."
        ),
        'public_context': [req_cook, req_fish, req_lasagna, req_reset, req_hand],
        'requirements': [
            {'id': 'dishes_baked', 'text': req_cook, 'terminal': ['dishes_baked']},
            {'id': 'fish_served', 'text': req_fish, 'terminal': ['fish_served']},
            {'id': 'lasagna_served', 'text': req_lasagna, 'terminal': ['lasagna_served']},
            {'id': 'appliances_safe', 'text': req_reset, 'terminal': ['appliances_safe']},
            {'id': 'hand_empty', 'text': req_hand, 'terminal': ['hand_empty']},
        ],
        'evaluation': {
            'predicates': {
                'lasagna_baked': {'object': 'lasagna', 'state': 'ProcessComplete', 'process': 'bake_lasagna'},
                'fish_baked': {'object': 'fish', 'state': 'ProcessComplete', 'process': 'bake_fish'},
                'lasagna_thawed': {'object': 'lasagna', 'state': 'ProcessComplete', 'process': 'thaw'},
                'fish_thawed': {'object': 'fish', 'state': 'ProcessComplete', 'process': 'thaw_fish'},
                **{f'{o}_ontop_{d}': {'object': o, 'state': 'OnTop', 'target': d}
                   for o in ['lasagna'] + living for d in ('table', 'tray')},
                **{f'{o}_ontop_{d}': {'object': o, 'state': 'OnTop', 'target': d}
                   for o in ['fish'] + north for d in ('bed', 'tray')},
                # r4: the three forks look alike, so any fork may go to either destination.
                **{f'{o}_ontop_{d}': {'object': o, 'state': 'OnTop', 'target': d}
                   for o in FORKS for d in ('bed', 'table', 'tray')},
                'tray_ontop_table': {'object': 'tray', 'state': 'OnTop', 'target': 'table'},
                'tray_ontop_bed': {'object': 'tray', 'state': 'OnTop', 'target': 'bed'},
                'fridge_open': {'object': 'fridge', 'state': 'Open'},
                'cabinet_open': {'object': 'cabinet', 'state': 'Open'},
                'oven_open': {'object': 'oven', 'state': 'Open'},
                'microwave_open': {'object': 'microwave', 'state': 'Open'},
                'oven_on': {'object': 'oven', 'state': 'ToggledOn'},
                'microwave_on': {'object': 'microwave', 'state': 'ToggledOn'},
                'hand_empty': {'object': 'tea', 'state': 'HandEmpty'},
                **{f'initial_{d}_Inside': {'object': d, 'state': 'Inside', 'target': 'fridge'} for d in DISHES},
                **{f'initial_{o}_OnTop': {'object': o, 'state': 'OnTop', 'target': 'island'}
                   for o in island_items},
            },
            'terminal': {
                'dishes_baked': {'all': ['lasagna_baked', 'fish_baked']},
                'fish_served': {'all': ['fish_baked', served('fish', 'bed'), served('tea', 'bed'),
                                        {'any': [served(f, 'bed') for f in FORKS]}]},
                'lasagna_served': {'all': ['lasagna_baked'] + [served(o, 'table') for o in ['lasagna', 'plate1', 'plate2']]
                                   + [{'any': [{'all': [served(a, 'table'), served(b, 'table')]}
                                               for a, b in FORK_PAIRS]}]},
                'appliances_safe': {'all': [{'not': 'oven_on'}, {'not': 'microwave_on'}, {'not': 'oven_open'},
                                            {'not': 'microwave_open'}, {'not': 'fridge_open'}]},
                'hand_empty': 'hand_empty',
            },
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': ['optimal_johnson_tray', 'habit_sequential'],
            'physical_negative_cases': ['omit_fish_serve', 'omit_living_tableware', 'omit_lasagna_serve'],
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
    def thaw_start(dish):
        return [act('open', 'microwave'), act('open', 'fridge'), g(dish, approach_role='fridge'),
                act('place_inside', 'microwave'), act('close', 'microwave'), act('toggle_on', 'microwave'),
                act('close', 'fridge')]

    def to_oven(dish, thawed):
        return [wait(thawed, 20.0, 25), act('open', 'oven'), act('open', 'microwave'),
                g(dish, approach_role='microwave'), act('place_inside', 'oven', xy=OVEN_AIM[dish]),
                act('close', 'oven'), act('toggle_on', 'oven'), act('close', 'microwave')]

    phases = {
        # ---- optimal (Johnson order: lasagna first)
        'thaw_lasagna': {'actions': thaw_start('lasagna'), 'after': []},
        'lasagna_to_oven': {'actions': to_oven('lasagna', 'lasagna_thawed'), 'after': ['thaw_lasagna']},
        # the microwave is closed again after the lasagna; thaw_start opens it
        'thaw_fish': {'actions': thaw_start('fish'), 'after': ['lasagna_to_oven']},
        'tray_north': {'actions': [g('fork3'), put('tray', uv=[0.25, 0.25]), g('tea'), put('tray', uv=[0.25, 0.75]),
                                   g('tray'), put('bed', xy=[21.25, 13.20])],
                       'after': []},
        'plates_living': {'actions': [g('plate1'), put('table', uv=[0.25, 0.25]),
                                      g('plate2'), put('table', uv=[0.25, 0.75])], 'after': []},
        # opening the oven pauses the lasagna for the loading time (honest timer semantics)
        'fish_to_oven': {'actions': to_oven('fish', 'fish_thawed'), 'after': ['thaw_fish', 'lasagna_to_oven']},
        'forks_living': {'actions': [g('fork1'), put('table', uv=[0.55, 0.20]),
                                     g('fork2'), put('table', uv=[0.55, 0.80])], 'after': []},
        # take the lasagna out and shut the door so the fish keeps baking, then serve it
        'lasagna_out': {'actions': [wait('lasagna_baked', 20.0, 40), act('open', 'oven'),
                                    g('lasagna', approach_role='oven'), put('island', xy=[6.40, 0.45]),
                                    act('close', 'oven')],
                        'after': ['lasagna_to_oven']},
        'lasagna_serve': {'actions': [g('lasagna'), put('table', uv=[0.70, 0.50])], 'after': ['lasagna_out']},
        'fish_out': {'actions': [wait('fish_baked', 20.0, 40), act('toggle_off', 'oven'), act('open', 'oven'),
                                 g('fish', approach_role='oven'), put('island', xy=[6.90, 0.45]),
                                 act('close', 'oven')],
                     'after': ['fish_to_oven']},
        'fish_serve_tray': {'actions': [g('fish'), put('tray', uv=[0.72, 0.50])], 'after': ['fish_out', 'tray_north']},
        # ---- habit: instruction order (fish first), idle waits, one item per trip
        'h_thaw_fish': {'actions': thaw_start('fish'), 'after': []},
        'h_fish_to_oven': {'actions': to_oven('fish', 'fish_thawed'), 'after': ['h_thaw_fish']},
        'h_fish_bake_serve': {'actions': [wait('fish_baked', 30.0, 20), act('toggle_off', 'oven'),
                                          act('open', 'oven'), g('fish', approach_role='oven'),
                                          put('bed', xy=[20.95, 13.20]), act('close', 'oven')],
                              'after': ['h_fish_to_oven']},
        'h_thaw_lasagna': {'actions': thaw_start('lasagna'), 'after': ['h_fish_bake_serve']},
        'h_lasagna_to_oven': {'actions': to_oven('lasagna', 'lasagna_thawed'), 'after': ['h_thaw_lasagna']},
        'h_lasagna_bake_serve': {'actions': [wait('lasagna_baked', 30.0, 30), act('toggle_off', 'oven'),
                                             act('open', 'oven'), g('lasagna', approach_role='oven'),
                                             put('table', uv=[0.70, 0.50]), act('close', 'oven')],
                                 'after': ['h_lasagna_to_oven']},
        'h_north_singles': {'actions': [g('fork3'), put('bed', xy=[21.30, 13.25]),
                                        g('tea'), put('bed', xy=[21.55, 13.25])], 'after': []},
    }
    orders = {
        'optimal_johnson_tray': [
            'thaw_lasagna', 'lasagna_to_oven', 'thaw_fish', 'tray_north', 'plates_living', 'fish_to_oven',
            'forks_living', 'lasagna_out', 'lasagna_serve', 'fish_out', 'fish_serve_tray',
        ],
        'habit_sequential': [
            'h_thaw_fish', 'h_fish_to_oven', 'h_fish_bake_serve', 'h_thaw_lasagna', 'h_lasagna_to_oven',
            'h_lasagna_bake_serve', 'h_north_singles', 'plates_living', 'forks_living',
        ],
    }
    return {
        'kind': 'full_workflow',
        'allow_strategy_variants': True,
        'phases': phases,
        'orders': orders,
        'negative_omissions': {
            'omit_fish_serve': 'fish_serve_tray',          # baked fish left on the island
            'omit_living_tableware': 'forks_living',        # forks never reach the living room
            # omitted phases must have no dependents (a later phase would fail, not the evaluator);
            # "a frozen dish does not bake" is covered by tests/test_timed_processes.py
            'omit_lasagna_serve': 'lasagna_serve',          # baked lasagna left on the island
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
