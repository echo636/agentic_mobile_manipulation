"""Custom scene construction/restoration and private state-based evaluation."""
from __future__ import annotations

import copy
from contextlib import contextmanager, nullcontext
import hashlib
import json
import math
import os
from pathlib import Path

from .omnigibson_rgb import RGBBackend
from ..tasks.long_horizon import TemporalGoalEvaluator
from ..tasks.custom_tasks import validate_spec
from ..records import write_json


def imported_state_with_velocities(source_state):
    """Complete legacy fixed-asset poses for the runtime rigid-body loader.

    Native public templates omit velocity for some fixed furniture. Imported
    DatasetObjects can still use RigidDynamicPrim, which requires both keys.
    Preserve supplied velocities and never mutate the pinned template data.
    """
    state = copy.deepcopy(source_state)
    for key in ('lin_vel', 'ang_vel'):
        state['root_link'].setdefault(key, [0., 0., 0.])
    return state


def complete_native_articulation_state(state, objects, native_names):
    """Use actual newly loaded native joint defaults only during construction."""
    completed=copy.deepcopy(state);changes=[]
    entries=completed['registry']['object_registry']
    for name in native_names:
        obj=objects[name];entry=entries[name]
        if obj.n_joints and 'joints' not in entry and 'joint_pos' not in entry:
            entry['joint_pos']=copy.deepcopy(obj.get_joint_positions())
            entry['joint_vel']=copy.deepcopy(obj.get_joint_velocities())
            changes.append({'name':name,'n_joints':obj.n_joints})
        # Added assets may briefly touch at the loader origin and trigger the
        # native contact-driven attachment callback before authored poses load.
        # An omitted attachment state must mean initially detached, rather
        # than retaining that accidental import-time joint across relocation.
        if any(cls.__name__=='AttachedTo' for cls in getattr(obj,'states',{})):
            states=entry.setdefault('non_kin',{})
            if 'AttachedTo' not in states:
                states['AttachedTo']={'attached_obj_uuid':-1}
                changes.append({'name':name,'initial_attachment':'detached'})
    return completed,changes


def initial_open_joint_targets(open_state, fraction):
    """Author a resting initial opening inside native limits, before policy time."""
    if type(fraction) not in (float,int) or not math.isfinite(fraction) or not .05<fraction<1.:
        raise ValueError('Initial open fraction must lie strictly between .05 and 1')
    both_sides,joints,directions=open_state.relevant_joints_info
    if both_sides or not joints or len(joints)!=len(directions):
        raise ValueError('Initial open fraction needs unambiguous native opening directions')
    targets=[]
    for joint,direction in zip(joints,directions):
        low,high=float(joint.lower_limit),float(joint.upper_limit)
        if direction not in (-1,1) or not all(map(math.isfinite,(low,high))) or low>=high:
            raise ValueError('Initial opening needs finite ordered native joint limits')
        closed,opened=(low,high) if direction==1 else (high,low)
        targets.append((joint,closed+fraction*(opened-closed)))
    return targets


@contextmanager
def construction_native_sampling_limit(macros, trials=30):
    """Bound initial rejection sampling, restoring runtime defaults even on failure."""
    previous=(macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS,macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS)
    with macros.unlocked():
        macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS=1
        macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS=trials
    try:yield
    finally:
        with macros.unlocked():
            macros.DEFAULT_HIGH_LEVEL_SAMPLING_ATTEMPTS,macros.DEFAULT_LOW_LEVEL_SAMPLING_ATTEMPTS=previous


def validate_system_references(spec, system_root):
    names={row['system'] for row in [*spec['evaluation']['predicates'].values(),
                                   *spec.get('initial_layout',[])] if row.get('system')}
    missing=sorted(name for name in names if not (Path(system_root)/name).is_dir())
    if missing:raise ValueError('Unknown custom particle systems: '+', '.join(missing))


