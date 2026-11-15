#!/usr/bin/env python3
"""Generate recipe.json and frozen task.json for custom task `blind_cabinet_exact_restock_unlock` (T7).

Task: 闭柜未知存货精确配平与压盖工具箱多跳解锁（0 秒等待纯空间条件规划 + 盈余物资防多搬折返 + 多跳压盖容器解锁）
Base scene: `clean_up_your_desk` instance 301, seed 0 (`house_single_floor`).

Design evidence & adherence to the 5 benchmark task criteria (AGENTS.md & 多模式规划难任务构造与调研_20261115.md):
  1. Feasible on current executor (`5741c57`):
     - Zero fluid/cloth/particle/timer instability: pure rigid-body & articulated-container states (`Open`,
       `Inside`, `OnTop`, `AttachedTo`, `ToggledOn`, `HandEmpty`) under CPU dynamics.
     - Reuses 1 cm-verified `clean_up_your_desk` single-floor house geometry (`study_guest_laundry`), keeping
       all 5 suite/corridor doors open and closing unused closet/side doors, with unused BDDL base objects
       cleanly parked in closed-off `childs_room_2`.
     - Places two closed tall storage cabinets (`cabinet_east` at `[24.64, 22.50]` and `cabinet_west` at
       `[21.08, 22.42]`) in the north guest bedroom (`y ~ 22.0..25.8`), ~24-25.5 m A* travel distance from
       the south utility supply counter (`countertop_ikwqer_0` at `y ~ -0.3..1.8`).
  2. Zero ambiguity or perception traps:
     - Excludes duplicate lamps/nightstands and all 11 extra corridor `shelf_amyper_*` planks so `shelf_amyper_2`
       is the single unambiguous floating wall shelf outside the study doorway.
     - Uses two high-visibility unlit table lamps (`table_lamp/ehjsdz`, `47 x 47 x 74 cm`) and two standard
       light bulbs (`light_bulb/kfmkwd`) inside a closed metal toolbox (`toolbox_191`) on a hallway workbench
       (`desk/bhkhxo` scaled `[0.95, 0.72, 1.0]` at `[19.92, 7.65]`), with two paperback books resting beside/on
       the workbench guarding the toolbox lid.
     - No split-mesh countertop traps: `countertop_ikwqer_0` is a single continuous 4.47 m counter surface.
  3. Planning complexity & human-realistic efficiency (M5 + M6, 0 s wait):
     - M5 (Contingent Blind-Cabinet Exact Balancing with Surplus Penalty):
       * North `cabinet_east` (initially closed) holds `1 water + 1 crackers` (needs `+1 apple juice`).
       * North `cabinet_west` (initially closed) holds `1 apple juice` (needs `+1 water + 1 crackers`).
       * South utility counter (`24-25.5 m` away) holds **surplus** stock (`2 water + 2 apple juice + 2 crackers`
         = 6 items, whereas total north deficit is only `1 water + 1 apple juice + 1 crackers` = 3 items) plus an
         open wicker basket (`carrier`).
       * Terminal goal requires **exact** stock (`1 water + 1 apple juice + 1 crackers` per north cabinet, doors
         closed) AND requires the unused supply items (`1 water + 1 apple juice + 1 crackers`) to remain/finish
         on the south utility counter. Inspecting the closed north cabinets first lets the agent bring only the
         exact 3 needed supplies in one carrier trip; blindly hauling extra supplies from south forces a costly
         50 m round-trip return to put surplus back on the south utility counter.
     - M6 (Multi-Hop Blocked-Container Unlock):
       * Two light bulbs needed to `attach` and switch on `study_lamp` and `guest_lamp` are locked inside the
         closed hallway metal `toolbox`.
       * Opening `toolbox` is guarded (`transition_guards`) until both `book1` and `book2` are cleared off the
         hallway tool bench (and delivered onto `hall_shelf`), forming a 4-hop dependency chain:
         `clear books -> shelve books -> open toolbox & extract bulbs -> attach bulbs & switch lamps on`.
     - Public instruction and `public_context` are 100% strategy-neutral: state only terminal conditions, initial
       locations, and the toolbox-lid clearance rule, with zero ordering/batching hints.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / 'src'))

from manipulation_agent.references.full_workflow_reference import validate_workflow_plan  # noqa: E402
from manipulation_agent.tasks.custom_tasks import load_spec, sha256, validate_spec  # noqa: E402

CLEAN_DESK_TEMPLATE_SHA256 = '6096a25139486cb627b5faa7b43c8ed20001d4ba8974bcd5b5e09182991df03f'


def one_of_three_partition(
    item_preds_east: tuple[str, str, str],
    item_preds_west: tuple[str, str, str],
    item_preds_south: tuple[str, str, str],
) -> dict:
    """Require exact 1-east, 1-west, 1-south partition across 3 identical-category instances."""
    permutations = (
        (0, 1, 2),
        (0, 2, 1),
        (1, 0, 2),
        (1, 2, 0),
        (2, 0, 1),
        (2, 1, 0),
    )
    return {
        'any': [
            {
                'all': [
                    item_preds_east[i],
                    item_preds_west[j],
                    item_preds_south[k],
                    {'not': item_preds_east[j]},
                    {'not': item_preds_east[k]},
                    {'not': item_preds_west[i]},
                    {'not': item_preds_west[k]},
                ]
            }
            for i, j, k in permutations
        ]
    }


def build_recipe() -> dict:
    req_cabinets_text = (
        'Balance the two closed storage cabinets in the north guest bedroom (one against the east wall and '
        'one against the west wall) using the supply stock on the south utility-room counter so that each of '
        'the two guest-bedroom cabinets holds one bottle of water, one bottle of apple juice, and one box of '
        'crackers with no extra supply items placed inside either cabinet, leave all remaining unused supply '
        'items resting on top of the south utility-room counter, and leave both guest-bedroom cabinet doors '
        'closed.'
    )
    req_unlock_lamps_text = (
        'Place both paperback books from the east-hallway wooden workbench onto the floating wall shelf '
        'outside the study doorway, retrieve the two light bulbs from inside the metal toolbox on that '
        'hallway workbench, attach one light bulb to the unlit table lamp on the study desk and switch that '
        'lamp on, attach the other light bulb to the unlit table lamp on the guest-bedroom nightstand and '
        'switch that lamp on, and leave the metal toolbox closed on the hallway workbench. Rule: the metal '
        'toolbox must not be opened while either paperback book is still on the hallway workbench or on top '
        'of the toolbox.'
    )
    req_carrier_hand_text = (
        'Leave the wooden utility tray resting on either the south utility-room counter or the wooden side table '
        'along the west wall of the north guest bedroom, and finish with no object held.'
    )

    spec = {
        'schema_version': 3,
        'id': 'blind_cabinet_exact_restock_unlock',
        'instance_id': 'blind_cabinet_exact_restock_unlock_seed0_r1',
        'status': 'complete_recipe_physics_pending',
        'benchmark_ready': False,
        'official_submission_eligible': False,
        'base': {
            'task': 'clean_up_your_desk',
            'instance': 301,
            'seed': 0,
        },
        'roles': {
            # Floors
            'study_floor': 'floors_qnvzkr_0',
            'hall_floor': 'floors_wkdxrr_0',
            'guest_floor': 'floors_htolat_0',
            'utility_floor': 'floors_glwobe_0',
            # Study & Corridor workstations
            'study_desk': 'desk_bhkhxo_0',
            'hall_shelf': 'shelf_amyper_2',
            'tool_bench': 'bceru_tool_bench',
            'toolbox': 'bceru_toolbox',
            'book1': 'paperback_book_225',
            'book2': 'paperback_book_226',
            'bulb1': 'bceru_bulb1',
            'bulb2': 'bceru_bulb2',
            'study_lamp': 'bceru_study_lamp',
            # North Guest Bedroom stations (2 closed blind-stock cabinets + nightstand lamp + side table)
            'cabinet_east': 'bceru_cabinet_east',
            'cabinet_west': 'bceru_cabinet_west',
            'guest_nightstand': 'nightstand_wbxekb_0',
            'guest_lamp': 'bceru_guest_lamp',
            'guest_table': 'bceru_guest_table',
            # South Utility Room supply station & open carrier
            'utility_counter': 'countertop_ikwqer_0',
            'carrier': 'bceru_carrier',
            # 3 categories x 3 instances = 9 total supply items (3 pre-stocked in north cabinets, 6 on south counter)
            'water1': 'bceru_water1',
            'water2': 'bceru_water2',
            'water3': 'bceru_water3',
            'juice1': 'bceru_juice1',
            'juice2': 'bceru_juice2',
            'juice3': 'bceru_juice3',
            'crackers1': 'bceru_crackers1',
            'crackers2': 'bceru_crackers2',
            'crackers3': 'bceru_crackers3',
            # Doors (explicitly controlled so corridor <-> study <-> guest <-> utility is unobstructed)
            'study_door': 'sliding_door_tprpvb_1',
            'utility_entry': 'sliding_door_tprpvb_12',
            'utility_exit': 'sliding_door_tprpvb_7',
            'guest_entry': 'sliding_door_tprpvb_0',
            'guest_bath_door': 'sliding_door_tprpvb_10',
            'study_garden_door': 'door_kwbnhy_3',
            'study_closet_door': 'sliding_door_tprpvb_8',
            'hall_bath_door': 'sliding_door_tprpvb_5',
            'unused_room2_door': 'sliding_door_tprpvb_6',
            'unused_room0_door': 'sliding_door_tprpvb_11',
            'guest_closet0_door': 'sliding_door_tprpvb_4',
            'guest_closet1_door': 'sliding_door_tprpvb_9',
            # Unused BDDL-scoped base clutter parked inside closed-off childs_room_2
            'unused_floor': 'floors_vmamhf_0',
            'unused_chair': 'eames_chair_hndxiw_0',
            'unused_laptop': 'laptop_233',
            'unused_pencil_case': 'pencil_case_224',
            'unused_lamp_study': 'table_lamp_uupwiq_0',
            'unused_folder1': 'folder_231',
            'unused_folder2': 'folder_232',
            'unused_pen1': 'pen_228',
            'unused_pen2': 'pen_229',
            'unused_pencil': 'pencil_230',
            'unused_stapler': 'stapler_227',
        },
        'scene_exclusions': [
            # Remove duplicate / visually ambiguous lamps & nightstands in guest suite
            'nightstand_wbxekb_1',
            'nightstand_wbxekb_2',
            'table_lamp_qgdwbh_0',
            'table_lamp_qgdwbh_1',
            'wardrobe_lrokhl_0',
            'bench_pfjnnj_0',
            'multi_station_furniture_sink_upwldu_0',
            'shelf_fjozhc_0',
            'public_trash_can_gvnfgj_0',
            # Remove extra corridor shelf planks so `shelf_amyper_2` is the sole hallway wall shelf
            'shelf_amyper_0',
            'shelf_amyper_1',
            'shelf_amyper_3',
            'shelf_amyper_4',
            'shelf_amyper_5',
            'shelf_amyper_6',
            'shelf_amyper_7',
            'shelf_amyper_8',
            'shelf_amyper_9',
            'shelf_amyper_10',
            'shelf_amyper_11',
        ],
        'construction_asset_scales': [],
        'asset_imports': [
            {
                'name': 'bceru_cabinet_east',
                'source_task': 'clean_up_your_desk',
                'source_object': 'bottom_cabinet_fancyy_0',
                'source_sha256': CLEAN_DESK_TEMPLATE_SHA256,
                'position': [101.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 1.0, 0.0],
                'fixed_base': True,
                'scale': [0.80, 0.72, 0.90],
            },
            {
                'name': 'bceru_cabinet_west',
                'source_task': 'clean_up_your_desk',
                'source_object': 'bottom_cabinet_fancyy_0',
                'source_sha256': CLEAN_DESK_TEMPLATE_SHA256,
                'position': [104.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'fixed_base': True,
                'scale': [0.80, 0.72, 0.90],
            },
            {
                'name': 'bceru_guest_table',
                'source_task': 'clean_up_your_desk',
                'source_object': 'desk_nbpuns_0',
                'source_sha256': CLEAN_DESK_TEMPLATE_SHA256,
                'position': [107.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'fixed_base': True,
                'scale': [1.0, 0.95, 1.0],
            },
            {
                'name': 'bceru_toolbox',
                'source_task': 'outfit_a_basic_toolbox',
                'source_object': 'toolbox_191',
                'source_sha256': 'dd7e35f3bb977176b280c84aa6fa1ef252bd9579e8d32a8b55114b7513aaf58e',
                'position': [110.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
        ],
        'native_assets': [
            {
                'name': 'bceru_carrier',
                'category': 'tray',
                'model': 'coqeme',
                'usd_sha256': 'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd',
                'prim_type': 0,
                'position': [113.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [3.38, 2.16, 1.50],
            },
            {
                'name': 'bceru_tool_bench',
                'category': 'desk',
                'model': 'bhkhxo',
                'usd_sha256': 'bd90dd738a51fb20776c177efd1c2845a5b6368c02f81cb7e3dfd38a893d04d7',
                'prim_type': 0,
                'fixed_base': True,
                'scale': [0.76, 1.05, 1.00],
                'position': [116.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_bulb1',
                'category': 'light_bulb',
                'model': 'kfmkwd',
                'usd_sha256': 'e2fcd2fbd519407e50d84f0ddaa1b3409d891c07952089b5979663fd1d446c13',
                'prim_type': 0,
                'position': [119.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_bulb2',
                'category': 'light_bulb',
                'model': 'kfmkwd',
                'usd_sha256': 'e2fcd2fbd519407e50d84f0ddaa1b3409d891c07952089b5979663fd1d446c13',
                'prim_type': 0,
                'position': [122.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_study_lamp',
                'category': 'table_lamp',
                'model': 'ehjsdz',
                'usd_sha256': '058b13cba1cb5a68c18df649bc6246020ff71115fcef159a7ca83cc75e174550',
                'prim_type': 0,
                'position': [125.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_guest_lamp',
                'category': 'table_lamp',
                'model': 'ehjsdz',
                'usd_sha256': '058b13cba1cb5a68c18df649bc6246020ff71115fcef159a7ca83cc75e174550',
                'prim_type': 0,
                'position': [128.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_water1',
                'category': 'bottle_of_water',
                'model': 'atvnqy',
                'usd_sha256': 'da8e2524f41fbae998ee82b50cb013d14a6a8b64ff986edb5a5fefcc8619417c',
                'prim_type': 0,
                'position': [131.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_water2',
                'category': 'bottle_of_water',
                'model': 'atvnqy',
                'usd_sha256': 'da8e2524f41fbae998ee82b50cb013d14a6a8b64ff986edb5a5fefcc8619417c',
                'prim_type': 0,
                'position': [134.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_water3',
                'category': 'bottle_of_water',
                'model': 'atvnqy',
                'usd_sha256': 'da8e2524f41fbae998ee82b50cb013d14a6a8b64ff986edb5a5fefcc8619417c',
                'prim_type': 0,
                'position': [137.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_juice1',
                'category': 'bottle_of_apple_juice',
                'model': 'xvrbdy',
                'usd_sha256': '6f8e42e987e6f8284c77a60bf5d550b2053c6654ae6be38d2e14b5f46031bf31',
                'prim_type': 0,
                'position': [140.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_juice2',
                'category': 'bottle_of_apple_juice',
                'model': 'xvrbdy',
                'usd_sha256': '6f8e42e987e6f8284c77a60bf5d550b2053c6654ae6be38d2e14b5f46031bf31',
                'prim_type': 0,
                'position': [143.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_juice3',
                'category': 'bottle_of_apple_juice',
                'model': 'xvrbdy',
                'usd_sha256': '6f8e42e987e6f8284c77a60bf5d550b2053c6654ae6be38d2e14b5f46031bf31',
                'prim_type': 0,
                'position': [146.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_crackers1',
                'category': 'box_of_crackers',
                'model': 'cmdigf',
                'usd_sha256': 'd1fbcb8229c6120add9c2dec27ab55c9cb17c6b8bf515a8415c9f6123163ff00',
                'prim_type': 0,
                'position': [149.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_crackers2',
                'category': 'box_of_crackers',
                'model': 'cmdigf',
                'usd_sha256': 'd1fbcb8229c6120add9c2dec27ab55c9cb17c6b8bf515a8415c9f6123163ff00',
                'prim_type': 0,
                'position': [152.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'bceru_crackers3',
                'category': 'box_of_crackers',
                'model': 'cmdigf',
                'usd_sha256': 'd1fbcb8229c6120add9c2dec27ab55c9cb17c6b8bf515a8415c9f6123163ff00',
                'prim_type': 0,
                'position': [155.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
        ],
        'fixtures': [],
        'robot_start': {
            'position': [19.15, 18.00, 0.02],
            'orientation': [0.0, 0.0, 0.70710678, 0.70710678],
        },
        'initial_layout': [
            # 1. Fixed furniture placed on native floors first
            {
                'object': 'tool_bench',
                'target': 'hall_floor',
                'place_on_support': True,
                'xy': [19.86, 7.45],
                'support_probe_height': 0.5,
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'cabinet_east',
                'target': 'guest_floor',
                'place_on_support': True,
                'xy': [24.64, 22.50],
                'support_probe_height': 0.5,
                'orientation': [0.0, 0.0, 1.0, 0.0],
            },
            {
                'object': 'cabinet_west',
                'target': 'guest_floor',
                'place_on_support': True,
                'xy': [21.08, 22.42],
                'support_probe_height': 0.5,
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'guest_table',
                'target': 'guest_floor',
                'place_on_support': True,
                'xy': [21.08, 24.05],
                'support_probe_height': 0.5,
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            # 2. Park unused BDDL-scoped base clutter inside closed-off childs_room_2
            {
                'object': 'unused_chair',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [21.00, 6.20],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_laptop',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [21.65, 6.20],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_pencil_case',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [22.25, 6.20],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_lamp_study',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [22.75, 6.20],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_folder1',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [23.25, 6.20],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_folder2',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [21.00, 6.85],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_pen1',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [21.50, 6.85],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_pen2',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [21.90, 6.85],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_pencil',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [22.30, 6.85],
                'support_probe_height': 0.5,
            },
            {
                'object': 'unused_stapler',
                'target': 'unused_floor',
                'place_on_support': True,
                'xy': [22.75, 6.85],
                'support_probe_height': 0.5,
            },
            # 3. Corridor toolbox + 2 blocking books on `tool_bench`, and 2 unlit table lamps
            {
                'object': 'toolbox',
                'target': 'tool_bench',
                'place_on_support': True,
                'support_uv': [0.55, 0.18],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'book1',
                'target': 'tool_bench',
                'place_on_support': True,
                'support_uv': [0.35, 0.43],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'book2',
                'target': 'tool_bench',
                'place_on_support': True,
                'support_uv': [0.70, 0.43],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'study_lamp',
                'target': 'study_desk',
                'place_on_support': True,
                'support_uv': [0.50, 0.50],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'guest_lamp',
                'target': 'guest_nightstand',
                'place_on_support': True,
                'support_uv': [0.50, 0.50],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            # 4. Stage items gently on supports during initial pose settle before placing Inside closed containers
            {
                'object': 'bulb1',
                'target': 'guest_table',
                'place_on_support': True,
                'support_uv': [0.32, 0.30],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'bulb2',
                'target': 'guest_table',
                'place_on_support': True,
                'support_uv': [0.68, 0.30],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'water1',
                'target': 'guest_table',
                'place_on_support': True,
                'support_uv': [0.25, 0.70],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'crackers1',
                'target': 'guest_table',
                'place_on_support': True,
                'support_uv': [0.52, 0.70],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'juice1',
                'target': 'guest_table',
                'place_on_support': True,
                'support_uv': [0.78, 0.70],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            # 5. South Utility Room supply counter: open basket + 6 supply items (3 needed north + 3 surplus)
            {
                'object': 'carrier',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.50, 1.65],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'water2',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.36, 0.92],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'water3',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.64, 0.92],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'juice2',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.36, 0.42],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'juice3',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.64, 0.42],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'crackers2',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.36, -0.10],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'crackers3',
                'target': 'utility_counter',
                'place_on_support': True,
                'xy': [24.64, -0.10],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            # 6. Door states: open active suite doors, close unused doors
            {'object': 'study_door', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'utility_entry', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'utility_exit', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'guest_entry', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'guest_bath_door', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'study_garden_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'study_closet_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'hall_bath_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'unused_room2_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'unused_room0_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'guest_closet0_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'guest_closet1_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            # 7. Populate blind initial stock inside the two closed north cabinets and bulbs inside closed toolbox
            {'object': 'cabinet_east', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {
                'object': 'water1',
                'state': 'Inside',
                'target': 'cabinet_east',
                'value': True,
                'max_sampling_attempts': 3,
            },
            {
                'object': 'crackers1',
                'state': 'Inside',
                'target': 'cabinet_east',
                'value': True,
                'max_sampling_attempts': 3,
            },
            {'object': 'cabinet_east', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'cabinet_west', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {
                'object': 'juice1',
                'state': 'Inside',
                'target': 'cabinet_west',
                'value': True,
                'max_sampling_attempts': 3,
            },
            {'object': 'cabinet_west', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'toolbox', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {
                'object': 'bulb1',
                'state': 'Inside',
                'target': 'toolbox',
                'value': True,
                'max_sampling_attempts': 3,
            },
            {
                'object': 'bulb2',
                'state': 'Inside',
                'target': 'toolbox',
                'value': True,
                'max_sampling_attempts': 3,
            },
            {'object': 'toolbox', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            # 8. Initial lamp switch states
            {'object': 'study_lamp', 'state': 'ToggledOn', 'value': False},
            {'object': 'guest_lamp', 'state': 'ToggledOn', 'value': False},
        ],
        'initial_assertions': {
            'study_door_open': 'study_door_open',
            'utility_entry_open': 'utility_entry_open',
            'utility_exit_open': 'utility_exit_open',
            'guest_entry_open': 'guest_entry_open',
            'cabinet_east_closed': {'not': 'cabinet_east_open'},
            'cabinet_west_closed': {'not': 'cabinet_west_open'},
            'toolbox_closed': {'not': 'toolbox_open'},
            'study_lamp_off': {'not': 'study_lamp_on'},
            'guest_lamp_off': {'not': 'guest_lamp_on'},
            'water1_in_east': 'water1_inside_cabinet_east',
            'crackers1_in_east': 'crackers1_inside_cabinet_east',
            'juice1_in_west': 'juice1_inside_cabinet_west',
            'bulb1_in_toolbox': 'bulb1_inside_toolbox',
            'bulb2_in_toolbox': 'bulb2_inside_toolbox',
            'bulb1_detached': {'all': [{'not': 'bulb1_attached_study_lamp'}, {'not': 'bulb1_attached_guest_lamp'}]},
            'bulb2_detached': {'all': [{'not': 'bulb2_attached_study_lamp'}, {'not': 'bulb2_attached_guest_lamp'}]},
            'toolbox_on_bench': 'toolbox_ontop_tool_bench',
            'book1_on_bench': 'book1_ontop_tool_bench',
            'book2_on_bench': 'book2_ontop_tool_bench',
            'study_lamp_on_desk': 'study_lamp_ontop_study_desk',
            'guest_lamp_on_nightstand': 'guest_lamp_ontop_guest_nightstand',
            'carrier_on_utility': 'carrier_ontop_utility_counter',
            'water2_on_utility': 'water2_ontop_utility_counter',
            'water3_on_utility': 'water3_ontop_utility_counter',
            'juice2_on_utility': 'juice2_ontop_utility_counter',
            'juice3_on_utility': 'juice3_ontop_utility_counter',
            'crackers2_on_utility': 'crackers2_ontop_utility_counter',
            'crackers3_on_utility': 'crackers3_ontop_utility_counter',
            'hand_empty': 'hand_empty',
        },
        'future_roles': [],
        'replacements': [],
        'public_instruction': (
            'Balance both closed storage cabinets in the north guest bedroom using supplies from the south '
            'utility-room counter so each cabinet holds one bottle of water, one bottle of apple juice, and '
            'one box of crackers while unused surplus supplies stay on the south utility-room counter and both '
            'cabinet doors end closed; shelve both paperback books from the hallway workbench onto the study-doorway '
            'wall shelf, unlock the closed hallway metal toolbox (which must not be opened while either book is '
            'still on the workbench or toolbox) to install light bulbs into and switch on the study-desk and '
            'guest-nightstand table lamps, leave the toolbox closed and the wooden utility tray parked, and finish '
            'empty-handed.'
        ),
        'public_context': [
            req_cabinets_text,
            req_unlock_lamps_text,
            req_carrier_hand_text,
        ],
        'requirements': [
            {
                'id': 'exact_cabinet_balance_and_surplus_conservation',
                'text': req_cabinets_text,
                'terminal': [
                    'water_exact_balance_and_surplus',
                    'juice_exact_balance_and_surplus',
                    'crackers_exact_balance_and_surplus',
                    'cabinets_closed',
                ],
            },
            {
                'id': 'hallway_toolbox_unlock_and_lamps',
                'text': req_unlock_lamps_text,
                'terminal': [
                    'books_shelved',
                    'lamps_lit_with_bulbs',
                    'toolbox_closed_on_bench',
                ],
                'constraints': [
                    'toolbox_open_requires_books_cleared',
                ],
            },
            {
                'id': 'carrier_parking_and_empty_hand',
                'text': req_carrier_hand_text,
                'terminal': [
                    'carrier_parked',
                    'hand_empty',
                ],
            },
        ],
        'evaluation': {
            'predicates': {
                # Doors & containers
                'study_door_open': {'object': 'study_door', 'state': 'Open'},
                'utility_entry_open': {'object': 'utility_entry', 'state': 'Open'},
                'utility_exit_open': {'object': 'utility_exit', 'state': 'Open'},
                'guest_entry_open': {'object': 'guest_entry', 'state': 'Open'},
                'cabinet_east_open': {'object': 'cabinet_east', 'state': 'Open'},
                'cabinet_west_open': {'object': 'cabinet_west', 'state': 'Open'},
                'toolbox_open': {'object': 'toolbox', 'state': 'Open'},
                'toolbox_ontop_tool_bench': {'object': 'toolbox', 'state': 'OnTop', 'target': 'tool_bench'},
                # Lamps & bulbs
                'study_lamp_on': {'object': 'study_lamp', 'state': 'ToggledOn'},
                'guest_lamp_on': {'object': 'guest_lamp', 'state': 'ToggledOn'},
                'study_lamp_ontop_study_desk': {'object': 'study_lamp', 'state': 'OnTop', 'target': 'study_desk'},
                'guest_lamp_ontop_guest_nightstand': {
                    'object': 'guest_lamp',
                    'state': 'OnTop',
                    'target': 'guest_nightstand',
                },
                'bulb1_inside_toolbox': {'object': 'bulb1', 'state': 'Inside', 'target': 'toolbox'},
                'bulb2_inside_toolbox': {'object': 'bulb2', 'state': 'Inside', 'target': 'toolbox'},
                'bulb1_ontop_carrier': {'object': 'bulb1', 'state': 'OnTop', 'target': 'carrier'},
                'bulb2_ontop_carrier': {'object': 'bulb2', 'state': 'OnTop', 'target': 'carrier'},
                'bulb1_attached_study_lamp': {'object': 'bulb1', 'state': 'AttachedTo', 'target': 'study_lamp'},
                'bulb2_attached_guest_lamp': {'object': 'bulb2', 'state': 'AttachedTo', 'target': 'guest_lamp'},
                'bulb1_attached_guest_lamp': {'object': 'bulb1', 'state': 'AttachedTo', 'target': 'guest_lamp'},
                'bulb2_attached_study_lamp': {'object': 'bulb2', 'state': 'AttachedTo', 'target': 'study_lamp'},
                # Books (guarding toolbox unlock + target shelf)
                'book1_ontop_tool_bench': {'object': 'book1', 'state': 'OnTop', 'target': 'tool_bench'},
                'book2_ontop_tool_bench': {'object': 'book2', 'state': 'OnTop', 'target': 'tool_bench'},
                'book1_ontop_toolbox': {'object': 'book1', 'state': 'OnTop', 'target': 'toolbox'},
                'book2_ontop_toolbox': {'object': 'book2', 'state': 'OnTop', 'target': 'toolbox'},
                'book1_ontop_hall_shelf': {'object': 'book1', 'state': 'OnTop', 'target': 'hall_shelf'},
                'book2_ontop_hall_shelf': {'object': 'book2', 'state': 'OnTop', 'target': 'hall_shelf'},
                # Supply items: Inside cabinet_east, Inside cabinet_west, OnTop utility_counter, OnTop carrier
                'water1_inside_cabinet_east': {'object': 'water1', 'state': 'Inside', 'target': 'cabinet_east'},
                'water2_inside_cabinet_east': {'object': 'water2', 'state': 'Inside', 'target': 'cabinet_east'},
                'water3_inside_cabinet_east': {'object': 'water3', 'state': 'Inside', 'target': 'cabinet_east'},
                'water1_inside_cabinet_west': {'object': 'water1', 'state': 'Inside', 'target': 'cabinet_west'},
                'water2_inside_cabinet_west': {'object': 'water2', 'state': 'Inside', 'target': 'cabinet_west'},
                'water3_inside_cabinet_west': {'object': 'water3', 'state': 'Inside', 'target': 'cabinet_west'},
                'water1_ontop_utility_counter': {'object': 'water1', 'state': 'OnTop', 'target': 'utility_counter'},
                'water2_ontop_utility_counter': {'object': 'water2', 'state': 'OnTop', 'target': 'utility_counter'},
                'water3_ontop_utility_counter': {'object': 'water3', 'state': 'OnTop', 'target': 'utility_counter'},
                'water1_ontop_carrier': {'object': 'water1', 'state': 'OnTop', 'target': 'carrier'},
                'water2_ontop_carrier': {'object': 'water2', 'state': 'OnTop', 'target': 'carrier'},
                'water3_ontop_carrier': {'object': 'water3', 'state': 'OnTop', 'target': 'carrier'},
                'juice1_inside_cabinet_east': {'object': 'juice1', 'state': 'Inside', 'target': 'cabinet_east'},
                'juice2_inside_cabinet_east': {'object': 'juice2', 'state': 'Inside', 'target': 'cabinet_east'},
                'juice3_inside_cabinet_east': {'object': 'juice3', 'state': 'Inside', 'target': 'cabinet_east'},
                'juice1_inside_cabinet_west': {'object': 'juice1', 'state': 'Inside', 'target': 'cabinet_west'},
                'juice2_inside_cabinet_west': {'object': 'juice2', 'state': 'Inside', 'target': 'cabinet_west'},
                'juice3_inside_cabinet_west': {'object': 'juice3', 'state': 'Inside', 'target': 'cabinet_west'},
                'juice1_ontop_utility_counter': {'object': 'juice1', 'state': 'OnTop', 'target': 'utility_counter'},
                'juice2_ontop_utility_counter': {'object': 'juice2', 'state': 'OnTop', 'target': 'utility_counter'},
                'juice3_ontop_utility_counter': {'object': 'juice3', 'state': 'OnTop', 'target': 'utility_counter'},
                'juice1_ontop_carrier': {'object': 'juice1', 'state': 'OnTop', 'target': 'carrier'},
                'juice2_ontop_carrier': {'object': 'juice2', 'state': 'OnTop', 'target': 'carrier'},
                'juice3_ontop_carrier': {'object': 'juice3', 'state': 'OnTop', 'target': 'carrier'},
                'crackers1_inside_cabinet_east': {
                    'object': 'crackers1',
                    'state': 'Inside',
                    'target': 'cabinet_east',
                },
                'crackers2_inside_cabinet_east': {
                    'object': 'crackers2',
                    'state': 'Inside',
                    'target': 'cabinet_east',
                },
                'crackers3_inside_cabinet_east': {
                    'object': 'crackers3',
                    'state': 'Inside',
                    'target': 'cabinet_east',
                },
                'crackers1_inside_cabinet_west': {
                    'object': 'crackers1',
                    'state': 'Inside',
                    'target': 'cabinet_west',
                },
                'crackers2_inside_cabinet_west': {
                    'object': 'crackers2',
                    'state': 'Inside',
                    'target': 'cabinet_west',
                },
                'crackers3_inside_cabinet_west': {
                    'object': 'crackers3',
                    'state': 'Inside',
                    'target': 'cabinet_west',
                },
                'crackers1_ontop_utility_counter': {
                    'object': 'crackers1',
                    'state': 'OnTop',
                    'target': 'utility_counter',
                },
                'crackers2_ontop_utility_counter': {
                    'object': 'crackers2',
                    'state': 'OnTop',
                    'target': 'utility_counter',
                },
                'crackers3_ontop_utility_counter': {
                    'object': 'crackers3',
                    'state': 'OnTop',
                    'target': 'utility_counter',
                },
                'crackers1_ontop_carrier': {'object': 'crackers1', 'state': 'OnTop', 'target': 'carrier'},
                'crackers2_ontop_carrier': {'object': 'crackers2', 'state': 'OnTop', 'target': 'carrier'},
                'crackers3_ontop_carrier': {'object': 'crackers3', 'state': 'OnTop', 'target': 'carrier'},
                # Carrier parking & empty hand
                'carrier_ontop_utility_counter': {'object': 'carrier', 'state': 'OnTop', 'target': 'utility_counter'},
                'carrier_ontop_guest_table': {'object': 'carrier', 'state': 'OnTop', 'target': 'guest_table'},
                'carrier_ontop_tool_bench': {'object': 'carrier', 'state': 'OnTop', 'target': 'tool_bench'},
                'hand_empty': {'object': 'study_desk', 'state': 'HandEmpty'},
            },
            'terminal': {
                'water_exact_balance_and_surplus': one_of_three_partition(
                    ('water1_inside_cabinet_east', 'water2_inside_cabinet_east', 'water3_inside_cabinet_east'),
                    ('water1_inside_cabinet_west', 'water2_inside_cabinet_west', 'water3_inside_cabinet_west'),
                    ('water1_ontop_utility_counter', 'water2_ontop_utility_counter', 'water3_ontop_utility_counter'),
                ),
                'juice_exact_balance_and_surplus': one_of_three_partition(
                    ('juice1_inside_cabinet_east', 'juice2_inside_cabinet_east', 'juice3_inside_cabinet_east'),
                    ('juice1_inside_cabinet_west', 'juice2_inside_cabinet_west', 'juice3_inside_cabinet_west'),
                    ('juice1_ontop_utility_counter', 'juice2_ontop_utility_counter', 'juice3_ontop_utility_counter'),
                ),
                'crackers_exact_balance_and_surplus': one_of_three_partition(
                    ('crackers1_inside_cabinet_east', 'crackers2_inside_cabinet_east', 'crackers3_inside_cabinet_east'),
                    ('crackers1_inside_cabinet_west', 'crackers2_inside_cabinet_west', 'crackers3_inside_cabinet_west'),
                    (
                        'crackers1_ontop_utility_counter',
                        'crackers2_ontop_utility_counter',
                        'crackers3_ontop_utility_counter',
                    ),
                ),
                'cabinets_closed': {
                    'all': [
                        {'not': 'cabinet_east_open'},
                        {'not': 'cabinet_west_open'},
                    ]
                },
                'books_shelved': {
                    'all': [
                        'book1_ontop_hall_shelf',
                        'book2_ontop_hall_shelf',
                    ]
                },
                'lamps_lit_with_bulbs': {
                    'all': [
                        'study_lamp_ontop_study_desk',
                        'guest_lamp_ontop_guest_nightstand',
                        'study_lamp_on',
                        'guest_lamp_on',
                        {
                            'any': [
                                {'all': ['bulb1_attached_study_lamp', 'bulb2_attached_guest_lamp']},
                                {'all': ['bulb1_attached_guest_lamp', 'bulb2_attached_study_lamp']},
                            ]
                        },
                    ]
                },
                'toolbox_closed_on_bench': {
                    'all': [
                        {'not': 'toolbox_open'},
                        'toolbox_ontop_tool_bench',
                    ]
                },
                'carrier_parked': {
                    'any': [
                        'carrier_ontop_utility_counter',
                        'carrier_ontop_guest_table',
                    ]
                },
                'hand_empty': 'hand_empty',
            },
            'milestones': {},
            'state_guards': [],
            'transition_guards': [
                {
                    'id': 'toolbox_open_requires_books_cleared',
                    'predicate': 'toolbox_open',
                    'from_value': False,
                    'to_value': True,
                    'require': {
                        'all': [
                            {'not': 'book1_ontop_tool_bench'},
                            {'not': 'book2_ontop_tool_bench'},
                            {'not': 'book1_ontop_toolbox'},
                            {'not': 'book2_ontop_toolbox'},
                        ]
                    },
                },
            ],
            'carry_guards': [],
            'motion_guards': [],
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': [
                'optimal_inspect_unlock_carrier_loop',
                'habit_blind_overfetch_sequential',
            ],
            'physical_negative_cases': [
                'violate_toolbox_book_guard',
                'omit_cabinet_restock',
                'omit_guest_lamp_lighting',
                'omit_cabinet_doors_closing',
            ],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 300,
        },
        'reference_plan': {
            'kind': 'full_workflow',
            'allow_strategy_variants': True,
            'phases': {
                # --- OPTIMAL CONTINGENT LOOP (Inspect North -> Shelve Books & Unlock Toolbox Southbound -> Exact Batch Northbound) ---
                'opt_inspect_north_cabinets': {
                    'actions': [
                        {'primitive': 'open', 'role': 'cabinet_west'},
                        {'primitive': 'open', 'role': 'cabinet_east'},
                    ],
                    'after': [],
                },
                'opt_clear_and_shelve_books': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'book1'},
                        {'primitive': 'place_on_top', 'role': 'hall_shelf', 'uv': [0.50, 0.28]},
                        {'primitive': 'grasp', 'role': 'book2'},
                        {'primitive': 'place_on_top', 'role': 'hall_shelf', 'uv': [0.50, 0.72]},
                    ],
                    'after': ['opt_inspect_north_cabinets'],
                },
                'opt_unlock_toolbox_and_light_study_lamp': {
                    'actions': [
                        {'primitive': 'open', 'role': 'toolbox'},
                        {'primitive': 'grasp', 'role': 'bulb1', 'approach_role': 'toolbox'},
                        {'primitive': 'attach', 'role': 'study_lamp'},
                        {'primitive': 'toggle_on', 'role': 'study_lamp'},
                        {'primitive': 'grasp', 'role': 'bulb2', 'approach_role': 'toolbox'},
                        {'primitive': 'place_on_top', 'role': 'tool_bench', 'uv': [0.88, 0.80]},
                        {'primitive': 'close', 'role': 'toolbox'},
                    ],
                    'after': ['opt_clear_and_shelve_books'],
                },
                'opt_fetch_carrier_and_exact_supplies': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'water2'},
                        {'primitive': 'place_on_top', 'role': 'carrier', 'uv': [0.30, 0.30]},
                        {'primitive': 'grasp', 'role': 'juice2'},
                        {'primitive': 'place_on_top', 'role': 'carrier', 'uv': [0.70, 0.30]},
                        {'primitive': 'grasp', 'role': 'crackers2'},
                        {'primitive': 'place_on_top', 'role': 'carrier', 'uv': [0.30, 0.70]},
                        {'primitive': 'grasp', 'role': 'carrier'},
                        {'primitive': 'place_on_top', 'role': 'tool_bench', 'uv': [0.35, 0.80]},
                    ],
                    'after': ['opt_unlock_toolbox_and_light_study_lamp'],
                },
                'opt_deliver_carrier_and_stock_cabinets': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'bulb2', 'approach_role': 'tool_bench'},
                        {'primitive': 'place_on_top', 'role': 'carrier', 'uv': [0.70, 0.70]},
                        {'primitive': 'grasp', 'role': 'carrier'},
                        {'primitive': 'place_on_top', 'role': 'guest_table', 'uv': [0.50, 0.50]},
                        {'primitive': 'grasp', 'role': 'water2', 'approach_role': 'carrier'},
                        {'primitive': 'place_inside', 'role': 'cabinet_west'},
                        {'primitive': 'grasp', 'role': 'crackers2', 'approach_role': 'carrier'},
                        {'primitive': 'place_inside', 'role': 'cabinet_west'},
                        {'primitive': 'grasp', 'role': 'juice2', 'approach_role': 'carrier'},
                        {'primitive': 'place_inside', 'role': 'cabinet_east'},
                    ],
                    'after': ['opt_fetch_carrier_and_exact_supplies'],
                },
                'opt_light_guest_lamp': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'bulb2', 'approach_role': 'carrier'},
                        {'primitive': 'attach', 'role': 'guest_lamp'},
                        {'primitive': 'toggle_on', 'role': 'guest_lamp'},
                    ],
                    'after': ['opt_deliver_carrier_and_stock_cabinets'],
                },
                'opt_close_north_cabinets': {
                    'actions': [
                        {'primitive': 'close', 'role': 'cabinet_west'},
                        {'primitive': 'close', 'role': 'cabinet_east'},
                    ],
                    'after': ['opt_inspect_north_cabinets'],
                },
                # --- HABIT BLIND OVER-FETCH & SEQUENTIAL SUBGOAL PHASES ---
                # Naive reactive agent goes straight south to fetch supplies single-handed BEFORE opening north
                # cabinets (over-fetching an extra water bottle), opens north cabinets & discovers existing stock,
                # stocks the 3 missing items, must walk the surplus water bottle 25 m back south to utility_counter,
                # and then handles books and light bulbs one by one.
                'habit_blind_overfetch_south_to_north': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'water2'},
                        {'primitive': 'place_on_top', 'role': 'guest_table', 'uv': [0.24, 0.30]},
                        {'primitive': 'grasp', 'role': 'water3'},
                        {'primitive': 'place_on_top', 'role': 'guest_table', 'uv': [0.74, 0.30]},
                        {'primitive': 'grasp', 'role': 'juice2'},
                        {'primitive': 'place_on_top', 'role': 'guest_table', 'uv': [0.24, 0.72]},
                        {'primitive': 'grasp', 'role': 'crackers2'},
                        {'primitive': 'place_on_top', 'role': 'guest_table', 'uv': [0.74, 0.72]},
                    ],
                    'after': [],
                },
                'habit_open_discover_and_restock_north': {
                    'actions': [
                        {'primitive': 'open', 'role': 'cabinet_west'},
                        {'primitive': 'open', 'role': 'cabinet_east'},
                        {'primitive': 'grasp', 'role': 'water2', 'approach_role': 'guest_table'},
                        {'primitive': 'place_inside', 'role': 'cabinet_west'},
                        {'primitive': 'grasp', 'role': 'crackers2', 'approach_role': 'guest_table'},
                        {'primitive': 'place_inside', 'role': 'cabinet_west'},
                        {'primitive': 'grasp', 'role': 'juice2', 'approach_role': 'guest_table'},
                        {'primitive': 'place_inside', 'role': 'cabinet_east'},
                        {'primitive': 'close', 'role': 'cabinet_west'},
                        {'primitive': 'close', 'role': 'cabinet_east'},
                    ],
                    'after': ['habit_blind_overfetch_south_to_north'],
                },
                'habit_return_surplus_to_south_utility': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'water3', 'approach_role': 'guest_table'},
                        {'primitive': 'place_on_top', 'role': 'utility_counter', 'xy': [24.64, 0.92]},
                    ],
                    'after': ['habit_open_discover_and_restock_north'],
                },
                'habit_shelve_books_and_single_trip_lamps': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'book1'},
                        {'primitive': 'place_on_top', 'role': 'hall_shelf', 'uv': [0.50, 0.28]},
                        {'primitive': 'grasp', 'role': 'book2'},
                        {'primitive': 'place_on_top', 'role': 'hall_shelf', 'uv': [0.50, 0.72]},
                        {'primitive': 'open', 'role': 'toolbox'},
                        {'primitive': 'grasp', 'role': 'bulb1', 'approach_role': 'toolbox'},
                        {'primitive': 'attach', 'role': 'study_lamp'},
                        {'primitive': 'toggle_on', 'role': 'study_lamp'},
                        {'primitive': 'grasp', 'role': 'bulb2', 'approach_role': 'toolbox'},
                        {'primitive': 'attach', 'role': 'guest_lamp'},
                        {'primitive': 'toggle_on', 'role': 'guest_lamp'},
                        {'primitive': 'close', 'role': 'toolbox'},
                    ],
                    'after': ['habit_return_surplus_to_south_utility'],
                },
            },
            'orders': {
                'optimal_inspect_unlock_carrier_loop': [
                    'opt_inspect_north_cabinets',
                    'opt_clear_and_shelve_books',
                    'opt_unlock_toolbox_and_light_study_lamp',
                    'opt_fetch_carrier_and_exact_supplies',
                    'opt_deliver_carrier_and_stock_cabinets',
                    'opt_light_guest_lamp',
                    'opt_close_north_cabinets',
                ],
                'habit_blind_overfetch_sequential': [
                    'habit_blind_overfetch_south_to_north',
                    'habit_open_discover_and_restock_north',
                    'habit_return_surplus_to_south_utility',
                    'habit_shelve_books_and_single_trip_lamps',
                ],
            },
            'negative_omissions': {
                'violate_toolbox_book_guard': 'opt_clear_and_shelve_books',
                'omit_cabinet_restock': 'opt_deliver_carrier_and_stock_cabinets',
                'omit_guest_lamp_lighting': 'opt_light_guest_lamp',
                'omit_cabinet_doors_closing': 'opt_close_north_cabinets',
            },
        },
        'settle_steps': 30,
    }

    validate_spec(spec, require_frozen=False)
    validate_workflow_plan(spec)
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--scene-file',
        type=Path,
        default=None,
        help='Optional external initial_scene.json path to generate frozen task.json without storing scene in Git',
    )
    parser.add_argument(
        '--frozen-scene-relpath',
        type=str,
        default='initial_scene.json',
        help='Relative path written into frozen_instance.path inside task.json',
    )
    args = parser.parse_args()

    out_dir = HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / 'recipe.json'
    spec = build_recipe()
    validation = validate_spec(spec, require_frozen=False)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    print('WROTE:', out)
    print('VALIDATION:', json.dumps(validation, indent=2))

    scene_file = args.scene_file or (out_dir / 'initial_scene.json')
    if scene_file.is_file():
        frozen = dict(spec)
        frozen.pop('construction_seed', None)
        frozen['frozen_instance'] = {
            'path': args.frozen_scene_relpath,
            'sha256': sha256(scene_file),
        }
        task_out = out_dir / 'task.json'
        task_out.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + '\n')
        if (out_dir / args.frozen_scene_relpath).is_file():
            _, frozen_contract = load_spec(task_out, require_frozen=True)
            print('FROZEN CONTRACT:', json.dumps(frozen_contract, indent=2))
        print('WROTE FROZEN:', task_out)


if __name__ == '__main__':
    main()
