"""RGB execution of frozen official BDDL instances outside the challenge-100 catalog.

Goals and transitions come from the installed official task. There are no custom
goal predicates, reference actions, or fabricated human-normalized metrics.
"""
import copy
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .omnigibson_rgb import RGBBackend
from ..catalog_tasks import load_catalog_spec
from ..deadline import DEADLINE_STEP_SENTINEL
from ..goal_grounding import efficient_grounding, evaluate_once_per_literal
from ..omnigibson_backend import normalize_embedded_robot
from ..records import write_json


class CatalogRGBBackend(RGBBackend):
    def __init__(self, spec_path, output, **kwargs):
        self.spec_path = Path(spec_path).resolve()
        self.catalog_spec = load_catalog_spec(self.spec_path)
        self.use_gpu_dynamics = True
        spec = self.catalog_spec
        super().__init__(spec['task'], 0, output, seed=spec['seed'], **kwargs)

    def _catalog_config(self, gpu, max_steps):
        import bddl
        spec = self.catalog_spec
        definition = Path(bddl.__file__).parent / 'activity_definitions' / spec['task'] / 'problem0.bddl'
        if hashlib.sha256(definition.read_bytes()).hexdigest() != spec['bddl_sha256']:
            raise ValueError('Runtime BDDL differs from the sampled task definition')
        frozen = (self.spec_path.parent / spec['frozen_instance']['path']).resolve()
        data = json.loads(frozen.read_text())
        migrations = normalize_embedded_robot(data, reset_controller_state=True)
        config = copy.deepcopy(spec['environment_config'])
        embedded = [o for o in data['objects_info']['init_info'].values() if o['class_name'] == 'Robot']
        if len(embedded) != 1:
            raise ValueError('Sampled scene must contain exactly one robot')
        args = embedded[0]['args']
        args.update(obs_modalities=['proprio'], include_sensor_names=[],
                    grasping_mode='sticky', disable_grasp_handling=True)
        args.pop('exclude_sensor_names', None)
        cache = Path(os.environ['OMNIGIBSON_APPDATA_PATH']) / 'mas-catalog-scenes'
        cache.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(data, sort_keys=True)
        scene_file = cache / (hashlib.sha256(encoded.encode()).hexdigest() + '.json')
        scene_file.write_text(encoded)
        config['robots'] = []
        config['scene'].update(scene_file=str(scene_file), scene_model=spec['scene'],
            include_robots=True, trav_map_resolution=0.05, default_erosion_radius=0.57,
            waypoint_resolution=0.1)
        # The frozen file already contains exactly the sampled scene. Reusing
        # the sampler's room/category filters drops embedded robots and sampled
        # movable objects whose serialized init args have no room annotation.
        # Match InteractiveTraversableScene's own saved-scene reload policy.
        filters = {key: config['scene'].get(key) for key in (
            'load_room_types', 'load_room_instances', 'load_object_categories',
            'not_load_object_categories', 'load_task_relevant_only')}
        config['scene'].update(load_room_types=None, load_room_instances=None,
            load_object_categories=None, not_load_object_categories=None,
            load_task_relevant_only=False)
        config['env'].update(device='cpu', automatic_reset=False)
        config['task'].update(type='BehaviorTask', activity_name=spec['task'],
            activity_definition_id=0, activity_instance_id=0, online_object_sampling=False,
            use_presampled_robot_pose=False, include_obs=False)
        config['task'].setdefault('termination_config', {})['max_steps'] = (
            DEADLINE_STEP_SENTINEL if self.deadline.managed else max_steps)
        self.input_hashes = {'catalog_task_spec': hashlib.sha256(self.spec_path.read_bytes()).hexdigest(),
            'task_instance_state': spec['frozen_instance']['sha256'],
            'sampling_record': spec['sampling_record']['sha256'],
            'bddl_definition': spec['bddl_sha256'],
            'configured_scene': hashlib.sha256(encoded.encode()).hexdigest()}
        write_json(self.output / 'scene_compatibility.json', {'changes': migrations,
            'original_template_unmodified': True, 'scope': 'sampled_full_catalog_instance',
            'sampling_filters_removed_for_frozen_reload': filters,
            'frozen_object_count': len(data['objects_info']['init_info'])})
        return config

    def _load_catalog_environment(self, config):
        from .articulation_sleep import ArticulationSleepPolicy
        from .macro_particle_size import MacroParticleSizePolicy
        self._articulation_sleep_policy = ArticulationSleepPolicy(gpu_dynamics=self.use_gpu_dynamics)
        try:
            self._macro_particle_size_policy = MacroParticleSizePolicy()
            self._load_catalog_environment_with_sleep_policy(config)
            self._articulation_sleep_policy.validate_scene(self.env.scene)
        except BaseException:
            self._articulation_sleep_policy.close()
            if getattr(self, '_macro_particle_size_policy', None) is not None:
                self._macro_particle_size_policy.close()
            raise
        finally:
            write_json(self.output / 'articulation_sleep_policy.json', self._articulation_sleep_policy.describe())
            if getattr(self, '_macro_particle_size_policy', None) is not None:
                write_json(self.output / 'macro_particle_size_policy.json', self._macro_particle_size_policy.describe())

    def _load_catalog_environment_with_sleep_policy(self, config):
        # Challenge-100 teleoperation disables several system cooking/mixing
        # rules. A full-catalog task uses the official simulator defaults; its
        # recipe transformations are necessary parts of the task semantics.
        with self._startup_stage('reload_sampled_catalog_instance'), efficient_grounding(), self._native_startup_progress():
            self.env = self.og.Environment(configs=config)
        with self._startup_stage('reset_sampled_catalog_instance'), self._native_startup_progress('initial_reset'):
            self.env.reset()
        self.robot = self.env.robots[0]
        self.evaluator = SimpleNamespace(env=self.env, robot=self.robot, metrics=[])
        spec = self.catalog_spec
        self.task_metadata = {'task': spec['task'], 'instance': spec['instance_id'],
            'split': 'behavior1k_sampled_development', 'scene': spec['scene'], 'seed': spec['seed'],
            'official_submission_eligible': False}
        write_json(self.output / 'dependency_versions.json',
                   {d.metadata['Name']: d.version for d in metadata.distributions() if d.metadata['Name']})

    def _prepare_task_instance(self):
        conditions = self.env.task.compiled_task.initial_conditions
        with evaluate_once_per_literal([conditions]):
            checked = [bool(c.evaluate(self.env.task._evaluate_predicate)) for c in conditions]
        result = {'status': 'passed' if checked and all(checked) else 'failed',
            'initial_conditions': checked, 'task_definition_unchanged': True,
            'task_instance': self.task_metadata, 'input_hashes': self.input_hashes}
        write_json(self.output / 'catalog_instance_validation.json', result)
        if result['status'] != 'passed':
            raise ValueError('Reloaded catalog instance no longer satisfies its official initial conditions')
        if any(option and all(option) for option in self.initial_goals):
            raise ValueError('Catalog instance is already complete before model execution')

    def evaluate(self):
        goals = self._goal_options()
        return {'task_success': any(bool(option) and all(option) for option in goals),
            'official_task_success': bool(self.env.task.success),
            'goal_options': goals, 'initial_goal_options': self.initial_goals,
            'goal_satisfaction_fraction': max((sum(o) / len(o) for o in goals if o), default=0),
            'official_metrics': {},
            'metric_scope': 'Official compiled BDDL predicates and BehaviorTask.success; no challenge human-normalized metrics for this sampled instance',
            'task_instance': self.task_metadata, 'official_submission_eligible': False,
            'protocol': 'rgb_agent_official_bddl_sampled_instance', 'observation_mode': 'rgb_only',
            'inside_placement': self.inside_placement,
            'volume_sampling_physics_steps': self.sampling_physics_steps}

    def provenance(self):
        result = super().provenance()
        result.update(catalog_task=True, evaluator='official_BehaviorTask_BDDL_conditions',
                      transition_policy='official_simulator_defaults_without_challenge100_teleop_rule_suppression',
                      articulation_sleep_policy=self._articulation_sleep_policy.describe(),
                      macro_particle_size_policy=self._macro_particle_size_policy.describe())
        return result

    def close(self):
        policy = getattr(self, '_articulation_sleep_policy', None)
        particle_policy = getattr(self, '_macro_particle_size_policy', None)
        try:
            if policy is not None:
                write_json(self.output / 'articulation_sleep_policy.json', policy.describe())
            super().close()
        finally:
            if policy is not None:
                policy.close()
            if particle_policy is not None:
                particle_policy.close()
