"""RGB execution of frozen native BDDL instances with pinned sampling evidence.

Installed tasks and explicit custom definitions use the same native predicates.
Custom definitions are registered only in the worker, with separate provenance
and no official-challenge or human-normalized score.
"""
import copy
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .omnigibson_rgb import RGBBackend
from ..tasks.catalog_tasks import complete_scene_context, load_catalog_spec
from ..tasks.lineage_goals import check_initial_lineages, evaluate_relational_goals
from ..tasks.native_definition import resolve_definition, register_definition
from ..runtime.deadline import DEADLINE_STEP_SENTINEL
from ..tasks.goal_grounding import FactoredGoalOptions, efficient_grounding, evaluate_once_per_literal
from .omnigibson_backend import normalize_embedded_robot
from ..records import write_json
from ..runtime.robot_clock import robot_clock_record


class CatalogRGBBackend(RGBBackend):
    def _checked_cut(self, target, max_steps):
        constraint = self.catalog_spec.get('cutting_support')
        if constraint is not None:
            from omnigibson.object_states import OnTop
            from ..contracts import SkillError
            scope = self.env.task.object_scope
            support = scope[constraint['support_binding']]
            allowed = [scope[key] for key in constraint['target_bindings']]
            valid_target = any(target is obj for obj in allowed)
            supported = bool(valid_target and target.states[OnTop].get_value(support))
            row = {'env_step': self.steps, 'target': target.name, 'support': support.name,
                   'declared_target': valid_target, 'on_required_support': supported}
            with (self.output / 'cutting_support_checks.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
            if not supported:
                raise SkillError('pre_condition_error',
                    'Cut the task material while it rests on the required work surface.')
        return super()._checked_cut(target, max_steps)

    def __init__(self, spec_path, output, **kwargs):
        self.spec_path = Path(spec_path).resolve()
        self.catalog_spec = load_catalog_spec(self.spec_path)
        self.use_gpu_dynamics = self.catalog_spec.get('use_gpu_dynamics', True)
        spec = self.catalog_spec
        super().__init__(spec['task'], 0, output, seed=spec['seed'], **kwargs)

    def _catalog_config(self, gpu, max_steps):
        import bddl
        from omnigibson.utils.asset_utils import get_scene_path
        spec = self.catalog_spec
        definition = resolve_definition(spec, self.spec_path)
        if definition is not None:
            from omnigibson.utils.bddl_utils import get_knowledge_base
            register_definition(get_knowledge_base(), spec['task'], definition.read_text())
        else:
            definition = Path(bddl.__file__).parent / 'activity_definitions' / spec['task'] / 'problem0.bddl'
        if hashlib.sha256(definition.read_bytes()).hexdigest() != spec['bddl_sha256']:
            raise ValueError('Runtime BDDL differs from the sampled task definition')
        frozen = (self.spec_path.parent / spec['frozen_instance']['path']).resolve()
        data = json.loads(frozen.read_text())
        # Room-filtered sampling omits entire floor objects as well as furniture.
        # Removing filters on reload cannot recover objects absent from the file.
        # Keep the sampled task state and fill the missing surroundings from the
        # official full scene that also supplies the native traversability map.
        template_path = Path(get_scene_path(spec['scene'])) / 'json' / (spec['scene'] + '_best.json')
        template_bytes = template_path.read_bytes()
        restored = complete_scene_context(data, json.loads(template_bytes))
        write_json(self.output / 'scene_context.json', {
            'policy': 'official_full_scene_with_sampled_task_state',
            'template_path': str(template_path),
            'template_sha256': hashlib.sha256(template_bytes).hexdigest(),
            'restored_objects': restored,
            'sampled_objects_and_task_state_preserved': True})
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
        # Reusing sampler filters would drop the restored surroundings, embedded
        # robot and sampled movable objects without room annotations.
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
            'frozen_object_count': len(data['objects_info']['init_info']) - len(restored),
            'configured_object_count': len(data['objects_info']['init_info']),
            'restored_context_count': len(restored)})
        return config

    def _load_catalog_environment(self, config):
        from .compat.articulation_sleep import ArticulationSleepPolicy
        from .compat.macro_particle_size import MacroParticleSizePolicy
        from .compat.recipe_particle_output import RecipeParticleOutputPolicy
        self._articulation_sleep_policy = ArticulationSleepPolicy(gpu_dynamics=self.use_gpu_dynamics)
        try:
            self._recipe_particle_output_policy = RecipeParticleOutputPolicy()
            self._macro_particle_size_policy = MacroParticleSizePolicy()
            self._load_catalog_environment_with_sleep_policy(config)
            self._articulation_sleep_policy.validate_scene(self.env.scene)
        except BaseException:
            self._articulation_sleep_policy.close()
            if getattr(self, '_macro_particle_size_policy', None) is not None:
                self._macro_particle_size_policy.close()
            if getattr(self, '_recipe_particle_output_policy', None) is not None:
                self._recipe_particle_output_policy.close()
            raise
        finally:
            write_json(self.output / 'articulation_sleep_policy.json', self._articulation_sleep_policy.describe())
            if getattr(self, '_macro_particle_size_policy', None) is not None:
                write_json(self.output / 'macro_particle_size_policy.json', self._macro_particle_size_policy.describe())
            if getattr(self, '_recipe_particle_output_policy', None) is not None:
                write_json(self.output / 'recipe_particle_output_policy.json', self._recipe_particle_output_policy.describe())

    def _load_catalog_environment_with_sleep_policy(self, config):
        from .compat.broadphase import native_mbp_broadphase
        # Challenge-100 teleoperation disables several system cooking/mixing
        # rules. A full-catalog task uses the official simulator defaults; its
        # recipe transformations are necessary parts of the task semantics.
        with self._startup_stage('reload_sampled_catalog_instance'), efficient_grounding(factorized=True), self._native_startup_progress(), native_mbp_broadphase(self.og) as broadphase:
            self.env = self.og.Environment(configs=config)
        self._broadphase = broadphase
        write_json(self.output / 'collision_broadphase.json', broadphase)
        with self._startup_stage('reset_sampled_catalog_instance'), self._native_startup_progress('initial_reset'):
            self.env.reset()
        from .compat.particle_melting import install_particle_melting
        self._particle_melting = install_particle_melting(self.env.scene)
        write_json(self.output / 'particle_melting_policy.json', self._particle_melting)
        self.robot = self.env.robots[0]
        self.evaluator = SimpleNamespace(env=self.env, robot=self.robot, metrics=[])
        spec = self.catalog_spec
        self.task_metadata = {'task': spec['task'], 'instance': spec['instance_id'],
            'split': ('custom_native_bddl_development' if spec['kind'] == 'custom_bddl_sampled_instance'
                      else 'behavior1k_sampled_development'), 'scene': spec['scene'], 'seed': spec['seed'],
            'official_submission_eligible': False}
        write_json(self.output / 'dependency_versions.json',
                   {d.metadata['Name']: d.version for d in metadata.distributions() if d.metadata['Name']})

    def _prepare_task_instance(self):
        check_initial_lineages(self.catalog_spec, lambda name: self.env.scene.object_registry('name', name, None))
        conditions = self.env.task.compiled_task.initial_conditions
        with evaluate_once_per_literal([conditions]):
            checked = [bool(c.evaluate(self.env.task._evaluate_predicate)) for c in conditions]
        result = {'status': 'passed' if checked and all(checked) else 'failed',
            'initial_conditions': checked, 'task_definition_unchanged': True,
            'task_instance': self.task_metadata, 'input_hashes': self.input_hashes}
        write_json(self.output / 'catalog_instance_validation.json', result)
        if result['status'] != 'passed':
            raise ValueError('Reloaded catalog instance no longer satisfies its official initial conditions')
        if self.env.task.compiled_task.check_goal(self.env.task._evaluate_predicate)[0]:
            raise ValueError('Catalog instance is already complete before model execution')

    def _goal_options(self):
        options = self.env.task.ground_goal_state_options
        if isinstance(options, FactoredGoalOptions):
            return [options.best_truth_option(self.env.task._evaluate_predicate)]
        return super()._goal_options()

    def evaluate(self):
        goals = self._goal_options()
        success, _ = self.env.task.compiled_task.check_goal(self.env.task._evaluate_predicate)
        result = {'task_success': bool(success),
            'official_task_success': (None if self.catalog_spec['kind'] == 'custom_bddl_sampled_instance'
                                      else bool(self.env.task.success)),
            'goal_options': goals, 'initial_goal_options': self.initial_goals,
            'goal_options_representation': ('best_scoring_ground_alternative' if
                isinstance(self.env.task.ground_goal_state_options, FactoredGoalOptions) else 'all_ground_alternatives'),
            'goal_satisfaction_fraction': max((sum(o) / len(o) for o in goals if o), default=0),
            'official_metrics': {},
            'metric_scope': 'Official compiled BDDL predicates and BehaviorTask.success; no challenge human-normalized metrics for this sampled instance',
            'task_instance': self.task_metadata, 'official_submission_eligible': False,
            'protocol': ('rgb_agent_custom_native_bddl_sampled_instance'
                         if self.catalog_spec['kind'] == 'custom_bddl_sampled_instance'
                         else 'rgb_agent_official_bddl_sampled_instance'), 'observation_mode': 'rgb_only',
            'inside_placement': self.inside_placement,
            'volume_sampling_physics_steps': self.sampling_physics_steps,
            'ideal_navigation_distance_m': self.navigation_distance, **robot_clock_record(self)}
        if self.catalog_spec.get('additional_terminal_conditions'):
            additional = {'hand_empty': self._get_held() is None}
            result.update(bddl_task_success=result['task_success'],
                additional_terminal_conditions=additional,
                task_success=result['task_success'] and all(additional.values()),
                protocol='rgb_agent_custom_native_bddl_additional_terminal_conditions_v1',
                metric_scope='Official compiled BDDL predicates plus declared public terminal conditions; no official challenge score')
        if self.catalog_spec.get('lineage_containment_goals') or self.catalog_spec.get('return_region_goals'):
            from omnigibson.object_states import Inside
            checks = evaluate_relational_goals(self.catalog_spec,
                lambda name: self.env.scene.object_registry('name', name, None),
                lambda obj, destination: bool(obj.states[Inside].get_value(destination)))
            result.update(before_relational_goal_success=result['task_success'],
                relational_terminal_conditions=checks,
                task_success=result['task_success'] and all(checks.values()),
                protocol='rgb_agent_custom_native_bddl_lineage_returns_v1',
                metric_scope='Native BDDL plus parent-derived output containment, declared return regions and public terminal conditions; no official challenge score')
        return result

    def provenance(self):
        result = super().provenance()
        result.update(catalog_task=True, custom_bddl=self.catalog_spec['kind'] == 'custom_bddl_sampled_instance',
                      evaluator='official_BehaviorTask_BDDL_conditions',
                      transition_policy='official_simulator_defaults_without_challenge100_teleop_rule_suppression',
                      articulation_sleep_policy=self._articulation_sleep_policy.describe(),
                      macro_particle_size_policy=self._macro_particle_size_policy.describe())
        result['recipe_particle_output_policy'] = self._recipe_particle_output_policy.describe()
        result['particle_melting_policy'] = self._particle_melting
        result['collision_broadphase'] = self._broadphase
        if self.catalog_spec.get('additional_terminal_conditions'):
            result.update(evaluator='official_BehaviorTask_BDDL_and_declared_terminal_conditions',
                          additional_terminal_conditions=self.catalog_spec['additional_terminal_conditions'])
        if self.catalog_spec.get('lineage_containment_goals') or self.catalog_spec.get('return_region_goals'):
            result.update(evaluator='native_BDDL_and_private_parent_lineage_terminal_relations',
                lineage_containment_goals=self.catalog_spec.get('lineage_containment_goals', []),
                return_region_goals=self.catalog_spec.get('return_region_goals', []))
        return result

    def close(self):
        policy = getattr(self, '_articulation_sleep_policy', None)
        particle_policy = getattr(self, '_macro_particle_size_policy', None)
        recipe_policy = getattr(self, '_recipe_particle_output_policy', None)
        try:
            if policy is not None:
                write_json(self.output / 'articulation_sleep_policy.json', policy.describe())
            if particle_policy is not None:
                write_json(self.output / 'macro_particle_size_policy.json', particle_policy.describe())
            if recipe_policy is not None:
                write_json(self.output / 'recipe_particle_output_policy.json', recipe_policy.describe())
            super().close()
        finally:
            if policy is not None:
                policy.close()
            if particle_policy is not None:
                particle_policy.close()
            if recipe_policy is not None:
                recipe_policy.close()
