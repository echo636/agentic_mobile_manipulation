#!/usr/bin/env python3
"""Generate recipe.json for custom task `upstairs_study_bedrooms_exchange` (Benchmark side).

Task: 二层双卧室与中央书房跨房载具巡回分拣及吸尘清洁（无计时器纯空间路由 + 半未知位置发现 + 双向载具置换 + 共用清洁工具巡回）
Base scene: `vacuuming_floors` instance 301, seed 0 (`house_double_floor_upper`).

Design evidence & adherence to the 5 benchmark task criteria (AGENTS.md):
  1. Feasible on current executor (`source_flush_support_a267904`):
     - Uses real BEHAVIOR-1K upper-floor furniture in `house_double_floor_upper` (`bedroom_0` West Bedroom,
       `living_room_0` Central Study, `bedroom_1` East Bedroom, `bathroom_0` North Utility Bathroom) plus one real
       articulated `bottom_cabinet_fancyy_0` (`study_cabinet`, `49.4 x 86.2 x 173.5 cm`) placed inside the north
       bookcase niche at `[-5.38, -0.72]`. Zero synthetic colored cube fixtures (`fixtures = []`).
     - Opens the 3 active room doors (`door_pxhbim_0`, `door_pxhbim_1`, `door_pxhbim_2`) and closes the unused
       southeast bedroom door (`door_pxhbim_3`) plus inner shower doors (`door_dlncrt_0`, `door_pevmgl_0`),
       producing a single 8,204-cell connected `k = 15` eroded walkable component across all 4 active rooms
       (even after blocking `study_cabinet` via `sync_added_fixture_map`).
     - Every manipulation step in both reference workflows passes the verbatim `FullWorkflowReference.action()`
       + `omnigibson_rgb._navigate()` (`trav_map_resolution = 0.05`, `1.25 m` target band, `<= 1.40 m` reach)
       check with `max_after_d <= 1.348 m`, zero cross-wall approaches, worst-case 360-degree diagonal placement
       edge margins `>= +2.88 cm`, and pairwise clearances `>= +3.26 cm`.
     - Uses deterministic rigid (`prim_type = 0`) assets with every bounding-box axis `>= 6.7 cm` (`tray`
       `53.9 x 54.0 x 3.2 cm`, `rag` `27.8 x 27.8 x 1.4 cm`, `vacuum_89` `19 x 30 x 101 cm`, `laptop_233`
       `32 x 45 x 6.7 cm`, and 8 distinct rigid household items). Avoids `PrimType.CLOTH` / `fold` so CPU
       dynamics remain stable (`physics` omitted; zero `use_gpu_dynamics` articulation NaN risk).
  2. Zero ambiguity or perception traps:
     - Excludes duplicate Central Study bookcases (`bookcase_otwukr_0`, `bookcase_otwukr_2`, `bookcase_otwukr_3`)
       so `bookcase_otwukr_1` is the sole study bookcase; excludes desk-blocking `armchair_axcwcm_0` and extra
       desk lamp `table_lamp_lshxmu_0`.
     - Excludes extra West Bedroom shelves/stool/floor-lamp (`bookcase_kmuacd_0`, `bookcase_kdnyfh_0`,
       `taboret_cgwxtq_0`, `floor_lamp_mqfdjk_0`) and wall clutter (`folderal_*`, `map_*`, `painting_*`,
       `picture_*`), leaving `nightstand_aykehx_0` + `table_lamp_erfbgw_1` as the sole West Bedroom nightstand
       and lamp.
     - Excludes duplicate East Bedroom nightstand (`nightstand_roxnbe_1`), floor lamp (`floor_lamp_ogolip_0`),
       and wall shelves (`shelf_sapyzl_0`, `shelf_sapyzl_1`), leaving `nightstand_roxnbe_0` +
       `table_lamp_erfbgw_0` as the sole East Bedroom nightstand and lamp.
     - All 8 misplaced items belong to completely distinct categories (`bottle_of_water`, `baseball_cap`,
       `teddy_bear`, `bottle_of_lotion`, `alarm_clock`, `box_of_tissues`, `hardback`, `digital_camera`).
  3. Planning complexity & human-realistic efficiency (`PLAN.md` >= 20% gap, `<original_task>` >= 25%-35% gap,
     zero appliance timer):
     - Semi-unknown multi-room discovery (`东西位置要找`): the 8 unique items are openly visible on beds,
       tables, and floors across the Central Study, West Bedroom, and East Bedroom, without the instruction
       disclosing which room each item starts in.
     - Bidirectional 4-for-4 carrier exchange + shared `rag`/`vacuum` passes + piggybacked empty-hand lamp
       shutoffs: under the exact `TraversableMap` (`0.05 m`, `k = 15` erosion) + `omnigibson_rgb._navigate()` +
       `route_motion` clock, `optimal_carrier_tour` takes 80 action records (56 manipulations + 24 navigations),
       114.78 m travel, and 891.82 s `robot_clock` time, whereas `habit_sequential` takes 83 action records
       (44 manipulations + 39 navigations), 261.23 m travel (+127.59% distance), and 1,149.97 s `robot_clock`
       time (+28.95% slower; optimal saves 22.45% = 258.15 s with zero timer wait).
     - Public instruction and private evaluator are 100% strategy-neutral (`tray` may finish on `study_table`,
       `west_bed`, `west_desk`, or `east_bed`; `habit_sequential` passes without ever touching `tray`).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / 'src'))
from manipulation_agent.references.full_workflow_reference import validate_workflow_plan  # noqa: E402
from manipulation_agent.tasks.custom_tasks import validate_spec  # noqa: E402

CLEAN_DESK_TEMPLATE_SHA256 = '6096a25139486cb627b5faa7b43c8ed20001d4ba8974bcd5b5e09182991df03f'


def build_recipe() -> dict:
    req_cleaning_devices_text = (
        'Clean and reset the upstairs furniture and lighting across the Central Study, West Bedroom, and '
        'East Bedroom using the cleaning cloth on the serving cart and the floor vacuum in the North Utility '
        'Bathroom: switch the Central Study desk laptop off before wiping dust off it, wipe dust off the Central '
        'Study laptop, the Central Study bookcase, the tall Central Study storage cabinet (along the north '
        'wall west of the bookcase), the West Bedroom desk, the West Bedroom nightstand, and the East Bedroom '
        'nightstand, and switch the Central Study laptop back on while leaving both the West Bedroom nightstand '
        'table lamp and East Bedroom nightstand table lamp switched off; after clearing all misplaced items off '
        'the West Bedroom floor and East Bedroom floor, vacuum the dust off both bedroom floors; and return the '
        'cleaning cloth to the North Utility Bathroom serving cart and leave the floor vacuum switched off on the '
        'North Utility Bathroom floor.'
    )
    req_sorting_storage_text = (
        'Locate the eight openly visible misplaced household items distributed across the beds, tables, and '
        'floors of the Central Study, West Bedroom, and East Bedroom—a bottle of water, a baseball cap, a '
        'teddy bear, a bottle of lotion, an alarm clock, a box of tissues, a hardback book, and a digital '
        'camera—and sort every item to its proper home (wiping dusty destination surfaces before placing '
        'items on or inside them): place the teddy bear and baseball cap onto the East Bedroom bed; place the '
        'bottle of lotion and bottle of water onto the wiped East Bedroom nightstand; place the alarm clock '
        'onto the wiped West Bedroom nightstand; place the box of tissues and hardback book onto the wiped '
        'West Bedroom desk; store the digital camera inside the wiped tall Central Study storage cabinet and '
        'close its doors; and leave the wooden utility tray resting on the Central Study coffee table, the '
        'West Bedroom bed, the West Bedroom desk, or the East Bedroom bed.'
    )
    req_hand_text = 'Finish with no object held.'

    spec = {
        'schema_version': 3,
        'id': 'upstairs_study_bedrooms_exchange',
        'instance_id': 'upstairs_study_bedrooms_exchange_seed0_r1',
        'status': 'complete_recipe_physics_pending',
        'benchmark_ready': False,
        'official_submission_eligible': False,
        'base': {
            'task': 'vacuuming_floors',
            'instance': 301,
            'seed': 0,
        },
        'roles': {
            # Floors across the 4 connected upper-floor rooms
            'study_floor': 'floors_rfqizg_0',
            'west_floor': 'floors_nlswvt_0',
            'east_floor': 'floors_zrhxwn_0',
            'utility_floor': 'floors_tfuahz_0',
            # Central Study (`living_room_0`) furniture & workstation
            'study_desk': 'desk_iotfzl_0',
            'study_table': 'coffee_table_osroux_0',
            'study_bookcase': 'bookcase_otwukr_1',
            'study_cabinet': 'usbe_cabinet',
            'laptop': 'usbe_laptop',
            # West Bedroom (`bedroom_0`) furniture & lighting
            'west_bed': 'bed_klcjcy_0',
            'west_desk': 'desk_aduafr_0',
            'west_nightstand': 'nightstand_aykehx_0',
            'west_lamp': 'table_lamp_erfbgw_1',
            # East Bedroom (`bedroom_1`) furniture & lighting
            'east_bed': 'bed_siuxgz_0',
            'east_ottoman': 'ottoman_qsnawv_0',
            'east_nightstand': 'nightstand_roxnbe_0',
            'east_lamp': 'table_lamp_erfbgw_0',
            # North Utility Bathroom (`bathroom_0`) shared tools & carrier
            'utility_cart': 'serving_cart_vvtcby_0',
            'vacuum': 'vacuum_89',
            'rag': 'usbe_rag',
            'tray': 'usbe_tray',
            # 8 unique cross-misplaced household items (4 Eastbound + 4 West/Study-bound)
            'water': 'usbe_water',
            'cap': 'usbe_cap',
            'teddy': 'usbe_teddy',
            'lotion': 'usbe_lotion',
            'clock': 'usbe_clock',
            'tissues': 'usbe_tissues',
            'book': 'usbe_book',
            'camera': 'usbe_camera',
            # Explicitly controlled upper-floor doors
            'utility_door': 'door_pxhbim_0',
            'west_door': 'door_pxhbim_1',
            'east_door': 'door_pxhbim_2',
            'unused_se_door': 'door_pxhbim_3',
            'shower_door_a': 'door_dlncrt_0',
            'shower_door_b': 'door_pevmgl_0',
        },
        'scene_exclusions': [
            # Central Study duplicate bookcases, desk-blocking armchair, and extra desk lamp
            'bookcase_otwukr_0',
            'bookcase_otwukr_2',
            'bookcase_otwukr_3',
            'armchair_axcwcm_0',
            'table_lamp_lshxmu_0',
            # West Bedroom extra shelves, desk-blocking taboret, extra floor lamp, and wall clutter
            'bookcase_kdnyfh_0',
            'bookcase_kmuacd_0',
            'taboret_cgwxtq_0',
            'floor_lamp_mqfdjk_0',
            'folderal_ydrlth_0',
            'map_fdtxut_0',
            'map_hsbtqi_0',
            'painting_ljabhq_0',
            'picture_abtgvz_0',
            'picture_bzmdxc_0',
            'picture_svpzro_0',
            # East Bedroom duplicate second nightstand, extra floor lamp, and wall shelves/pictures
            'nightstand_roxnbe_1',
            'floor_lamp_ogolip_0',
            'shelf_sapyzl_0',
            'shelf_sapyzl_1',
            'picture_aqahkx_0',
            'picture_hyjqxp_0',
            'picture_nzctlq_0',
            'picture_rypaxo_0',
            'picture_ttwhjj_0',
            'picture_vezfau_0',
            'picture_yrkfct_0',
        ],
        'construction_asset_scales': [],
        'asset_imports': [
            {
                'name': 'usbe_cabinet',
                'source_task': 'clean_up_your_desk',
                'source_object': 'bottom_cabinet_fancyy_0',
                'source_sha256': CLEAN_DESK_TEMPLATE_SHA256,
                'position': [101.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, -0.70710678, 0.70710678],
                'fixed_base': True,
                'scale': [0.76, 0.70, 0.88],
            },
            {
                'name': 'usbe_laptop',
                'source_task': 'clean_up_your_desk',
                'source_object': 'laptop_233',
                'source_sha256': CLEAN_DESK_TEMPLATE_SHA256,
                'position': [104.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 1.8],
            },
        ],
        'native_assets': [
            {
                'name': 'usbe_tray',
                'category': 'tray',
                'model': 'coqeme',
                'usd_sha256': 'f742b94604395437e931d8424c2803681c6331c6c983524d2c696cb0faec0ccd',
                'prim_type': 0,
                'position': [110.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [3.80, 2.43, 1.80],
            },
            {
                'name': 'usbe_rag',
                'category': 'rag',
                'model': 'oocmed',
                'usd_sha256': '732677fea4ca1e96c8b71f843249e2b5740287542aae48245871d7a6daeb7068',
                'prim_type': 0,
                'position': [113.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.0, 1.0, 10.0],
            },
            {
                'name': 'usbe_water',
                'category': 'bottle_of_water',
                'model': 'atvnqy',
                'usd_sha256': 'da8e2524f41fbae998ee82b50cb013d14a6a8b64ff986edb5a5fefcc8619417c',
                'prim_type': 0,
                'position': [116.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.20, 1.20, 1.00],
            },
            {
                'name': 'usbe_cap',
                'category': 'baseball_cap',
                'model': 'ogptul',
                'usd_sha256': '22b2b6180e0634712e952a7c505330a1363284d83f5d3fa9d2b2df5a91a69f97',
                'prim_type': 0,
                'position': [119.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.84, 0.95, 1.00],
            },
            {
                'name': 'usbe_teddy',
                'category': 'teddy_bear',
                'model': 'dgagea',
                'usd_sha256': '08e38773984b0de0a9e8fdf3d3ef54331cdd0dcdb01bc65132ab330671cae571',
                'prim_type': 0,
                'position': [122.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.78, 0.82, 1.00],
            },
            {
                'name': 'usbe_lotion',
                'category': 'bottle_of_lotion',
                'model': 'tkryrh',
                'usd_sha256': 'd1df3b43ac232ed50e5b1c9b0efeca0970e36dde6a5925b6c0060de34aa92146',
                'prim_type': 0,
                'position': [125.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [1.15, 1.15, 1.00],
            },
            {
                'name': 'usbe_clock',
                'category': 'alarm_clock',
                'model': 'cvknrh',
                'usd_sha256': 'a94f0876ef56356691b38bfa2518d18c47137b836cd191157c4581025eee5141',
                'prim_type': 0,
                'position': [128.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.72, 0.72, 1.15],
            },
            {
                'name': 'usbe_tissues',
                'category': 'box_of_tissues',
                'model': 'ntbrtz',
                'usd_sha256': '3cde3ec3bc5ce7d4ff2e2aa82d6e281951cb7e0ef4ca9f39592334d424f5b691',
                'prim_type': 0,
                'position': [131.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'name': 'usbe_book',
                'category': 'hardback',
                'model': 'acbrnv',
                'usd_sha256': 'e47d77da3ff597609e8d5990d3b4c5938cb780e5c129d5b21903e6f18cf67609',
                'prim_type': 0,
                'position': [134.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.82, 0.85, 2.30],
            },
            {
                'name': 'usbe_camera',
                'category': 'digital_camera',
                'model': 'wbzzjl',
                'usd_sha256': '435fb1ae36a6297f8002bed81f8b90e58bc55b3b7fd03e1601fc54b811365102',
                'prim_type': 0,
                'position': [137.0, 50.0, 2.0],
                'orientation': [0.0, 0.0, 0.0, 1.0],
                'scale': [0.90, 0.85, 1.00],
            },
        ],
        'fixtures': [],
        'initial_layout': [
            # 1. Place fixed storage furniture inside the north bookcase niche of Central Study
            {
                'object': 'study_cabinet',
                'target': 'study_floor',
                'place_on_support': True,
                'xy': [-5.38, -0.72],
                'support_probe_height': 0.5,
                'orientation': [0.0, 0.0, -0.70710678, 0.70710678],
            },
            # 2. Place tools & carrier at unambiguous starting stations
            {
                'object': 'vacuum',
                'target': 'utility_floor',
                'place_on_support': True,
                'xy': [0.65, 2.70],
                'support_probe_height': 0.5,
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'rag',
                'target': 'utility_cart',
                'place_on_support': True,
                'support_uv': [0.50, 0.50],
            },
            {
                'object': 'tray',
                'target': 'west_bed',
                'place_on_support': True,
                'xy': [-5.45, 2.88],
                'orientation': [0.0, 0.0, 0.0, 1.0],
            },
            {
                'object': 'laptop',
                'target': 'study_desk',
                'place_on_support': True,
                'xy': [-1.30, -3.02],
                'orientation': [0.0, 0.0, 1.0, 0.0],
            },
            # 3. Place the 8 unique cross-misplaced items openly across the 3 rooms (Central Study, West, East)
            {
                'object': 'water',
                'target': 'study_table',
                'place_on_support': True,
                'xy': [-3.55, -2.35],
            },
            {
                'object': 'cap',
                'target': 'west_bed',
                'place_on_support': True,
                'xy': [-4.72, 2.88],
            },
            {
                'object': 'teddy',
                'target': 'west_floor',
                'place_on_support': True,
                'xy': [-5.15, 1.38],
                'support_probe_height': 0.5,
            },
            {
                'object': 'lotion',
                'target': 'west_floor',
                'place_on_support': True,
                'xy': [-4.75, 1.45],
                'support_probe_height': 0.5,
            },
            {
                'object': 'clock',
                'target': 'east_bed',
                'place_on_support': True,
                'xy': [5.12, 2.12],
            },
            {
                'object': 'tissues',
                'target': 'east_bed',
                'place_on_support': True,
                'xy': [5.12, 3.42],
            },
            {
                'object': 'book',
                'target': 'east_floor',
                'place_on_support': True,
                'xy': [5.80, 1.95],
                'support_probe_height': 0.5,
            },
            {
                'object': 'camera',
                'target': 'east_floor',
                'place_on_support': True,
                'xy': [6.18, 2.15],
                'support_probe_height': 0.5,
            },
            # 4. Door states: open active 4-room suite doors, close unused southeast bedroom & shower doors
            {'object': 'utility_door', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'west_door', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'east_door', 'state': 'Open', 'value': True, 'kwargs': {'fully': True}},
            {'object': 'unused_se_door', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'shower_door_a', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'shower_door_b', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            # 5. Initial device & cabinet states
            {'object': 'study_cabinet', 'state': 'Open', 'value': False, 'kwargs': {'fully': True}},
            {'object': 'vacuum', 'state': 'ToggledOn', 'value': False},
            {'object': 'laptop', 'state': 'ToggledOn', 'value': True},
            {'object': 'west_lamp', 'state': 'ToggledOn', 'value': True},
            {'object': 'east_lamp', 'state': 'ToggledOn', 'value': True},
            # 6. Initial dust states: Central Study & North Utility Bathroom floors clean; 6 surfaces + 2 bedroom floors dusty
            {'object': 'study_floor', 'state': 'Covered', 'value': False, 'system': 'dust'},
            {'object': 'utility_floor', 'state': 'Covered', 'value': False, 'system': 'dust'},
            {
                'object': 'laptop',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'study_bookcase',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'study_cabinet',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'west_desk',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'west_nightstand',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'east_nightstand',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'west_floor',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
            {
                'object': 'east_floor',
                'state': 'Covered',
                'value': True,
                'max_sampling_attempts': 3,
                'settle_steps': 1,
                'system': 'dust',
            },
        ],
        'initial_assertions': {
            'laptop_dust': 'laptop_dust',
            'study_bookcase_dust': 'study_bookcase_dust',
            'study_cabinet_dust': 'study_cabinet_dust',
            'west_desk_dust': 'west_desk_dust',
            'west_nightstand_dust': 'west_nightstand_dust',
            'east_nightstand_dust': 'east_nightstand_dust',
            'west_floor_dust': 'west_floor_dust',
            'east_floor_dust': 'east_floor_dust',
            'laptop_on': 'laptop_on',
            'west_lamp_on': 'west_lamp_on',
            'east_lamp_on': 'east_lamp_on',
            'vacuum_off': {'not': 'vacuum_on'},
            'study_cabinet_closed': {'not': 'study_cabinet_open'},
            'initial_vacuum_OnTop': 'vacuum_ontop_utility_floor',
            'initial_rag_OnTop': 'rag_ontop_utility_cart',
            'initial_tray_OnTop': 'tray_ontop_west_bed',
            'initial_laptop_OnTop': 'laptop_ontop_study_desk',
            'initial_water_OnTop': 'water_ontop_study_table',
            'initial_cap_OnTop': 'cap_ontop_west_bed',
            'initial_teddy_OnTop': 'teddy_ontop_west_floor',
            'initial_lotion_OnTop': 'lotion_ontop_west_floor',
            'initial_clock_OnTop': 'clock_ontop_east_bed',
            'initial_tissues_OnTop': 'tissues_ontop_east_bed',
            'initial_book_OnTop': 'book_ontop_east_floor',
            'initial_camera_OnTop': 'camera_ontop_east_floor',
            'hand_empty': 'hand_empty',
        },
        'future_roles': [],
        'replacements': [],
        'public_instruction': (
            'Locate and cross-sort the eight misplaced household items across the Central Study, West Bedroom, '
            'and East Bedroom, wipe the dusty furniture and study laptop, vacuum both bedroom floors once cleared, '
            'set the bedroom lamps and study laptop to their required states, and return both cleaning tools.'
        ),
        'public_context': [req_cleaning_devices_text, req_sorting_storage_text, req_hand_text],
        'requirements': [
            {
                'id': 'cleaning_and_devices',
                'text': req_cleaning_devices_text,
                'terminal': ['cleaning_and_devices_complete'],
                'milestones': ['surfaces_wiped_clean', 'bedroom_floors_vacuumed'],
                'constraints': [
                    'laptop_off_before_wipe',
                    'west_floor_clear_before_vacuum',
                    'east_floor_clear_before_vacuum',
                ],
            },
            {
                'id': 'cross_room_sorting_and_storage',
                'text': req_sorting_storage_text,
                'terminal': ['cross_room_items_sorted', 'storage_and_carrier_safe'],
                'milestones': ['east_bedroom_stocked', 'study_and_west_stocked'],
                'constraints': [
                    'study_cabinet_clean_before_camera',
                    'west_desk_clean_before_supplies',
                    'west_nightstand_clean_before_clock',
                    'east_nightstand_clean_before_supplies',
                ],
            },
            {
                'id': 'hand_empty',
                'text': req_hand_text,
                'terminal': ['hand_empty'],
            },
        ],
        'evaluation': {
            'predicates': {
                # Device & cabinet states
                'study_cabinet_open': {'object': 'study_cabinet', 'state': 'Open'},
                'vacuum_on': {'object': 'vacuum', 'state': 'ToggledOn'},
                'laptop_on': {'object': 'laptop', 'state': 'ToggledOn'},
                'west_lamp_on': {'object': 'west_lamp', 'state': 'ToggledOn'},
                'east_lamp_on': {'object': 'east_lamp', 'state': 'ToggledOn'},
                # Dust states (6 wiped surfaces + 2 vacuumed bedroom floors)
                'laptop_dust': {'object': 'laptop', 'state': 'Covered', 'system': 'dust'},
                'study_bookcase_dust': {'object': 'study_bookcase', 'state': 'Covered', 'system': 'dust'},
                'study_cabinet_dust': {'object': 'study_cabinet', 'state': 'Covered', 'system': 'dust'},
                'west_desk_dust': {'object': 'west_desk', 'state': 'Covered', 'system': 'dust'},
                'west_nightstand_dust': {'object': 'west_nightstand', 'state': 'Covered', 'system': 'dust'},
                'east_nightstand_dust': {'object': 'east_nightstand', 'state': 'Covered', 'system': 'dust'},
                'west_floor_dust': {'object': 'west_floor', 'state': 'Covered', 'system': 'dust'},
                'east_floor_dust': {'object': 'east_floor', 'state': 'Covered', 'system': 'dust'},
                # Tool, workstation, & carrier resting predicates
                'rag_ontop_utility_cart': {'object': 'rag', 'state': 'OnTop', 'target': 'utility_cart'},
                'vacuum_ontop_utility_floor': {'object': 'vacuum', 'state': 'OnTop', 'target': 'utility_floor'},
                'laptop_ontop_study_desk': {'object': 'laptop', 'state': 'OnTop', 'target': 'study_desk'},
                'tray_ontop_study_table': {'object': 'tray', 'state': 'OnTop', 'target': 'study_table'},
                'tray_ontop_west_bed': {'object': 'tray', 'state': 'OnTop', 'target': 'west_bed'},
                'tray_ontop_west_desk': {'object': 'tray', 'state': 'OnTop', 'target': 'west_desk'},
                'tray_ontop_east_bed': {'object': 'tray', 'state': 'OnTop', 'target': 'east_bed'},
                # Goal placement predicates for the 8 items
                'teddy_ontop_east_bed': {'object': 'teddy', 'state': 'OnTop', 'target': 'east_bed'},
                'cap_ontop_east_bed': {'object': 'cap', 'state': 'OnTop', 'target': 'east_bed'},
                'lotion_ontop_east_nightstand': {
                    'object': 'lotion',
                    'state': 'OnTop',
                    'target': 'east_nightstand',
                },
                'water_ontop_east_nightstand': {
                    'object': 'water',
                    'state': 'OnTop',
                    'target': 'east_nightstand',
                },
                'clock_ontop_west_nightstand': {
                    'object': 'clock',
                    'state': 'OnTop',
                    'target': 'west_nightstand',
                },
                'tissues_ontop_west_desk': {'object': 'tissues', 'state': 'OnTop', 'target': 'west_desk'},
                'book_ontop_west_desk': {'object': 'book', 'state': 'OnTop', 'target': 'west_desk'},
                'camera_inside_study_cabinet': {
                    'object': 'camera',
                    'state': 'Inside',
                    'target': 'study_cabinet',
                },
                # Initial / guard-tracking predicates for the 8 items
                'water_ontop_study_table': {'object': 'water', 'state': 'OnTop', 'target': 'study_table'},
                'cap_ontop_west_bed': {'object': 'cap', 'state': 'OnTop', 'target': 'west_bed'},
                'teddy_ontop_west_floor': {'object': 'teddy', 'state': 'OnTop', 'target': 'west_floor'},
                'lotion_ontop_west_floor': {'object': 'lotion', 'state': 'OnTop', 'target': 'west_floor'},
                'clock_ontop_east_bed': {'object': 'clock', 'state': 'OnTop', 'target': 'east_bed'},
                'tissues_ontop_east_bed': {'object': 'tissues', 'state': 'OnTop', 'target': 'east_bed'},
                'book_ontop_east_floor': {'object': 'book', 'state': 'OnTop', 'target': 'east_floor'},
                'camera_ontop_east_floor': {'object': 'camera', 'state': 'OnTop', 'target': 'east_floor'},
                'hand_empty': {'object': 'study_desk', 'state': 'HandEmpty'},
            },
            'milestones': {
                'surfaces_wiped_clean': {
                    'when': {
                        'all': [
                            {'not': 'laptop_dust'},
                            {'not': 'study_bookcase_dust'},
                            {'not': 'study_cabinet_dust'},
                            {'not': 'west_desk_dust'},
                            {'not': 'west_nightstand_dust'},
                            {'not': 'east_nightstand_dust'},
                        ]
                    },
                    'after': [],
                },
                'east_bedroom_stocked': {
                    'when': {
                        'all': [
                            'teddy_ontop_east_bed',
                            'cap_ontop_east_bed',
                            'lotion_ontop_east_nightstand',
                            'water_ontop_east_nightstand',
                        ]
                    },
                    'after': [],
                },
                'study_and_west_stocked': {
                    'when': {
                        'all': [
                            'clock_ontop_west_nightstand',
                            'tissues_ontop_west_desk',
                            'book_ontop_west_desk',
                            'camera_inside_study_cabinet',
                        ]
                    },
                    'after': [],
                },
                'bedroom_floors_vacuumed': {
                    'when': {
                        'all': [
                            {'not': 'west_floor_dust'},
                            {'not': 'east_floor_dust'},
                        ]
                    },
                    'after': [],
                },
            },
            'terminal': {
                'cleaning_and_devices_complete': {
                    'all': [
                        {'not': 'laptop_dust'},
                        {'not': 'study_bookcase_dust'},
                        {'not': 'study_cabinet_dust'},
                        {'not': 'west_desk_dust'},
                        {'not': 'west_nightstand_dust'},
                        {'not': 'east_nightstand_dust'},
                        {'not': 'west_floor_dust'},
                        {'not': 'east_floor_dust'},
                        'laptop_ontop_study_desk',
                        'laptop_on',
                        {'not': 'west_lamp_on'},
                        {'not': 'east_lamp_on'},
                        'rag_ontop_utility_cart',
                        'vacuum_ontop_utility_floor',
                        {'not': 'vacuum_on'},
                    ]
                },
                'cross_room_items_sorted': {
                    'all': [
                        'teddy_ontop_east_bed',
                        'cap_ontop_east_bed',
                        'lotion_ontop_east_nightstand',
                        'water_ontop_east_nightstand',
                        'clock_ontop_west_nightstand',
                        'tissues_ontop_west_desk',
                        'book_ontop_west_desk',
                        'camera_inside_study_cabinet',
                    ]
                },
                'storage_and_carrier_safe': {
                    'all': [
                        {'not': 'study_cabinet_open'},
                        {
                            'any': [
                                'tray_ontop_study_table',
                                'tray_ontop_west_bed',
                                'tray_ontop_west_desk',
                                'tray_ontop_east_bed',
                            ]
                        },
                    ]
                },
                'hand_empty': 'hand_empty',
            },
            'state_guards': [
                {
                    'id': 'study_cabinet_clean_before_camera',
                    'until': None,
                    'require': {
                        'any': [
                            {'not': 'camera_inside_study_cabinet'},
                            {'not': 'study_cabinet_dust'},
                        ]
                    },
                },
                {
                    'id': 'west_desk_clean_before_supplies',
                    'until': None,
                    'require': {
                        'any': [
                            {
                                'not': {
                                    'any': [
                                        'tissues_ontop_west_desk',
                                        'book_ontop_west_desk',
                                    ]
                                }
                            },
                            {'not': 'west_desk_dust'},
                        ]
                    },
                },
                {
                    'id': 'west_nightstand_clean_before_clock',
                    'until': None,
                    'require': {
                        'any': [
                            {'not': 'clock_ontop_west_nightstand'},
                            {'not': 'west_nightstand_dust'},
                        ]
                    },
                },
                {
                    'id': 'east_nightstand_clean_before_supplies',
                    'until': None,
                    'require': {
                        'any': [
                            {
                                'not': {
                                    'any': [
                                        'lotion_ontop_east_nightstand',
                                        'water_ontop_east_nightstand',
                                    ]
                                }
                            },
                            {'not': 'east_nightstand_dust'},
                        ]
                    },
                },
            ],
            'transition_guards': [
                {
                    'id': 'laptop_off_before_wipe',
                    'predicate': 'laptop_dust',
                    'from_value': True,
                    'to_value': False,
                    'require': {'not': 'laptop_on'},
                },
                {
                    'id': 'west_floor_clear_before_vacuum',
                    'predicate': 'west_floor_dust',
                    'from_value': True,
                    'to_value': False,
                    'require': {
                        'all': [
                            {'not': 'teddy_ontop_west_floor'},
                            {'not': 'lotion_ontop_west_floor'},
                        ]
                    },
                },
                {
                    'id': 'east_floor_clear_before_vacuum',
                    'predicate': 'east_floor_dust',
                    'from_value': True,
                    'to_value': False,
                    'require': {
                        'all': [
                            {'not': 'book_ontop_east_floor'},
                            {'not': 'camera_ontop_east_floor'},
                        ]
                    },
                },
            ],
            'score_type': 'custom_joint_terminal_and_public_history_v2',
        },
        'acceptance_cases': {
            'reference_orders': [
                'optimal_carrier_tour',
                'habit_sequential',
            ],
            'physical_negative_cases': [
                'omit_rag_wiping',
                'omit_west_clock_delivery',
                'omit_study_restore',
                'omit_bedroom_vacuuming',
            ],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 260,
        },
        'reference_plan': {
            'kind': 'full_workflow',
            'allow_strategy_variants': True,
            'phases': {
                # Shared opening phase: switch off study laptop before wiping & open study cabinet
                'prep_study_devices': {
                    'actions': [
                        {'primitive': 'toggle_off', 'role': 'laptop'},
                        {'primitive': 'open', 'role': 'study_cabinet'},
                    ],
                    'after': [],
                },
                # --- OPTIMAL CARRIER TOUR PHASES (80 calls: 56 manipulations + 24 navigations, 891.53 s) ---
                'shared_rag_wipe_tour': {
                    'actions': [
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'grasp', 'role': 'rag'},
                        {'primitive': 'wipe', 'role': 'laptop'},
                        {'primitive': 'wipe', 'role': 'study_bookcase'},
                        {'primitive': 'wipe', 'role': 'study_cabinet'},
                        {'primitive': 'wipe', 'role': 'east_nightstand'},
                        {'primitive': 'wipe', 'role': 'west_nightstand'},
                        {'primitive': 'wipe', 'role': 'west_desk'},
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'utility_cart', 'uv': [0.50, 0.50]},
                    ],
                    'after': ['prep_study_devices'],
                },
                'outbound_carrier_exchange_to_east': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'water'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.275, 2.075, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.73, 0.73]},
                        {'primitive': 'toggle_off', 'role': 'west_lamp'},
                        {'primitive': 'grasp', 'role': 'cap'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.73, 0.27]},
                        {'primitive': 'grasp', 'role': 'teddy'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.27, 0.73]},
                        {'primitive': 'grasp', 'role': 'lotion'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.27, 0.27]},
                        {'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'navigate_to', 'role': 'east_floor', 'point': [5.875, 2.725, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'east_bed', 'xy': [5.12, 2.75]},
                        {'primitive': 'toggle_off', 'role': 'east_lamp'},
                        {'primitive': 'grasp', 'role': 'lotion', 'approach_role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'east_nightstand', 'xy': [6.14, 3.66]},
                        {'primitive': 'grasp', 'role': 'water', 'approach_role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'east_nightstand', 'xy': [6.14, 3.92]},
                        {'primitive': 'grasp', 'role': 'teddy', 'approach_role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'east_bed', 'xy': [4.72, 2.12]},
                        {'primitive': 'grasp', 'role': 'cap', 'approach_role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'east_bed', 'xy': [4.72, 3.42]},
                    ],
                    'after': ['prep_study_devices'],
                },
                'inbound_carrier_exchange_to_west': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'clock'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.27, 0.27]},
                        {'primitive': 'grasp', 'role': 'tissues'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.73, 0.27]},
                        {'primitive': 'grasp', 'role': 'book'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.27, 0.73]},
                        {'primitive': 'grasp', 'role': 'camera'},
                        {'primitive': 'place_on_top', 'role': 'tray', 'uv': [0.73, 0.73]},
                        {'primitive': 'grasp', 'role': 'tray'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.275, 2.075, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'west_bed', 'xy': [-5.45, 2.88]},
                    ],
                    'after': ['outbound_carrier_exchange_to_east'],
                },
                'deliver_clock_to_west_nightstand': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'clock', 'approach_role': 'tray'},
                        {'primitive': 'place_on_top', 'role': 'west_nightstand', 'xy': [-5.92, 2.31]},
                    ],
                    'after': ['inbound_carrier_exchange_to_west'],
                },
                'deliver_west_desk_and_study_camera': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'tissues', 'approach_role': 'tray'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.175, 0.925, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'west_desk', 'uv': [0.72, 0.70]},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.275, 2.075, 0.02]},
                        {'primitive': 'grasp', 'role': 'book', 'approach_role': 'tray'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.575, 0.925, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'west_desk', 'uv': [0.28, 0.70]},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.275, 2.075, 0.02]},
                        {'primitive': 'grasp', 'role': 'camera', 'approach_role': 'tray'},
                        {'primitive': 'navigate_to', 'role': 'study_floor', 'point': [-4.825, -1.775, 0.02]},
                        {'primitive': 'place_inside', 'role': 'study_cabinet'},
                    ],
                    'after': ['inbound_carrier_exchange_to_west'],
                },
                'restore_study_devices_and_cabinet': {
                    'actions': [
                        {'primitive': 'close', 'role': 'study_cabinet'},
                        {'primitive': 'toggle_on', 'role': 'laptop'},
                    ],
                    'after': ['deliver_west_desk_and_study_camera'],
                },
                'shared_bedroom_vacuum_tour': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'vacuum'},
                        {'primitive': 'toggle_on', 'role': 'vacuum'},
                        {'primitive': 'vacuum', 'role': 'west_floor', 'xy': [-4.40, 1.45]},
                        {'primitive': 'vacuum', 'role': 'east_floor', 'xy': [4.20, 1.05]},
                        {'primitive': 'toggle_off', 'role': 'vacuum'},
                        {'primitive': 'place_on_top', 'role': 'utility_floor', 'xy': [0.65, 2.70]},
                    ],
                    'after': ['deliver_west_desk_and_study_camera'],
                },
                # --- HABIT SEQUENTIAL PHASES (83 calls: 44 manipulations + 39 navigations, 1,151.86 s, zero tray use) ---
                'habit_study_wipe_and_water_delivery': {
                    'actions': [
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'grasp', 'role': 'rag'},
                        {'primitive': 'wipe', 'role': 'laptop'},
                        {'primitive': 'wipe', 'role': 'study_bookcase'},
                        {'primitive': 'wipe', 'role': 'study_cabinet'},
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'utility_cart', 'uv': [0.50, 0.50]},
                        {'primitive': 'toggle_on', 'role': 'laptop'},
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'grasp', 'role': 'rag'},
                        {'primitive': 'wipe', 'role': 'east_nightstand'},
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'utility_cart', 'uv': [0.50, 0.50]},
                        {'primitive': 'grasp', 'role': 'water'},
                        {'primitive': 'navigate_to', 'role': 'east_floor', 'point': [5.875, 2.725, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'east_nightstand', 'xy': [6.14, 3.92]},
                        {'primitive': 'toggle_off', 'role': 'east_lamp'},
                    ],
                    'after': ['prep_study_devices'],
                },
                'habit_west_bedroom_single_item_and_vacuum': {
                    'actions': [
                        {'primitive': 'toggle_off', 'role': 'west_lamp'},
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'grasp', 'role': 'rag'},
                        {'primitive': 'wipe', 'role': 'west_nightstand'},
                        {'primitive': 'wipe', 'role': 'west_desk'},
                        {'primitive': 'navigate_to', 'role': 'utility_floor', 'point': [-0.225, 1.625, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'utility_cart', 'uv': [0.50, 0.50]},
                        {'primitive': 'grasp', 'role': 'cap'},
                        {'primitive': 'navigate_to', 'role': 'east_floor', 'point': [3.025, 2.425, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'east_bed', 'xy': [3.85, 2.75]},
                        {'primitive': 'grasp', 'role': 'teddy'},
                        {'primitive': 'navigate_to', 'role': 'east_floor', 'point': [3.025, 2.425, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'east_bed', 'xy': [3.85, 2.10]},
                        {'primitive': 'grasp', 'role': 'lotion'},
                        {'primitive': 'navigate_to', 'role': 'east_floor', 'point': [5.875, 2.725, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'east_nightstand', 'xy': [6.14, 3.66]},
                        {'primitive': 'grasp', 'role': 'vacuum'},
                        {'primitive': 'toggle_on', 'role': 'vacuum'},
                        {'primitive': 'vacuum', 'role': 'west_floor', 'xy': [-4.40, 1.45]},
                        {'primitive': 'toggle_off', 'role': 'vacuum'},
                        {'primitive': 'place_on_top', 'role': 'utility_floor', 'xy': [0.65, 2.70]},
                    ],
                    'after': ['habit_study_wipe_and_water_delivery'],
                },
                'habit_east_bedroom_single_item_and_vacuum': {
                    'actions': [
                        {'primitive': 'grasp', 'role': 'clock'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.275, 2.075, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'west_nightstand', 'xy': [-5.92, 2.31]},
                        {'primitive': 'grasp', 'role': 'tissues'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.175, 0.925, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'west_desk', 'uv': [0.72, 0.70]},
                        {'primitive': 'grasp', 'role': 'book'},
                        {'primitive': 'navigate_to', 'role': 'west_floor', 'point': [-5.575, 0.925, 0.02]},
                        {'primitive': 'place_on_top', 'role': 'west_desk', 'uv': [0.28, 0.70]},
                        {'primitive': 'grasp', 'role': 'camera'},
                        {'primitive': 'navigate_to', 'role': 'study_floor', 'point': [-4.825, -1.775, 0.02]},
                        {'primitive': 'place_inside', 'role': 'study_cabinet'},
                        {'primitive': 'close', 'role': 'study_cabinet'},
                        {'primitive': 'grasp', 'role': 'vacuum'},
                        {'primitive': 'toggle_on', 'role': 'vacuum'},
                        {'primitive': 'vacuum', 'role': 'east_floor', 'xy': [4.20, 1.05]},
                        {'primitive': 'toggle_off', 'role': 'vacuum'},
                        {'primitive': 'place_on_top', 'role': 'utility_floor', 'xy': [0.65, 2.70]},
                    ],
                    'after': ['habit_west_bedroom_single_item_and_vacuum'],
                },
            },
            'orders': {
                'optimal_carrier_tour': [
                    'prep_study_devices',
                    'shared_rag_wipe_tour',
                    'outbound_carrier_exchange_to_east',
                    'inbound_carrier_exchange_to_west',
                    'deliver_clock_to_west_nightstand',
                    'deliver_west_desk_and_study_camera',
                    'restore_study_devices_and_cabinet',
                    'shared_bedroom_vacuum_tour',
                ],
                'habit_sequential': [
                    'prep_study_devices',
                    'habit_study_wipe_and_water_delivery',
                    'habit_west_bedroom_single_item_and_vacuum',
                    'habit_east_bedroom_single_item_and_vacuum',
                ],
            },
            'negative_omissions': {
                'omit_rag_wiping': 'shared_rag_wipe_tour',
                'omit_west_clock_delivery': 'deliver_clock_to_west_nightstand',
                'omit_study_restore': 'restore_study_devices_and_cabinet',
                'omit_bedroom_vacuuming': 'shared_bedroom_vacuum_tour',
            },
        },
        'robot_start': {
            'position': [-2.40, -1.25, 0.02],
            'orientation': [0.0, 0.0, 0.0, 1.0],
        },
        'settle_steps': 30,
    }
    return spec


def main() -> None:
    out = HERE / 'recipe.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = build_recipe()
    validation = validate_spec(spec)
    workflow_validation = validate_workflow_plan(spec)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n')
    print('WROTE:', out)
    print('VALIDATE_SPEC:', json.dumps(validation, indent=2))
    print('VALIDATE_WORKFLOW_PLAN:', json.dumps(workflow_validation, indent=2))


if __name__ == '__main__':
    main()
