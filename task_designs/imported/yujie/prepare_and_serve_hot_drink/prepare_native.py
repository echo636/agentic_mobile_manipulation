"""Compose the pinned template and instance301 for the current native backend."""
import argparse
import hashlib
import json
from pathlib import Path


def prepare(scene, output):
    manifest = json.loads((scene / 'scene_manifest.json').read_text())
    files = {}
    for row in manifest['files']:
        path = scene / row['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Restored source changed: ' + row['path'])
        files[path.name] = path
    template = files['house_single_floor_task_prepare_and_serve_hot_drink_0_0_template-partial_rooms.json']
    instance = files['house_single_floor_task_prepare_and_serve_hot_drink_0_301_template-tro_state.json']
    data = json.loads(template.read_text())
    states = json.loads(instance.read_text())
    bindings = data['metadata']['task']['inst_to_name']
    for name, state in states.items():
        if name != 'robot_poses':
            data['state']['registry']['object_registry'][bindings[name]] = state
    output.mkdir(parents=True, exist_ok=True)
    scene_path = output / 'native_scene.json'
    if scene_path.exists():
        raise FileExistsError(scene_path)
    encoded = json.dumps(data, sort_keys=True)
    scene_path.write_text(encoded)
    spec = json.loads((Path(__file__).parent / 'native_task.json').read_text())
    spec['frozen_instance'] = dict(path=scene_path.name, sha256=hashlib.sha256(encoded.encode()).hexdigest())
    (output / 'task.json').write_text(json.dumps(spec, indent=2) + '\n')
    (output / 'preparation.json').write_text(json.dumps(dict(
        status='prepared', simulator_validation='not_run', task_success=None,
        source_commit=manifest['source_commit'], source_files=manifest['files'],
        instance_objects_restored=len(states)-1,
        robot_start_source='available_tasks_entry.yaml; instance robot_poses contains only [-50,-50,-50] placeholder',
        particle_state_source='pinned template; instance301 has no system override',
        goal_source='problem0.bddl eleven top-level clauses, evaluated using native object states',
        frozen_instance=spec['frozen_instance']), indent=2) + '\n')
    return output / 'task.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True, help='Existing restore_scene.py output')
    parser.add_argument('--output', type=Path, required=True, help='Native preparation directory outside Git')
    args = parser.parse_args()
    print(prepare(args.scene.resolve(), args.output.resolve()))
