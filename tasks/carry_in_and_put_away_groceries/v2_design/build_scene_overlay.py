"""Build an isolated dataset overlay for the two-bag grocery research task."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

TASK = 'carrying_in_groceries'
SCENE = 'house_double_floor_lower'
PREFIX = f'{SCENE}_task_{TASK}_0_'
CABINET_BINDINGS = {
    'cabinet.n.01_1': 'top_cabinet_tynnnw_1',
    'cabinet.n.01_2': 'top_cabinet_tynnnw_0',
}
DEFAULT_BAG_HEIGHT_SCALE = 1.2
CARGO_KEYS = ('sack.n.01_1', 'sack.n.01_2', 'beefsteak_tomato.n.01_1',
              'carton__of__milk.n.01_1', 'carton__of__milk.n.01_2',
              'swiss_cheese.n.01_1', 'butter_cookie.n.01_1', 'butter_cookie.n.01_2')


def translate_cargo(states, keys, offset):
    """Move cargo together; caller supplies template names or instance bindings."""
    if len(offset) != 2 or any(not math.isfinite(v) or abs(v) > .15 for v in offset):
        raise ValueError('cargo offset must contain two finite values within +/-0.15 m')
    positions = [states[key]['root_link']['pos'] for key in keys]
    if offset == (0.0, 0.0):
        return
    for position in positions:
        position[0] += offset[0]
        position[1] += offset[1]



def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def build(source: Path, output: Path, *, bag_height_scale: float = DEFAULT_BAG_HEIGHT_SCALE,
          cargo_offset_xy: tuple[float, float] = (0.0, 0.0)) -> None:
    if not 1.0 <= bag_height_scale <= 1.5:
        raise ValueError('bag_height_scale must be between 1.0 and 1.5')
    translate_cargo({}, (), cargo_offset_xy)  # Validate before creating output.
    source = source.resolve()
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f'Overlay directory must be empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = output / item.name
        if item.name != '2026-challenge-task-instances':
            target.symlink_to(item, target_is_directory=item.is_dir())
    src_tasks = source / '2026-challenge-task-instances'
    dst_tasks = output / '2026-challenge-task-instances'
    dst_tasks.mkdir()
    for item in src_tasks.iterdir():
        if item.name != 'scene_test':
            (dst_tasks / item.name).symlink_to(item, target_is_directory=item.is_dir())
    src_test = src_tasks / 'scene_test'
    dst_test = dst_tasks / 'scene_test'
    dst_test.mkdir()
    for item in src_test.iterdir():
        if item.name != 'public':
            (dst_test / item.name).symlink_to(item, target_is_directory=item.is_dir())
    src_public = src_test / 'public'
    dst_public = dst_test / 'public'
    dst_public.mkdir()
    for item in src_public.iterdir():
        if item.name != SCENE:
            (dst_public / item.name).symlink_to(item, target_is_directory=item.is_dir())
    src_scene = src_public / SCENE
    dst_scene = dst_public / SCENE
    dst_scene.mkdir()
    for item in src_scene.iterdir():
        if item.name != 'json':
            (dst_scene / item.name).symlink_to(item, target_is_directory=item.is_dir())
    src_json, dst_json = src_scene / 'json', dst_scene / 'json'
    dst_json.mkdir()
    for item in src_json.iterdir():
        if item.name not in {f'{PREFIX}0_template.json', f'{PREFIX}0_template-partial_rooms.json',
                             f'{SCENE}_task_{TASK}_instances'}:
            (dst_json / item.name).symlink_to(item, target_is_directory=item.is_dir())

    gift = json.loads((src_json / f'{SCENE}_task_assembling_gift_baskets_0_0_template-partial_rooms.json').read_text())
    instance_src = src_json / f'{SCENE}_task_{TASK}_instances' / f'{PREFIX}301_template-tro_state.json'
    instance = json.loads(instance_src.read_text())
    additions = [
        ('sack.n.01_2', 'paper_bag_82', 'paper_bag_81', None, (0.36, 0.0, 0.0)),
        ('carton__of__milk.n.01_2', 'carton_of_milk_83', 'carton_of_milk_79', None, (0.36, 0.0, 0.0)),
        ('swiss_cheese.n.01_1', 'swiss_cheese_84', 'swiss_cheese_77', gift, (0.01, 0.01, 0.04)),
        ('butter_cookie.n.01_1', 'butter_cookie_85', 'butter_cookie_81', gift, (0.36, 0.01, 0.04)),
        ('butter_cookie.n.01_2', 'butter_cookie_86', 'butter_cookie_81', gift, (0.37, -0.01, 0.04)),
    ]
    for suffix in ('0_template.json', '0_template-partial_rooms.json'):
        template_name = f'{PREFIX}{suffix}'
        data = json.loads((src_json / template_name).read_text())
        scope = data['metadata']['task']['inst_to_name']
        scope.update(CABINET_BINDINGS)
        states = data['state']['registry']['object_registry']
        objects = data['objects_info']['init_info']
        for bddl_key, new_name, old_name, other, offset in additions:
            source_data = other if other is not None else data
            source_obj = source_data['objects_info']['init_info'][old_name]
            source_state = source_data['state']['registry']['object_registry'][old_name]
            obj, state = copy.deepcopy(source_obj), copy.deepcopy(source_state)
            obj['args']['name'] = new_name
            obj['args']['in_rooms'] = ['garage_0']
            reference = instance['sack.n.01_1']['root_link']['pos']
            state['root_link']['pos'] = [reference[i] + offset[i] for i in range(3)]
            for name in ('lin_vel', 'ang_vel'):
                if name in state['root_link']:
                    state['root_link'][name] = [0.0, 0.0, 0.0]
            objects[new_name] = obj
            states[new_name] = state
            scope[bddl_key] = new_name
            instance[bddl_key] = copy.deepcopy(state)
        for bag_name in ('paper_bag_81', 'paper_bag_82'):
            objects[bag_name]['args']['scale'][2] *= bag_height_scale
        for bddl_key, name in CABINET_BINDINGS.items():
            instance[bddl_key] = copy.deepcopy(states[name])
        translate_cargo(states, [scope[key] for key in CARGO_KEYS], cargo_offset_xy)
        write_json(dst_json / template_name, data)
    dst_instances = dst_json / f'{SCENE}_task_{TASK}_instances'
    dst_instances.mkdir()
    for item in (src_json / dst_instances.name).iterdir():
        if item.name != instance_src.name:
            (dst_instances / item.name).symlink_to(item)
    translate_cargo(instance, CARGO_KEYS, cargo_offset_xy)
    write_json(dst_instances / instance_src.name, instance)
    write_json(output / 'overlay_manifest.json', {'source_dataset': str(source), 'instance': 301,
        'task': TASK, 'scene': SCENE, 'bag_height_scale': bag_height_scale,
        'cargo_offset_xy_m': list(cargo_offset_xy),
        'variant_validation': 'requires_physical_containment_discoverability_and_action_checks',
        'status': 'research_candidate_unvalidated'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bag-height-scale', type=float, default=DEFAULT_BAG_HEIGHT_SCALE)
    parser.add_argument('--cargo-offset-xy', type=float, nargs=2, default=(0.0, 0.0), metavar=('X', 'Y'))
    args = parser.parse_args()
    build(args.source, args.output, bag_height_scale=args.bag_height_scale,
          cargo_offset_xy=tuple(args.cargo_offset_xy))
