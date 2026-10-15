"""Custom scene construction/restoration and private state-based evaluation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from .omnigibson_rgb import RGBBackend
from ..long_horizon import TemporalGoalEvaluator
from ..records import write_json


def validate_system_references(spec, system_root):
    names={row['system'] for row in [*spec['evaluation']['predicates'].values(),
                                   *spec.get('initial_layout',[])] if row.get('system')}
    missing=sorted(name for name in names if not (Path(system_root)/name).is_dir())
    if missing:raise ValueError('Unknown custom particle systems: '+', '.join(missing))


class CustomTaskBackend(RGBBackend):
    def __init__(self, spec_path, output, *, build_instance=None, **kwargs):
        self.spec_path = Path(spec_path).resolve()
        self.custom_spec = json.loads(self.spec_path.read_text())
        if self.custom_spec['schema_version'] != 1: raise ValueError('Unknown custom task schema')
        validate_system_references(self.custom_spec,
            Path(os.environ['OMNIGIBSON_DATA_PATH'])/'behavior-1k-assets/systems')
        self.custom_tracker = None; self.custom_sequence = 0
        self.build_instance = Path(build_instance) if build_instance else None
        self._custom_motion_positions = {}; self._custom_state_errors = []
        base = self.custom_spec['base']
        super().__init__(base['task'], base['instance'], output, seed=base['seed'], **kwargs)
        # Camera initialization may advance physics. Begin scoring from the
        # actual policy-ready scene, and reject a fixture that already drifted
        # out of its declared initial conditions before the agent can act.
        from ..long_horizon import expression
        before_refresh=self._predicate_values()
        # Restoring a scene and attaching cameras can invalidate PhysX contact
        # caches. Render-only warmup does not rebuild OnTop / Touching data.
        # KinematicsMixin also retains a cached false value when a restored
        # object's pose barely changes, even after contact data is refreshed.
        self.env.scene.wake_scene_objects()
        for _ in range(5): self.og.sim.step()
        for obj in self.env.scene.objects:
            for state in obj.states.values(): state.clear_cache()
        initial=self._predicate_values()
        failed=[label for label,condition in self.custom_spec.get('initial_assertions',{}).items()
                if not expression(condition,initial)]
        write_json(self.output/'custom_initial_contact_refresh.json',{
            'status':'failed' if failed else 'passed','physics_steps':5,
            'before':before_refresh,'after':initial,'failed_assertions':failed,
            'role_poses':{role:[v.tolist() for v in self.custom_objects[name].get_position_orientation()]
                          for role,name in self.custom_spec['roles'].items()}})
        if failed:
            raise RuntimeError('Policy-ready initial state assertion failed: '+', '.join(failed))
        self.custom_tracker=TemporalGoalEvaluator(self.custom_spec['evaluation'])
        self.custom_sequence=0
        self.custom_tracker.observe(initial,0,env_step=self.steps,initial=True)
        self._custom_motion_positions=self._motion_positions()
        write_json(self.output/'custom_policy_ready_validation.json',{
            'status':'passed','validation_level':'declared initial conditions after camera warmup; feasibility separate',
            'input_hashes':self.input_hashes,'initial_predicates':initial,
            'initial_evaluation':self.custom_tracker.result(initial),
            'env_step':self.steps})

    def _prepare_task_instance(self):
        """Called before camera setup / first policy observation."""
        spec = self.custom_spec
        if self.build_instance:
            self._construct_instance()
            if self.build_instance.exists(): raise FileExistsError(self.build_instance)
            self.env.scene.save(json_path=str(self.build_instance))
            instance_path = self.build_instance
        else:
            frozen = spec['frozen_instance']
            instance_path = (self.spec_path.parent / frozen['path']).resolve()
            if hashlib.sha256(instance_path.read_bytes()).hexdigest() != frozen['sha256']:
                raise ValueError('Custom initial scene hash mismatch')
            self.env.scene.restore(str(instance_path))
        self.custom_objects = {o.name: o for o in self.env.scene.objects}
        for role, name in spec['roles'].items():
            if name not in self.custom_objects: raise ValueError('Missing custom role: ' + role)
        self.custom_tracker = TemporalGoalEvaluator(spec['evaluation'])
        initial = self._predicate_values()
        self.custom_tracker.observe(initial, 0, env_step=0, initial=True)
        self._custom_motion_positions = self._motion_positions()
        self.input_hashes.update(custom_task_spec=hashlib.sha256(self.spec_path.read_bytes()).hexdigest(),
                                 custom_initial_scene=hashlib.sha256(instance_path.read_bytes()).hexdigest())
        self.task_metadata = {'task': spec['id'], 'instance': spec['instance_id'], 'seed': spec['base']['seed'],
                              'split': 'custom_long_horizon_pilot', 'scene': self.env.task.scene_name,
                              'base_public_task': spec['base'], 'official_submission_eligible': False}
        write_json(self.output / 'custom_instance_validation.json', {
            'status':'passed', 'validation_level':'scene loaded and all private predicates evaluated; feasibility separate',
            'task':self.task_metadata, 'input_hashes':self.input_hashes,
            'initial_predicates':initial, 'initial_evaluation':self.custom_tracker.result(initial),
            'role_poses':{role:[v.tolist() for v in self.custom_objects[name].get_position_orientation()]
                          for role,name in spec['roles'].items()}})
        # Construction is outside policy time and outside the agent/video trace.
        self.steps = 0; self.frames_revision = -1

    def _construct_instance(self):
        """Import pinned asset definitions, then sample one joint initial layout.

        No other task's scene state or goal is spliced into the evaluator.
        Source entries are only object assets with documented initial poses.
        """
        import yaml
        from omnigibson.utils.config_utils import TorchEncoder
        from omnigibson import object_states
        spec = self.custom_spec
        root = Path(os.environ['OMNIGIBSON_DATA_PATH']) / '2026-challenge-task-instances'
        catalog = yaml.safe_load((root/'metadata/available_tasks.yaml').read_text())
        scene = json.loads(json.dumps(self.env.scene.save(as_dict=True), cls=TorchEncoder))
        provenance = []
        for item in spec.get('asset_imports', []):
            task = item['source_task']; source_scene = catalog[task][0]['scene_model']
            template = root/'scene_test/public'/source_scene/'json'/f'{source_scene}_task_{task}_0_0_template.json'
            data = json.loads(template.read_text()); old = item['source_object']; name = item.get('name', old)
            if name in scene['objects_info']['init_info']: raise ValueError('Duplicate imported object: '+name)
            entry = copy.deepcopy(data['objects_info']['init_info'][old])
            entry['args']['name'] = name; entry['args'].pop('bddl_object_scope', None)
            if 'in_rooms' in item: entry['args']['in_rooms'] = item['in_rooms']
            state = copy.deepcopy(data['state']['registry']['object_registry'][old])
            if 'position' in item: state['root_link']['pos'] = item['position']
            if 'orientation' in item: state['root_link']['ori'] = item['orientation']
            scene['objects_info']['init_info'][name] = entry
            scene['state']['registry']['object_registry'][name] = state
            provenance.append({'name':name,'source_template':str(template),
                'source_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),
                'asset':entry['args']})
        self.env.scene.restore(scene)
        objects = {o.name:o for o in self.env.scene.objects}
        roles = {k:objects[v] for k,v in spec['roles'].items()}
        for operation in spec.get('initial_layout', []):
            obj = roles[operation['object']]
            if 'position' in operation:
                pos = self.torch.tensor(operation['position'], dtype=self.torch.float32)
                ori = self.torch.tensor(operation.get('orientation',[0,0,0,1]), dtype=self.torch.float32)
                obj.set_position_orientation(pos,ori);obj.keep_still()
            else:
                cls = getattr(object_states, operation['state'])
                args = [roles[operation['target']]] if operation.get('target') else []
                if operation.get('system'): args.append(self.env.scene.get_system(operation['system'],force_init=True))
                if not obj.states[cls].set_value(*args, operation.get('value',True), **operation.get('kwargs',{})):
                    raise RuntimeError('Custom layout setter failed: '+str(operation))
            for _ in range(operation.get('settle_steps',5)): self.og.sim.step()
        for _ in range(spec.get('settle_steps',30)): self.og.sim.step()
        self.custom_objects = objects
        initial_values = self._predicate_values()
        from ..long_horizon import expression
        for label,condition in spec.get('initial_assertions',{}).items():
            if not expression(condition,initial_values): raise RuntimeError('Initial state assertion failed: '+label)
        write_json(self.output/'custom_scene_build.json', {'status':'passed','asset_imports':provenance,
            'initial_predicates':initial_values,'scope':'joint initial scene only; independent trajectory feasibility pending'})

    def _predicate_values(self):
        from omnigibson import object_states
        live = {obj.name:obj for obj in self.env.scene.objects}
        roles = {key:live.get(name) for key,name in self.custom_spec['roles'].items()}
        result = {}
        for key, row in self.custom_spec['evaluation']['predicates'].items():
            obj = roles[row['object']]
            if obj is None or (row.get('target') and roles[row['target']] is None):
                result[key] = False; continue
            cls = getattr(object_states,row['state'])
            if cls not in obj.states: raise ValueError('Unsupported custom predicate '+key)
            args = [roles[row['target']]] if row.get('target') else []
            if row.get('system'):
                system = self.env.scene.active_systems.get(row['system'])
                if system is None:
                    result[key] = False; continue
                args.append(system)
            result[key] = bool(obj.states[cls].get_value(*args))
        return result

    def _motion_positions(self):
        live = {obj.name:obj for obj in self.env.scene.objects}
        return {rule['object']:live[self.custom_spec['roles'][rule['object']]].get_position_orientation()[0].clone()
                for rule in self.custom_spec['evaluation'].get('motion_guards',[])
                if self.custom_spec['roles'][rule['object']] in live}

    def _check_custom_motion(self):
        current = self._motion_positions()
        moved = {role for role,pos in current.items() if role in self._custom_motion_positions
                 and float(self.torch.linalg.vector_norm(pos-self._custom_motion_positions[role])) > .015}
        if moved: self.custom_tracker.check_motion(self._predicate_values(),moved,env_step=self.steps)
        # Accumulate small control-step displacements. Updating every tick
        # would miss slow transport forever (e.g. 8 mm/tick < 15 mm tolerance).
        self._custom_motion_positions = {
            role:pos if role in moved or role not in self._custom_motion_positions
            else self._custom_motion_positions[role] for role,pos in current.items()}

    def _step(self, action):
        super()._step(action)
        if self.custom_tracker is not None: self._check_custom_motion()

    def execute_visual(self, primitive, target, max_steps, **kwargs):
        if self.custom_tracker is None: return super().execute_visual(primitive,target,max_steps,**kwargs)
        # Transactional executor errors restore the scene. Do not award history
        # based on internal sampler candidates: milestones only use action ends.
        before_violations = copy.deepcopy(self.custom_tracker.violations)
        before_positions = self._motion_positions()
        before_values = self._predicate_values()
        try:
            result = super().execute_visual(primitive,target,max_steps,**kwargs)
        except Exception:
            # Motion violations during a rolled-back atomic action are not a
            # committed trajectory. Current terminal state is still rechecked.
            current = self._motion_positions()
            rolled_back = current.keys()==before_positions.keys() and all(
                float(self.torch.linalg.vector_norm(current[k]-before_positions[k]))<1e-4 for k in current)
            if rolled_back and self._predicate_values()==before_values:
                self.custom_tracker.violations = before_violations
                self._custom_motion_positions = current
            raise
        finally:
            self._check_custom_motion()
            self.custom_sequence += 1
            self.custom_tracker.observe(self._predicate_values(),self.custom_sequence,env_step=self.steps)
            write_json(self.output/'custom_goal_trace.json',self.custom_tracker.result(self._predicate_values()))
        return result

    def evaluate(self):
        result = self.custom_tracker.result(self._predicate_values())
        live = {obj.name for obj in self.env.scene.objects}
        missing = [role for role,name in self.custom_spec['roles'].items() if name not in live]
        result['missing_required_entities'] = missing
        result['task_success'] = result['task_success'] and not missing
        result.update(task_instance=self.task_metadata,input_hashes=self.input_hashes,
                      observation_mode='rgb_only',ideal_navigation_distance_m=self.navigation_distance)
        return result

    def provenance(self):
        result = super().provenance()
        result.update(task_instance=self.task_metadata,custom_task=True,
                      evaluator='custom_joint_terminal_and_public_history_v1')
        return result
