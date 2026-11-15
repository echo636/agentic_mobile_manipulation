#!/usr/bin/env python3
"""Sample a custom native BDDL in a private worker; never a model success claim."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))
from manipulation_agent.records import write_json, source_version
from manipulation_agent.tasks.native_definition import register_definition, sampling_scene_config


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--definition', type=Path, required=True)
    parser.add_argument('--instruction', type=Path, required=True)
    parser.add_argument('--base-config', type=Path, required=True)
    parser.add_argument('--scene', default='house_single_floor')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--gpu-dynamics', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    public = args.instruction.read_text().strip()
    if not public or not public.isascii():
        parser.error('Use the English goal-only instruction')
    (args.output / 'problem0.bddl').write_bytes(args.definition.read_bytes())
    (args.output / 'instruction.txt').write_text(public + '\n')
    spec = {'schema_version': 1, 'kind': 'custom_bddl_sampled_instance',
            'task': args.task, 'scene': args.scene, 'seed': args.seed,
            'instance_id': f'{args.task}_development_seed{args.seed}',
            'public_instruction': public, 'bddl_sha256': digest(args.definition),
            'definition': {'path': 'problem0.bddl', 'sha256': digest(args.definition)},
            'use_gpu_dynamics': args.gpu_dynamics, 'official_submission_eligible': False}
    record = {'status': 'running', 'model_used': False, 'task': args.task,
              'scene': args.scene, 'seed': args.seed, 'bddl_sha256': digest(args.definition),
              'use_gpu_dynamics': args.gpu_dynamics,
              'source': source_version(SOURCE), 'pid': os.getpid(),
              'gpu_uuid': os.environ.get('MAS_GPU_UUID'), 'started_at_unix': time.time(),
              'validation_level': 'native_initial_condition_sampling_only',
              'physical_reference_passed': False, 'benchmark_ready': False}
    write_json(args.output / 'sampling_record.json', record)
    backend = None
    try:
        from manipulation_agent.executors.catalog_task import CatalogRGBBackend
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.tasks.goal_grounding import evaluate_once_per_literal

        class SamplingBackend(CatalogRGBBackend):
            def __init__(self):
                self.catalog_spec = spec
                self.spec_path = args.output / 'task.json'
                self.use_gpu_dynamics = args.gpu_dynamics
                RGBBackend.__init__(self, args.task, 0, args.output, seed=args.seed,
                                    max_steps=100000, inside_placement='official_volume',
                                    record_video=False)

            def _catalog_config(self, gpu, max_steps):
                import yaml
                from omnigibson.utils.bddl_utils import get_knowledge_base
                register_definition(get_knowledge_base(), args.task, args.definition.read_text())
                config = copy.deepcopy(json.loads(args.base_config.read_text()))
                robot = yaml.safe_load((Path(self.og.__file__).parent / 'eval/r1pro.yaml').read_text())
                robot.pop('eval', None)
                robot.update(obs_modalities=['proprio'], include_sensor_names=[],
                             grasping_mode='sticky', disable_grasp_handling=True)
                for name in ('base', 'trunk', 'arm_left', 'arm_right', 'gripper_left', 'gripper_right'):
                    robot['controller_config'][name] = {
                        'name': 'HolonomicBaseJointController' if name == 'base' else 'JointController',
                        'motor_type': 'position', 'command_input_limits': None,
                        'command_output_limits': None, 'use_impedances': False}
                    if name != 'base': robot['controller_config'][name]['use_delta_commands'] = False
                config['robots'] = [robot]
                config['scene'] = sampling_scene_config(config['scene'], args.scene)
                config['env'].update(device='cpu', automatic_reset=False)
                config['task'].pop('debug_object_sampling', None)
                config['task'].update(type='BehaviorTask', activity_name=args.task,
                    activity_definition_id=0, activity_instance_id=0, online_object_sampling=True,
                    use_presampled_robot_pose=False, randomize_presampled_pose=False,
                    highlight_task_relevant_objects=False, include_obs=False)
                config['task']['termination_config'] = {'max_steps': max_steps}
                self.input_hashes = {'bddl_definition': spec['bddl_sha256'],
                                    'base_environment_config': digest(args.base_config)}
                spec['environment_config'] = copy.deepcopy(config)
                return config

            def _prepare_task_instance(self):
                # The parent will attach the same four-camera rig as model episodes.
                # Acceptance is checked again below after camera warmup.
                pass

        backend = SamplingBackend()
        conditions = backend.env.task.compiled_task.initial_conditions
        with evaluate_once_per_literal([conditions]):
            checked = [bool(c.evaluate(backend.env.task._evaluate_predicate)) for c in conditions]
        goal, _ = backend.env.task.compiled_task.check_goal(backend.env.task._evaluate_predicate)
        record.update(initial_conditions=checked, initial_goal_satisfied=bool(goal),
                      backend=backend.provenance())
        if not checked or not all(checked) or goal:
            raise RuntimeError('Sampled policy-ready scene violates initial conditions or is already solved')
        scene = args.output / 'initial_scene.json'
        backend.env.task.update_bddl_scope_metadata(backend.env)
        backend.env.scene.save(json_path=str(scene))
        observation = backend.observe()
        write_json(args.output / 'initial_observation.json', observation)
        write_json(args.output / 'bindings_and_geometry.json', {
            name: None if entity is None else {
                'name': entity.name, 'category': getattr(entity, 'category', None),
                'in_rooms': getattr(entity, 'in_rooms', None),
                'aabb': [v.tolist() for v in entity.aabb] if hasattr(entity, 'aabb') else None}
            for name, entity in backend.env.task.object_scope.items()})
        record.update(status='passed', finished_at_unix=time.time(),
                      saved_files={'initial_scene.json': digest(scene)})
        write_json(args.output / 'sampling_record.json', record)
        spec['frozen_instance'] = {'path': scene.name, 'sha256': digest(scene)}
        spec['sampling_record'] = {'path': 'sampling_record.json',
                                   'sha256': digest(args.output / 'sampling_record.json')}
        write_json(args.output / 'task.json', spec)
        print(json.dumps({'status': 'passed', 'task': args.task,
                          'scope': 'initial sampling and four-camera readiness only',
                          'model_used': False}), flush=True)
    except BaseException as exc:
        record.update(status='failed', finished_at_unix=time.time(), error=str(exc),
                      error_type=type(exc).__name__)
        write_json(args.output / 'sampling_record.json', record)
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        raise
    finally:
        # Keep sampling_record immutable after its hash was put into task.json.
        if backend is not None:
            backend.close()
        else:
            from manipulation_agent.batch.startup_cleanup import shutdown_partial_simulator
            shutdown_partial_simulator()


if __name__ == '__main__':
    main()