class CustomTaskBackend(RGBBackend):
    def __init__(self, spec_path, output, *, build_instance=None, geometry_only=False, **kwargs):
        self.spec_path = Path(spec_path).resolve()
        self.custom_spec = json.loads(self.spec_path.read_text())
        # Complete furnished scenes require more search time than the small
        # atomic fixtures. The recorded loaded garden map has a valid route
        # that the 10-second budget misses under simulator CPU contention.
        # Keep the same collision checks, candidate set and expansion bound.
        self.forward_route_planning_seconds=(60. if
            self.custom_spec.get('reference_plan',{}).get('kind')=='full_workflow' else 10.)
        self.geometry_only = geometry_only
        self.use_gpu_dynamics = self.custom_spec.get('physics', {}).get('use_gpu_dynamics', False) and not geometry_only
        validate_spec(self.custom_spec, spec_path=self.spec_path, require_frozen=build_instance is None)
        validate_system_references(self.custom_spec,
            Path(os.environ['OMNIGIBSON_DATA_PATH'])/'behavior-1k-assets/systems')
        self.custom_tracker = None; self.custom_sequence = 0
        self._initial_entity_names = None
        self._protected_initial_positions = {}
        self.build_instance = Path(build_instance) if build_instance else None
        self._custom_motion_positions = {}; self._custom_state_errors = []
        base = self.custom_spec['base']
        super().__init__(base['task'], base['instance'], output, seed=base['seed'], **kwargs)
        if self.geometry_only:return
        # Camera initialization may advance physics. Begin scoring from the
        # actual policy-ready scene, and reject a fixture that already drifted
        # out of its declared initial conditions before the agent can act.
        from ..tasks.long_horizon import expression
        with self._startup_stage('policy_ready_predicates_before_refresh'):
            before_refresh=self._predicate_values()
        # Restoring a scene and attaching cameras can invalidate PhysX contact
        # caches. Render-only warmup does not rebuild OnTop / Touching data.
        # KinematicsMixin also retains a cached false value when a restored
        # object's pose barely changes, even after contact data is refreshed.
        self._refresh_custom_contacts('policy_ready')
        initial=self._predicate_values()
        failed=[label for label,condition in self.custom_spec.get('initial_assertions',{}).items()
                if not expression(condition,initial)]
        write_json(self.output/'custom_initial_contact_refresh.json',{
            'status':'failed' if failed else 'passed','physics_steps':5,
            'before':before_refresh,'after':initial,'failed_assertions':failed,
            'role_poses':self._role_poses()})
        if failed:
            raise RuntimeError('Policy-ready initial state assertion failed: '+', '.join(failed))
        if self.custom_spec.get('robot_start'):
            with self._startup_stage('policy_ready_robot_start'):
                self._validate_custom_robot_start()
        self.custom_tracker=TemporalGoalEvaluator(self.custom_spec['evaluation'])
        self.custom_sequence=0
        self.custom_tracker.observe(initial,0,env_step=self.steps,initial=True)
        self._custom_motion_positions=self._motion_positions()
        write_json(self.output/'custom_policy_ready_validation.json',{
            'status':'passed','validation_level':'declared initial conditions after camera warmup; feasibility separate',
            'input_hashes':self.input_hashes,'initial_predicates':initial,
            'initial_evaluation':self.custom_tracker.result(initial),
            'env_step':self.steps})

    def _validate_custom_robot_start(self):
        """Reject a tipped or trapped prepared robot before accepting a build."""
        import math
        import numpy as np
        from .navigation.live_gt_map import build_navigation_grid
        from .navigation.heading_collision import HeadingCollisionChecker
        from .navigation.forward_navigation import local_exit_certificate
        from .navigation.gt_navigation import PlanningBudget, SearchBudgetExhausted
        pos,quat=self.robot.get_position_orientation()
        position=[float(v) for v in pos];orientation=[float(v) for v in quat]
        up_z=1.-2.*(orientation[0]**2+orientation[1]**2)
        record={'position':position,'orientation':orientation,
                'upright':math.isfinite(up_z) and up_z>=math.cos(math.radians(10.)),
                'collision_free':False,'departure_available':False,
                'validation_level':'read-only initial robot pose and departure geometry; not task success'}
        if record['upright']:
            trav=self.env.scene.trav_map
            floor=min(range(len(trav.floor_heights)),key=lambda i:abs(position[2]-trav.floor_heights[i]))
            grid,details=build_navigation_grid(self,floor)
            pose=self._local_pose()
            budget=PlanningBudget(self.deadline.check,seconds=10.)
            collision=HeadingCollisionChecker(grid,self._navigation_layers,pose[2],self._navigation_support,
                                               check_cancelled=budget.check)
            path=self.output/'custom_robot_start_grid.npz'
            np.savez_compressed(path,
                free=np.frombuffer(grid.free,dtype='uint8').reshape(grid.height,grid.width),
                origin=grid.origin,resolution=grid.resolution,start=pose,
                raw_layers=np.stack([layer[0] for layer in collision.layers]),
                support=self._navigation_support,initial_layer_yaw=collision.reference_yaw,
                layer_footprints_json=json.dumps([{'footprint':layer[1].tolist()} for layer in collision.layers]))
            record.update(floor=floor,map_sha256=hashlib.sha256(grid.free).hexdigest(),
                          grid_artifact=path.name,grid_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            try:
                record['collision_free']=collision.motion_free(pose,pose)
                if record['collision_free']:
                    record['departure_certificate']=local_exit_certificate(pose,collision,
                        check_cancelled=budget.check,
                        allow_lateral=bool(getattr(self.robot,'is_holonomic_base',False)))
                    record['departure_available']=record['departure_certificate'] is not None
            except SearchBudgetExhausted:
                record['planning_budget_exhausted']=True
        record['status']='passed' if all(record[k] for k in ('upright','collision_free','departure_available')) else 'failed'
        write_json(self.output/'custom_robot_start_validation.json',record)
        if record['status']!='passed':
            raise RuntimeError('Prepared robot must be upright with collision-free departure geometry')

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
            self._restore_custom_scene(str(instance_path))
        if self.geometry_only:return
        self.custom_objects = {o.name: o for o in self.env.scene.objects}
        for role, name in spec['roles'].items():
            if name not in self.custom_objects and role not in spec.get('future_roles', []):
                raise ValueError('Missing custom role: ' + role)
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
            'role_poses':self._role_poses()})
        # Construction is outside policy time and outside the agent/video trace.
        self.steps = 0; self.frames_revision = -1

    def _role_poses(self):
        live = {o.name:o for o in self.env.scene.objects}
        return {role:([v.tolist() for v in live[name].get_position_orientation()] if name in live else None)
                for role,name in self.custom_spec['roles'].items()}

    def _refresh_custom_contacts(self, stage):
        # Only task entities participate in custom contact predicates. Waking
        # hundreds of unrelated background articulations simultaneously can
        # invalidate the furnished GPU scene. Keep them in their native sleep
        # state, while waking every declared entity (including goal supports)
        # and the robot. All bodies still take ordinary physics steps.
        names=set(self.custom_spec['roles'].values()) | {self.robot.name}
        with self._startup_stage(stage+'_contact_wake'):
            awakened=[]
            for obj in self.env.scene.objects:
                if obj.name in names:
                    obj.wake();awakened.append(obj.name)
            write_json(self.output/(stage+'_contact_wake.json'),{
                'scope':'all declared task entities and robot; background sleep state preserved',
                'awakened':awakened,'physics_steps':5})
        with self._startup_stage(stage+'_contact_physics'):
            for _ in range(5):self.og.sim.step()
        for obj in self.env.scene.objects:
            for state in obj.states.values():state.clear_cache()

    def _restore_custom_scene(self, data):
        if self.custom_spec.get('schema_version') != 3:
            return self.env.scene.restore(data)
        # Native restore briefly initializes newly added bodies at the origin
        # before loading saved poses. An axe touching all new logs there can
        # slice them during play(), before the intended scene even exists.
        # Suspend transitions only during restore, then refresh real rule
        # candidates before any settling, scoring, or policy execution.
        from omnigibson.macros import gm
        enabled = gm.ENABLE_TRANSITION_RULES
        with gm.unlocked():
            gm.ENABLE_TRANSITION_RULES = False
        scene=self.env.scene
        original_load=scene.load_state
        construction=isinstance(data,dict) and self.build_instance is not None
        if construction:
            def load_constructed_state(state, serialized=False):
                if serialized:raise ValueError('Native construction needs structured state')
                objects={obj.name:obj for obj in scene.objects}
                state,changes=complete_native_articulation_state(
                    state,objects,[row['name'] for row in self.custom_spec.get('native_assets',[])])
                write_json(self.output/'native_articulation_defaults.json',{
                    'status':'passed','scope':'construction only; actual loaded native joint defaults',
                    'changes':changes})
                return original_load(state,serialized=False)
            scene.load_state=load_constructed_state
        try:
            scene.restore(data)
        finally:
            if construction:scene.load_state=original_load
            with gm.unlocked():
                gm.ENABLE_TRANSITION_RULES = enabled
            if enabled:
                self.env.scene.transition_rule_api.refresh_all_rules()

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
        if not spec.get('construction_seed'):
            protected={getattr(obj,'name',None) for obj in self.env.task.object_scope.values()}
            for name in spec.get('scene_exclusions',[]):
                if name in protected:
                    raise ValueError('Cannot exclude a base task binding: '+name)
                entry=scene['objects_info']['init_info'].pop(name)
                if entry['args'].get('category') in ('agent','floors','walls','ceilings','door'):
                    raise ValueError('Cannot exclude structural or robot entities: '+name)
                scene['state']['registry']['object_registry'].pop(name)
            provenance = []
            for item in spec.get('native_assets', []):
                name, category, model = item['name'], item['category'], item['model']
                usd = Path(os.environ['OMNIGIBSON_DATA_PATH'])/'behavior-1k-assets/objects'/category/model/'usd'/f'{model}.encrypted.usd'
                if hashlib.sha256(usd.read_bytes()).hexdigest() != item['usd_sha256']:
                    raise ValueError('Native construction asset changed: '+name)
                if name in scene['objects_info']['init_info']:
                    raise ValueError('Duplicate native construction asset: '+name)
                args = {k:item[k] for k in ('name','category','model','prim_type','fixed_base','scale') if k in item}
                scene['objects_info']['init_info'][name] = {
                    'class_module':'omnigibson.objects.dataset_object', 'class_name':'DatasetObject', 'args':args}
                scene['state']['registry']['object_registry'][name] = {
                    'is_asleep':False, 'root_link':{
                        'pos':item['position'], 'ori':item.get('orientation',[0.,0.,0.,1.]),
                        'lin_vel':[0.,0.,0.], 'ang_vel':[0.,0.,0.]}, 'non_kin':{}}
                provenance.append({'name':name,'native_asset':args,'usd_sha256':item['usd_sha256']})
            for item in spec.get('asset_imports', []):
                task = item['source_task']; source_scene = catalog[task][0]['scene_model']
                template = root/'scene_test/public'/source_scene/'json'/f'{source_scene}_task_{task}_0_0_template.json'
                data = json.loads(template.read_text()); old = item['source_object']; name = item.get('name', old)
                digest = hashlib.sha256(template.read_bytes()).hexdigest()
                if item.get('source_sha256') and item['source_sha256'] != digest:
                    raise ValueError('Construction source template changed: '+task)
                if name in scene['objects_info']['init_info'] and item.get('if_missing'):
                    continue
                if name in scene['objects_info']['init_info']: raise ValueError('Duplicate imported object: '+name)
                entry = copy.deepcopy(data['objects_info']['init_info'][old])
                entry['args']['name'] = name; entry['args'].pop('bddl_object_scope', None)
                if 'in_rooms' in item: entry['args']['in_rooms'] = item['in_rooms']
                if 'scale' in item: entry['args']['scale'] = list(item['scale'])
                if 'fixed_base' in item: entry['args']['fixed_base'] = item['fixed_base']
                state = imported_state_with_velocities(data['state']['registry']['object_registry'][old])
                if 'position' in item: state['root_link']['pos'] = item['position']
                if 'orientation' in item: state['root_link']['ori'] = item['orientation']
                scene['objects_info']['init_info'][name] = entry
                scene['state']['registry']['object_registry'][name] = state
                provenance.append({'name':name,'source_template':str(template),
                    'source_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),
                    'asset':entry['args']})
            # Restore supports together with imported assets. A second stop/play
            # would rewind newly imported bodies to their creation poses at origin
            # and trigger physical contacts/transitions before layout is applied.
            for fixture in spec.get('fixtures', []):
                name=fixture['name']
                if name in scene['objects_info']['init_info']:
                    raise ValueError('Duplicate construction support: '+name)
                scene['objects_info']['init_info'][name]={
                    'class_module':'omnigibson.objects.primitive_object', 'class_name':'PrimitiveObject',
                    'args':dict(name=name,primitive_type='Cube',size=1.,scale=fixture['size'],
                                fixed_base=True,rgba=fixture['rgba'],category='work_surface')}
                scene['state']['registry']['object_registry'][name]={
                    'is_asleep':False,'root_link':{'pos':fixture['position'],'ori':[0.,0.,0.,1.]},
                    'non_kin':{},'radius':-1,'height':-1,'size':1.}
            with self._startup_stage('custom_imported_scene_restore'):
                self._restore_custom_scene(scene)
        else:
            provenance=[{'construction_seed':spec['construction_seed'],'scope':'complete native objects loaded before GPU simulation'}]
        if spec.get('robot_start'):
            # Scene import can temporarily overlap newly authored supports
            # with the base task's old robot pose. Do not carry a displaced or
            # folded articulation into a CPU seed or the frozen task instance.
            with self._startup_stage('construction_robot_reset'):
                pose=spec['robot_start']
                self.robot.set_joint_positions(self.robot.reset_joint_pos.clone())
                self.robot.set_position_orientation(self.torch.tensor(pose['position']),
                                                    self.torch.tensor(pose['orientation']))
                self.robot.keep_still()
                self.robot.apply_action(self.robot.q_to_action(self.robot.get_joint_positions()))
        if self.geometry_only:
            # Macro particle templates contain native prims too. Include their
            # empty systems in the CPU seed so the fresh GPU scene loads them
            # before play(), instead of adding a template to a live GPU scene
            # at the first Covered setter. Do not create fluids without GPU
            # dynamics or apply any task initial conditions in this seed.
            from omnigibson.systems.macro_particle_system import MacroParticleSystem
            names=sorted({row['system'] for row in [*spec.get('initial_layout',[]),
                *spec['evaluation']['predicates'].values()] if row.get('system')})
            preloaded=[]
            for name in names:
                system=self.env.scene.get_system(name,force_init=False)
                if isinstance(system,MacroParticleSystem):
                    with self._startup_stage('geometry_system_template_'+name):
                        system=self.env.scene.get_system(name,force_init=True)
                        self.og.sim.step()
                        preloaded.append({'name':name,'class':type(system).__name__,
                                          'n_particles':system.n_particles})
                        if system.n_particles:
                            raise RuntimeError('Geometry seed unexpectedly contains task particles: '+name)
            write_json(self.output/'geometry_system_templates.json',{
                'status':'passed','systems':preloaded,
                'scope':'native empty templates only; material initial conditions remain unvalidated'})
            write_json(self.output/'custom_geometry_seed_validation.json',{'status':'passed',
                'scope':'object construction and serialization only; initial conditions and task not validated'})
            return
        objects = {o.name:o for o in self.env.scene.objects}
        roles = {k:objects.get(v) for k,v in spec['roles'].items()}
        missing=[role for role,obj in roles.items() if obj is None and role not in spec.get('future_roles',[])]
        if missing:raise RuntimeError('Required construction roles disappeared before layout: '+', '.join(missing))
        layout=spec.get('initial_layout', [])
        if spec.get('schema_version')==3:
            poses=[op for op in layout if op.get('place_on_support') or 'position' in op]
            layout=[op for op in layout if op not in poses]
            # Author every pose before advancing physics; unplaced objects
            # must not fall or tip while earlier items are being processed.
            for index,operation in enumerate(poses):
                with self._startup_stage(f'custom_pose_{index:03d}_{operation["object"]}'):
                    self._apply_initial_operation(operation,roles,settle=False)
            for _ in range(spec.get('settle_steps',30)):self.og.sim.step()
        for index,operation in enumerate(layout):
            with self._startup_stage(f'custom_layout_{index:03d}_{operation["object"]}'):
                self._apply_initial_operation(operation,roles)
        with self._startup_stage('construction_final_settle'):
            for _ in range(spec.get('settle_steps',30)): self.og.sim.step()
        self.custom_objects = objects
        with self._startup_stage('construction_predicates_before_refresh'):
            before_refresh = self._predicate_values()
        self._refresh_custom_contacts('construction')
        self._validate_initial_articulations(roles)
        initial_values = self._predicate_values()
        from ..tasks.long_horizon import expression
        failed=[label for label,condition in spec.get('initial_assertions',{}).items()
                if not expression(condition,initial_values)]
        write_json(self.output/'custom_construction_contact_refresh.json',{
            'status':'failed' if failed else 'passed','physics_steps':5,'before':before_refresh,
            'after':initial_values,'failed_assertions':failed})
        if failed:raise RuntimeError('Initial state assertion failed: '+', '.join(failed))
        write_json(self.output/'custom_scene_build.json', {'status':'passed','asset_imports':provenance,
            'excluded_background_entities':spec.get('scene_exclusions',[]),
            'initial_predicates':initial_values,'scope':'joint initial scene only; independent trajectory feasibility pending'})

    def _apply_initial_operation(self, operation, roles, *, settle=True):
        from omnigibson import object_states
        obj = roles[operation['object']]
        if operation.get('place_on_support'):
            support = roles[operation['target']]
            pos, ori = obj.get_position_orientation()
            if 'orientation' in operation:
                ori = self.torch.tensor(operation['orientation'],dtype=pos.dtype,device=pos.device)
                obj.set_position_orientation(pos,ori)
            low, high = obj.aabb
            if 'support_uv' in operation:
                slo,shi=support.aabb
                uv=self.torch.tensor(operation['support_uv'],dtype=pos.dtype,device=pos.device)
                xy=slo[:2]+uv*(shi[:2]-slo[:2])
            else:
                xy = self.torch.tensor(operation['xy'],dtype=pos.dtype,device=pos.device)
            pos = pos.clone()
            pos[:2] += xy-(low[:2]+high[:2])/2
            surface_height=float(support.aabb[1][2])
            if ('support_probe_height' in operation or
                    getattr(support,'category',None) in ('floors','lawn','driveway')):
                from omnigibson.utils.sampling_utils import raytest
                probe_height=operation.get('support_probe_height',surface_height+1.5)
                start=self.torch.tensor([float(xy[0]),float(xy[1]),probe_height],device=pos.device)
                end=start.clone();end[2]=float(support.aabb[0][2])-.2
                hit=raytest(start,end,ignore_bodies=[link.prim_path for link in obj.links.values()])
                if hit.get('rigidBody') not in {link.prim_path for link in support.links.values()}:
                    raise RuntimeError(f"Initial support point for {operation['object']} does not hit selected support: {hit.get('rigidBody')} at {xy.tolist()}")
                if float(hit['normal'][2])<.9:
                    raise RuntimeError('Initial support ray hit a nonhorizontal surface: '+operation['object'])
                surface_height=float(hit['position'][2])
            pos[2] += surface_height-low[2]+.004
            obj.set_position_orientation(pos,ori);obj.keep_still()
        elif 'position' in operation:
            pos = self.torch.tensor(operation['position'], dtype=self.torch.float32)
            ori = self.torch.tensor(operation.get('orientation',[0,0,0,1]), dtype=self.torch.float32)
            obj.set_position_orientation(pos,ori);obj.keep_still()
        else:
            cls = getattr(object_states, operation['state'])
            args = [roles[operation['target']]] if operation.get('target') else []
            if operation.get('system'): args.append(self.env.scene.get_system(operation['system'],force_init=True))
            successes=[]
            for attempt in range(operation.get('max_sampling_attempts',1)):
                from omnigibson.utils.object_state_utils import m as sampling_macros
                limits=(construction_native_sampling_limit(sampling_macros)
                        if self.custom_spec.get('reference_plan',{}).get('kind')=='full_workflow' else nullcontext())
                with limits:
                    if 'initial_open_fraction' in operation:
                        # This is explicit scene authoring, never a policy action
                        # or a repair while restoring an existing frozen task.
                        if cls is not object_states.Open or operation.get('value') is not True:
                            raise ValueError('Initial open fraction applies only to Open=True')
                        state=obj.states[cls]
                        for joint,position in initial_open_joint_targets(state,operation['initial_open_fraction']):
                            joint.set_pos(position);joint.set_vel(0.)
                        state.clear_cache()
                        success=bool(state.get_value())
                    else:
                        success=bool(obj.states[cls].set_value(*args, operation.get('value',True), **operation.get('kwargs',{})))
                successes.append(success)
                if success:break
                self.og.sim.step()
                for state in obj.states.values():state.clear_cache()
            with (self.output/'construction_state_sampling.jsonl').open('a') as stream:
                stream.write(json.dumps({'operation':operation,'attempt_results':successes})+'\n')
            if not successes[-1]:
                raise RuntimeError('Custom layout setter failed: '+str(operation))
        if settle:
            for _ in range(operation.get('settle_steps',5)): self.og.sim.step()

    def _validate_initial_articulations(self, roles):
        from omnigibson import object_states
        rows=[]
        for operation in self.custom_spec.get('initial_layout',[]):
            if 'initial_open_fraction' not in operation:continue
            obj=roles[operation['object']]
            state=obj.states[object_states.Open]
            for joint,_ in initial_open_joint_targets(state,operation['initial_open_fraction']):
                position,velocity=map(float,joint.get_state()[:2])
                low,high=float(joint.lower_limit),float(joint.upper_limit)
                passed=(all(map(math.isfinite,(position,velocity))) and
                        low-1e-4<=position<=high+1e-4 and abs(velocity)<=.05 and bool(state.get_value()))
                rows.append(dict(object=obj.name,joint=joint.name,position=position,velocity=velocity,
                                 limits=[low,high],status='passed' if passed else 'failed'))
        if not rows:return
        failed=any(r['status']!='passed' for r in rows)
        write_json(self.output/'construction_articulation_validation.json',dict(
            status='failed' if failed else 'passed',scope='post-settle initial openings; no state repair',joints=rows))
        if failed:raise RuntimeError('Initial articulation did not settle open within native joint limits')

    def _predicate_values(self):
        from omnigibson import object_states
        live = {obj.name:obj for obj in self.env.scene.objects}
        if self._initial_entity_names is None:
            self._initial_entity_names = set(live)
        roles = {key:live.get(name) for key,name in self.custom_spec['roles'].items()}
        result = {}
        for key, row in self.custom_spec['evaluation']['predicates'].items():
            obj = roles[row['object']]
            if row['state'] == 'Exists':
                result[key] = obj is not None; continue
            if row['state'] == 'Replaced':
                from ..tasks.diverse_task_contract import replacement_complete
                rule = next(r for r in self.custom_spec['replacements'] if r['parent'] == row['object'])
                result[key] = replacement_complete(rule,self.custom_spec['roles'],live,self._initial_entity_names)
                continue
            if row['state'] == 'HandEmpty':
                result[key] = self._get_held() is None; continue
            if obj is None or (row.get('target') and roles[row['target']] is None):
                result[key] = False; continue
            if row['state']=='NearInitial':
                current=obj.get_position_orientation()[0]
                initial=self._protected_initial_positions.setdefault(row['object'],current.clone())
                result[key]=float(self.torch.linalg.vector_norm(current-initial)) <= row.get('distance',.03)
                continue
            if row['state']=='Upright':
                from ..tasks.diverse_task_contract import upright_orientation
                result[key]=upright_orientation(obj.get_position_orientation()[1],row.get('max_tilt_degrees',15.))
                continue
            if row['state'] in ('PairedOnTop','CountOnTop'):
                members = [roles[m] for m in row['members'] if roles[m] is not None]
                supported = [m for m in members if object_states.OnTop in m.states
                             and m.states[object_states.OnTop].get_value(obj)]
                if row['state']=='CountOnTop':
                    result[key] = len(supported)==row['count']
                else:
                    result[key] = (len(supported)==2 and object_states.NextTo in supported[0].states
                                   and bool(supported[0].states[object_states.NextTo].get_value(supported[1])))
                continue
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
            if primitive == 'grasp' and self._get_held() is not None and self._carry_contents:
                for rule in self.custom_spec['evaluation'].get('carry_guards', []):
                    if not any(v['id']==rule['id'] for v in self.custom_tracker.violations):
                        self.custom_tracker.violations.append({'id':rule['id'],'kind':'aggregate_carry',
                            'env_step':self.steps,'carried_count':1+len(self._carry_contents)})
            self._check_custom_motion()
            self.custom_sequence += 1
            self.custom_tracker.observe(self._predicate_values(),self.custom_sequence,env_step=self.steps)
            write_json(self.output/'custom_goal_trace.json',self.custom_tracker.result(self._predicate_values()))
        return result

    def evaluate(self):
        values = self._predicate_values()
        result = self.custom_tracker.result(values)
        live = {obj.name for obj in self.env.scene.objects}
        replaced = {r['parent'] for r in self.custom_spec.get('replacements', [])
                    if values.get(r['parent']+'_replaced',False)}
        missing = [role for role,name in self.custom_spec['roles'].items() if name not in live and role not in replaced]
        result['missing_required_entities'] = missing
        result['task_success'] = result['task_success'] and not missing
        result.update(task_instance=self.task_metadata,input_hashes=self.input_hashes,
                      observation_mode='rgb_only',ideal_navigation_distance_m=self.navigation_distance)
        return result

    def provenance(self):
        result = super().provenance()
        result.update(task_instance=self.task_metadata,custom_task=True,
                      evaluator=self.custom_spec['evaluation'].get(
                          'score_type', 'custom_joint_terminal_and_public_history_v1'))
        return result
