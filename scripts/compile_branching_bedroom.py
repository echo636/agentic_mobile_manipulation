"""Compile the shared-library candidate without changing the four older recipes."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compile_recipe():
    s = json.loads((ROOT / 'task_designs/constructed_100plus/bedroom_vacuum_reset_close_102_v1.json').read_text())
    s.update(id='bedroom_shared_library_branching_v2',
             instance_id='bedroom_shared_library_branching_v2_seed0_r4',
             public_instruction='整理两间卧室及共用书柜：分房暂存、吸尘，清空并擦净公共书柜后归书，玩具回各房绿台，清洁暂存台，最后关灯并归还工具。')
    # Exclude this unbound movable footstool in this new instance only: its
    # measured footprint overlaps the old B-room floor-item grid.
    s['scene_exclusions'] = ['ottoman_qsnawv_0']
    s['robot_start'] = dict(position=[-4.65, -2.15, .02],
                             orientation=[0., 0., .70710678, .70710678])
    s['roles'].update(shared_shelf='lh_shared_shelf', clutter_home='lh_clutter_home',
                      wipe_tool='lh_wipe_tool', wipe_home='lh_wipe_home',
                      common_floor='floors_rfqizg_0',
                      a_door='door_pxhbim_1', b_door='door_pxhbim_2')
    s['asset_imports'] += [
        dict(name='lh_shared_shelf', source_task='vacuuming_floors', source_object='bookcase_kmuacd_0',
             source_sha256='3aa56342cc02f704725cdd268ce94e15fa7015dfa8c88fadf01b3f5890772b36',
             scale=[2., 1.6, .7], fixed_base=True, in_rooms=['living_room_0'],
             position=[-4.6, -1.05, .44]),
        dict(name='lh_wipe_tool', source_task='polishing_shoes', source_object='scrub_brush_42',
             source_sha256='74f9a552142c042a4c91b03ed4c0bed19ea941dd85f0192d2ed6966b5c436708',
             position=[-5.7, -2.4, .7], in_rooms=['living_room_0'])]
    for role, xy, size, color in [
        ('clutter_home', [-2.9, -2.3], [1.2, .8], [.12, .32, .85, 1.]),
        ('wipe_home', [-5.7, -2.4], [.6, .6], [.7, .7, .7, 1.])]:
        s['fixtures'].append(dict(name='lh_' + role, position=[*xy, .225], size=[*size, .45],
                                  rgba=color, fixed_base=True))
    e = s['evaluation']
    e.update(terminal={}, milestones={}, state_guards=[], motion_guards=[],
             score_type='custom_branching_library_terminal_history_and_handling_v2')
    p = e['predicates']
    def neg(x): return {'not': x}
    def allof(xs): return {'all': list(xs)}
    def anyof(xs): return {'any': list(xs)}
    def on(obj, target):
        key = obj + '_on_' + target
        p[key] = dict(object=obj, state='OnTop', target=target)
        return key
    def dirty(obj):
        key = obj + '_Covered_dust'
        p[key] = dict(object=obj, state='Covered', system='dust')
        return key
    def guard(key, expr, until=None):
        e['state_guards'].append(dict(id=key, require=expr, until=until))
    def milestone(key, expr, after=()):
        e['milestones'][key] = dict(when=expr, after=list(after))
    def place(obj, target, xy):
        s['initial_layout'].append(dict(object=obj, target=target, xy=xy, place_on_support=True))
        s['initial_assertions'][obj + '_initial_support'] = on(obj, target)

    # The base vacuuming task leaves both doors closed. This task explicitly
    # connects the bedrooms with the shared common area, so author open doors
    # as part of the initial instance and verify them after settling/restore.
    for door in ['a_door', 'b_door']:
        p[door + '_open'] = dict(object=door, state='Open')
        s['initial_layout'].append(dict(object=door, state='Open', value=True,
                                        kwargs={'fully': True}, max_sampling_attempts=3))
        s['initial_assertions'][door + '_starts_open'] = door + '_open'

    place('wipe_tool', 'wipe_home', [-5.7, -2.4])
    clutter = []
    for i in range(6):
        role = f'clutter{i}'
        s['roles'][role] = 'lh_' + role
        s['asset_imports'].append(dict(name='lh_' + role, source_task='clean_up_your_desk',
            source_object='pencil_case_224',
            source_sha256='6096a25139486cb627b5faa7b43c8ed20001d4ba8974bcd5b5e09182991df03f',
            position=[-4.6, -1.05, 1.5 + i * .2], in_rooms=['living_room_0']))
        place(role, 'shared_shelf', [-5.35 + .30 * i, -1.05])
        clutter.append(dict(item=role, destination='clutter_home',
                            destination_xy=[-3.3 + .4 * (i % 3), -2.5 + .4 * (i // 3)]))
        e['terminal'][role + '_stored'] = on(role, 'clutter_home')
    shelf_clear = allof([allof([on(w['item'], 'clutter_home'), neg(on(w['item'], 'shared_shelf'))])
                         for w in clutter])
    for obj in ['shared_shelf', 'a_stage', 'b_stage']:
        s['initial_layout'].append(dict(object=obj, state='Covered', system='dust', value=True,
                                        max_sampling_attempts=3))
        s['initial_assertions'][obj + '_starts_dusty'] = dirty(obj)
        e['terminal'][obj + '_clean'] = neg(dirty(obj))
    milestone('shelf_cleared', shelf_clear)
    milestone('shelf_clean', allof([shelf_clear, neg(dirty('shared_shelf'))]), ['shelf_cleared'])
    guard('clear_shelf_before_wipe', dirty('shared_shelf'), 'shelf_cleared')
    guard('keep_shelf_clear_until_wiped', anyof([dirty('shared_shelf'), shelf_clear]), 'shelf_clean')

    rooms = copy.deepcopy(s['reference_plan']['rooms'])
    room_ready = []
    book_destinations = []
    for room_index, room in enumerate(rooms):
        r = room['id']; stage = r + '_stage'; floor = room['floor']
        restored = []; staged = []
        for i, w in enumerate(room['work']):
            item = w['item']; staged.append(on(item, stage))
            if i % 2:
                w.update(destination='shared_shelf', destination_xy=[-5.35 + .3 * (i // 2), -.90 - .30 * room_index])
                book_destinations.append(on(item, 'shared_shelf'))
            restored.append(on(item, w['destination']))
            e['terminal'][item + '_returned'] = restored[-1]
        empty = neg(anyof(staged)); clean_floor = neg(dirty(floor))
        current_restored = allof([clean_floor, *restored, empty])
        e['terminal'][r + '_floor_clean'] = clean_floor
        e['terminal'][r + '_stage_empty'] = empty
        milestone(r + '_staged', allof(staged))
        milestone(r + '_floor_clean', allof([clean_floor, allof(staged)]), [r + '_staged'])
        milestone(r + '_restored', current_restored, [r + '_floor_clean', 'shelf_clean'])
        milestone(r + '_stage_clean', allof([current_restored, neg(dirty(stage))]), [r + '_restored'])
        guard(r + '_stage_before_vacuum', dirty(floor), r + '_staged')
        guard(r + '_keep_staged_until_vacuum', anyof([dirty(floor), allof(staged)]), r + '_floor_clean')
        guard(r + '_vacuum_before_restore', neg(anyof(restored)), r + '_floor_clean')
        guard(r + '_floor_currently_clean_for_restored_items', anyof([neg(anyof(restored)), clean_floor]))
        guard(r + '_restore_before_stage_wipe', dirty(stage), r + '_restored')
        room_ready.append(allof([current_restored, neg(dirty(stage))]))
    guard('shelf_clean_before_books', neg(anyof(book_destinations)), 'shelf_clean')
    guard('shelf_currently_clean_under_books', anyof([neg(anyof(book_destinations)), neg(dirty('shared_shelf'))]))
    ready = allof([*room_ready, shelf_clear, neg(dirty('shared_shelf'))])
    milestone('rooms_ready', ready, ['a_stage_clean', 'b_stage_clean'])
    lights = s['reference_plan']['lights']
    for r in ['a', 'b']:
        off = []
        for lamp in [x for x in lights if x.startswith(r)]:
            expr = neg(lamp + '_ToggledOn'); off.append(expr)
            e['terminal'][lamp + '_off'] = expr
            guard(lamp + '_last', lamp + '_ToggledOn', 'rooms_ready')
        milestone(r + '_lights_off', allof(off), ['rooms_ready'])
    e['terminal'].update(tool_off=neg('tool_ToggledOn'), tool_home=on('tool', 'tool_home'),
                          wipe_tool_home=on('wipe_tool', 'wipe_home'), hand_empty='hand_empty')
    milestone('tools_home', allof([e['terminal'][k] for k in ['tool_off', 'tool_home', 'wipe_tool_home', 'hand_empty']]), ['rooms_ready'])
    milestone('complete', allof(e['terminal'].values()), ['a_lights_off', 'b_lights_off', 'tools_home'])
    schedules = {
        'library_then_a_then_b': ['library', 'a_stage', 'a_vacuum', 'a_restore', 'a_wipe', 'b_stage', 'b_vacuum', 'b_restore', 'b_wipe', 'close'],
        'b_then_library_then_a': ['b_stage', 'b_vacuum', 'library', 'b_restore', 'b_wipe', 'a_stage', 'a_vacuum', 'a_restore', 'a_wipe', 'close'],
        'interleave_both_rooms': ['a_stage', 'b_stage', 'a_vacuum', 'library', 'b_vacuum', 'a_restore', 'b_restore', 'b_wipe', 'a_wipe', 'close']}
    s['reference_plan'].update(kind='branching_library', rooms=rooms, clutter=clutter,
        wipe_tool='wipe_tool', wipe_home='wipe_home', wipe_xy=[-5.7, -2.4], schedules=schedules)
    s['acceptance_cases'].update(reference_orders=list(schedules),
        physical_negative_cases=['early_shelf_wipe', 'early_stage_wipe', 'early_lights_off'])
    s['design_ledger']['conditional_useful_calls'] = 117
    s['design_deviations'] = [
        'Native kmuacd bookcase is scaled to about 1.93 x 0.62 x 0.86 m; books occupy its accessible top, not interior compartments.',
        'Six native pencil cases instantiate the designated shelf clutter; their destination is the blue storage stand.',
        'Yellow stages, green toy destinations and tool/storage stands remain explicit fixed primitive task fixtures.',
        'Initial stage dust is authored independently of object movement; no motion-induced dust is assumed.',
        'Both bedroom doors are initialized with native Open(fully=True); Open state and measured joint angles are recorded, while route feasibility still requires checked navigation.',
        'Robot starts on unoccupied common-area floor; the inherited vacuuming spawn overlapped the A-room item grid in r2.',
        'Remove only the unbound B-bedroom ottoman that overlapped the prior floor-item grid.']
    # Exhaustive, verbatim public coverage; preserve meaningful groups in audits.
    s['requirements'] = []
    def req(key, text, terminal=(), milestones=(), constraints=()):
        s['requirements'].append(dict(id=key, text=text, terminal=list(terminal),
                                      milestones=list(milestones), constraints=list(constraints)))
    for r, label in [('a', 'A'), ('b', 'B')]:
        req(r + '_work', f'{label} 卧室的 6 本书和 6 件玩具必须同时放到本房黄色暂存台，保持全部暂存直到地面吸尘完成。吸尘后玩具归本房绿台，书归公共区原生矮书柜顶面；有归位物品时本房地面须保持清洁。全部归位且暂存台清空后才可清除该台初始灰尘，结束时台面清洁且没有任务物品。',
            [k for k in e['terminal'] if k.startswith(r + '_') and 'lamp' not in k],
            [r + suffix for suffix in ['_staged', '_floor_clean', '_restored', '_stage_clean']],
            [g['id'] for g in e['state_guards'] if g['id'].startswith(r + '_') and 'lamp' not in g['id']])
    req('library', '公共区矮书柜顶面起初有 6 个笔袋和灰尘；笔袋全部转移到蓝色收纳台且书柜无笔袋后，才能用护理刷清洁书柜。书柜清洁后才可归书；任何书在书柜上时，书柜都必须保持清洁。结束时 6 个笔袋在蓝台、12 本书在书柜，书柜保持清洁。',
        [k for k in e['terminal'] if k.startswith('clutter') or k == 'shared_shelf_clean'],
        ['shelf_cleared', 'shelf_clean'],
        ['clear_shelf_before_wipe', 'keep_shelf_clear_until_wiped', 'shelf_clean_before_books', 'shelf_currently_clean_under_books'])
    req('close', '两间地面、所有物品、共用书柜及两张暂存台均完成后，才可关闭 4 盏任务台灯。结束时 4 灯全关、吸尘器关闭并在原灰台、护理刷在公共区灰台、机械手为空；各项结束状态须同时满足。允许中途归还再取用工具。',
        [k for k in e['terminal'] if 'lamp' in k or k in ['tool_off', 'tool_home', 'wipe_tool_home', 'hand_empty']],
        ['rooms_ready', 'a_lights_off', 'b_lights_off', 'tools_home', 'complete'],
        [g['id'] for g in e['state_guards'] if 'lamp' in g['id']])
    req('handling', '物品逐件搬运，不能连同承载其上其他任务物品一起搬运；任务家具固定在场景中。',
        constraints=['individual_handling'])
    s['public_context'] = [r['text'] for r in s['requirements']]
    return s


if __name__ == '__main__':
    path = ROOT / 'task_designs/constructed_branching/bedroom_shared_library_branching_v2.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(compile_recipe(), ensure_ascii=False, indent=2) + '\n')
