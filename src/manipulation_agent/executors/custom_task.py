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
from .timed_processes import TimedProcesses, VERSION as TIMED_PROCESSES_VERSION, check_appliance_capacity
from ..tasks.long_horizon import TemporalGoalEvaluator
from ..tasks.custom_tasks import validate_spec
from ..records import write_json
from ..runtime.robot_clock import robot_clock_record
from ..contracts import SkillError


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
        self.standalone_scene = self.custom_spec.get('standalone_scene')
        if self.standalone_scene and build_instance is not None:
            raise ValueError('Standalone import loads an existing frozen scene')
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
        self.timed_processes = TimedProcesses.from_spec(self.custom_spec)
        self._symbolic_process_wait = self.timed_processes is not None
        from .tool_cleanliness import ToolCleanlinessTracker
        self.tool_cleanliness = ToolCleanlinessTracker.from_spec(self.custom_spec)
        self.custom_tracker = None; self.custom_sequence = 0
        # Evaluator v9 (benchmark side, all methods): stacked items count as on the surface; final state saved.
        self.stacked_on_top = True
        self.save_final_state = True
        self._initial_entity_names = None
        self._protected_initial_positions = {}
        self.build_instance = Path(build_instance) if build_instance else None
        # Fixed supports created by this task are absent from the scene's baked map.
        self._native_map_added_fixtures = [fixture['name'] for fixture in self.custom_spec.get('fixtures', [])]
        self._custom_motion_positions = {}; self._custom_state_errors = []
        base = (dict(task=self.custom_spec['id'], instance=0, seed=self.custom_spec.get('seed', 0))
                if self.standalone_scene else self.custom_spec['base'])
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
        """Reject a tipped initial robot; ordinary navigation uses the native map."""
        pos,quat=self.robot.get_position_orientation()
        position=[float(v) for v in pos];orientation=[float(v) for v in quat]
        up_z=1.-2.*(orientation[0]**2+orientation[1]**2)
        record={'position':position,'orientation':orientation,
                'upright':math.isfinite(up_z) and up_z>=math.cos(math.radians(10.)),
                'validation_level':'read-only initial robot posture; not route feasibility or task success'}
        # rc1 (2026-11-20 22:00, KNOWN_ISSUES #15): the start lives in the frozen scene (base joints); robot_start is
        # only used when the scene is built. Refuse to start when the loaded start differs from robot_start, and when
        # a scene_exclusions entity is present -- both mean a construction field changed without a rebuild.
        mismatch=construction_mismatch(self.custom_spec,position,orientation,
                                       {getattr(o,'name',None) for o in self.env.scene.objects})
        record.update(construction_check=mismatch or 'passed',
                      start_check_switch=os.environ.get('MAS_START_CHECK','on'))  # 'off' -> episode not for tables
        record['status']='passed' if record['upright'] and not mismatch else 'failed'
        write_json(self.output/'custom_robot_start_validation.json',record)
        if not record['upright']:
            raise RuntimeError('Prepared robot must be upright')
        if mismatch and os.environ.get('MAS_START_CHECK','on')!='off':
            raise RuntimeError('Loaded scene does not match the task input: '+json.dumps(mismatch)+
                               '. A construction field (robot_start / scene_exclusions) changed; rebuild the frozen scene.')

    def _prepare_task_instance(self):
        """Called before camera setup / first policy observation."""
        spec = self.custom_spec
        if self.build_instance:
            self._apply_open_state_joints(spec)
            self._construct_instance()
            if self.build_instance.exists(): raise FileExistsError(self.build_instance)
            self.env.scene.save(json_path=str(self.build_instance))
            instance_path = self.build_instance
        else:
            frozen = spec['frozen_instance']
            instance_path = (self.spec_path.parent / frozen['path']).resolve()
            if hashlib.sha256(instance_path.read_bytes()).hexdigest() != frozen['sha256']:
                raise ValueError('Custom initial scene hash mismatch')
            if not self.standalone_scene:
                self._restore_custom_scene(str(instance_path))
        if self.geometry_only:return
        self.custom_objects = {o.name: o for o in self.env.scene.objects}
        for role, name in spec['roles'].items():
            if name not in self.custom_objects and role not in spec.get('future_roles', []):
                raise ValueError('Missing custom role: ' + role)
        self._apply_open_state_joints(spec)
        self._apply_fixed_supports(spec)
        self.custom_tracker = TemporalGoalEvaluator(spec['evaluation'])
        initial = self._predicate_values()
        self.custom_tracker.observe(initial, 0, env_step=0, initial=True)
        self._custom_motion_positions = self._motion_positions()
        self.input_hashes.update(custom_task_spec=hashlib.sha256(self.spec_path.read_bytes()).hexdigest(),
                                 custom_initial_scene=hashlib.sha256(instance_path.read_bytes()).hexdigest())
        self.task_metadata = {'task': spec['id'], 'instance': spec['instance_id'], 'seed': self.seed,
                              'split': 'custom_long_horizon_pilot', 'scene': self.env.scene.scene_model,
                              'base_public_task': spec.get('base'), 'official_submission_eligible': False}
        write_json(self.output / 'custom_instance_validation.json', {
            'status':'passed', 'validation_level':'scene loaded and all private predicates evaluated; feasibility separate',
            'task':self.task_metadata, 'input_hashes':self.input_hashes,
            'initial_predicates':initial, 'initial_evaluation':self.custom_tracker.result(initial),
            'role_poses':self._role_poses()})
        # Construction is outside policy time and outside the agent/video trace.
        self.steps = 0; self.frames_revision = -1

    # v11 (benchmark side, 2026-11-19): task inputs may declare large support furniture as fixed supports
    # (`fixed_supports`: role names). A one-armed robot cannot lift or shove such furniture in reality, so a
    # declared support refuses grasping through the existing fixed-furniture path (same receipt as any fixed
    # furniture) and gets a large link mass so contacts from placing or driving do not push it.
    FIXED_SUPPORT_MASS_KG = 1000.0

    def _apply_fixed_supports(self, spec):
        names, applied = set(), []
        for role in spec.get('fixed_supports', []):
            name = spec['roles'][role]
            obj = self.custom_objects.get(name)
            if obj is None:
                raise ValueError('Missing fixed support role: ' + role)
            names.add(name)
            row = {'role': role, 'name': name, 'fixed_base': bool(getattr(obj, 'fixed_base', False)), 'links': {}}
            if not row['fixed_base']:
                for link_name, link in obj.links.items():
                    before = float(link.mass)
                    link.mass = max(before, self.FIXED_SUPPORT_MASS_KG)
                    row['links'][link_name] = [round(before, 3), float(link.mass)]
            applied.append(row)
        self.fixed_support_names = frozenset(names)
        # v12 (benchmark side): a scene-authored movable support is not an obstacle in the baked traversability
        # map, so navigation endpoints could land 0.35 m from its edge and the base pushed it (v11 T1 tier2note_r3).
        # Block its footprint like a task-added fixture (native_astar.sync_added_fixture_map; erosion unchanged).
        added = list(getattr(self, '_native_map_added_fixtures', None) or [])
        for name in sorted(names):
            if name not in added:
                added.append(name)
        self._native_map_added_fixtures = added
        self._native_fixture_map_cache = None
        for row in applied:
            row['navigation_map_blocked'] = True
        if applied:
            write_json(self.output / 'fixed_supports.json', {'status': 'applied', 'audience': 'executor_private',
                                                             'supports': applied})

    def _role_poses(self):
        live = {o.name:o for o in self.env.scene.objects}
        return {role:([v.tolist() for v in live[name].get_position_orientation()] if name in live else None)
                for role,name in self.custom_spec['roles'].items()}

    def _load_standalone_environment(self, config):
        from .standalone_scene import load_environment
        load_environment(self, config)

    def _goal_options(self):
        return [] if self.standalone_scene else super()._goal_options()

    def close(self):
        recipe_policy = getattr(self, '_recipe_particle_output_policy', None)
        try:
            if recipe_policy is not None:
                write_json(self.output / 'recipe_particle_output_policy.json', recipe_policy.describe())
            super().close()
        finally:
            policy = getattr(self, '_standalone_sleep_policy', None)
            if policy is not None:
                policy.close()
            if recipe_policy is not None:
                recipe_policy.close()

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
            elif 'uv' in operation:
                slo,shi=support.aabb
                uv=self.torch.tensor(operation['uv'],dtype=pos.dtype,device=pos.device)
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

    # Benchmark side (evaluator v9, 2026-11-18, every method): an item resting on other movable task items that rest
    # on the required surface counts as on that surface ("stacked counts", user decision). Strict contact-only
    # results are still reported next to it (strict_contact_evaluation) so earlier numbers stay comparable.
    STACKED_ON_TOP_VERSION = 'stacked_on_top_v1'
    STACKED_MAX_DEPTH = 3

    def _direct_on_top(self, obj, target):
        from omnigibson import object_states
        if obj is None or target is None or object_states.OnTop not in obj.states:
            return False
        return bool(obj.states[object_states.OnTop].get_value(target)) or self._sleeping_on_top_fallback(obj, target)

    def _within_footprint(self, obj, target):
        try:
            lo, hi = target.aabb
            pos = obj.get_position_orientation()[0]
            return (float(lo[0]) <= float(pos[0]) <= float(hi[0])) and (float(lo[1]) <= float(pos[1]) <= float(hi[1]))
        except Exception:
            return False

    def _stacked_on_top(self, obj, target, movable):
        """obj rests on a chain of movable task items (no furniture in between) whose bottom item rests directly on
        target, and obj's centre lies over target's footprint. Inside relations are never relaxed."""
        if obj is None or target is None or not self._within_footprint(obj, target):
            return False
        frontier, seen = [obj], {obj.name}
        for _ in range(self.STACKED_MAX_DEPTH):
            nxt = []
            for top in frontier:
                for mid in movable:
                    if mid.name in seen or mid is target or not self._direct_on_top(top, mid):
                        continue
                    if self._direct_on_top(mid, target):
                        return True
                    seen.add(mid.name); nxt.append(mid)
            frontier = nxt
            if not frontier:
                break
        return False

    def _stacking_movable(self, objects):
        """Role objects that may form a layer of a stacked_on_top chain.
        rc1 A7 (2026-11-20, benchmark side, defect #26): fixed objects never form a layer -- neither fixed_base nor
        declared fixed_supports (furniture the robot cannot move). Before, a fixed support with fixed_base false (e.g. a
        nightstand standing on a floor) counted as a movable layer, so an item resting on it counted as on the floor."""
        fixed = getattr(self, 'fixed_support_names', ()) or ()
        return [o for o in objects if o is not None and not getattr(o, 'fixed_base', False)
                and getattr(o, 'name', None) not in fixed]

    def _note_a7_cut(self, key, obj, target, movable, cut_layers):
        """Private record only; always returns False so no predicate value changes. Records when the pre-A7 rule
        (fixed supports as layers) would have counted obj as stacked on target, i.e. a chain that A7 cut. Written once
        per (predicate, object) when the situation starts, with a running count in self.a7_cut_counts."""
        if not cut_layers or obj is None or target is None:
            return False
        try:
            would = self._stacked_on_top(obj, target, list(movable) + list(cut_layers))
        except Exception:
            return False
        counts = self.__dict__.setdefault('a7_cut_counts', {})
        active = self.__dict__.setdefault('_a7_cut_active', set())
        tag = (key, getattr(obj, 'name', None))
        if would and tag not in active:
            active.add(tag)
            counts[key] = counts.get(key, 0) + 1
            out = getattr(self, 'output', None)
            if out is not None:
                with (Path(out) / 'evaluator_diagnostics.jsonl').open('a') as f:
                    f.write(json.dumps({'audience': 'executor_private', 'event': 'a7_stacking_chain_cut',
                                        'predicate': key, 'object': getattr(obj, 'name', None),
                                        'target': getattr(target, 'name', None),
                                        'fixed_layers': sorted(getattr(o, 'name', '') for o in cut_layers),
                                        'env_step': getattr(self, 'steps', None)}) + '\n')
        elif not would:
            active.discard(tag)
        return False

    def _predicate_values(self, *, stacked=None):
        from omnigibson import object_states
        stacked = getattr(self, 'stacked_on_top', False) if stacked is None else stacked
        if getattr(self, 'timed_processes', None) is not None:
            self.timed_processes.sync_all_states(self)
        live = {obj.name:obj for obj in self.env.scene.objects}
        if self._initial_entity_names is None:
            self._initial_entity_names = set(live)
        roles = {key:live.get(name) for key,name in self.custom_spec['roles'].items()}
        movable = ([o for o in self._stacking_movable(roles.values()) if object_states.OnTop in o.states]
                   if stacked else [])
        # rc1 A7 private counter (coordinator 18:07): objects that the pre-A7 rule would have used as a layer.
        cut_layers = ([o for o in roles.values() if o is not None and not getattr(o, 'fixed_base', False)
                       and o not in movable and object_states.OnTop in o.states] if stacked else [])
        result = {}
        for key, row in self.custom_spec['evaluation']['predicates'].items():
            obj = roles[row['object']]
            if row['state'] == 'ProcessComplete':
                result[key] = bool(self.timed_processes
                                   and self.timed_processes.is_completed(row['process'], row['object']))
                continue
            if row['state'] == 'ProcessOvercooked':
                result[key] = bool(self.timed_processes
                                   and self.timed_processes.is_overcooked(row['process'], row['object']))
                continue
            if row['state'] in ('CrossContaminationViolation', 'ToolCleanlinessViolation'):
                result[key] = bool(self.tool_cleanliness and self.tool_cleanliness.has_violation())
                continue
            if row['state'] == 'SystemExists':
                result[key] = row['system'] in self.env.scene.active_systems; continue
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
                             and (bool(m.states[object_states.OnTop].get_value(obj))
                                  or self._sleeping_on_top_fallback(m, obj)
                                  or (stacked and (self._stacked_on_top(m, obj, movable)
                                                   or self._note_a7_cut(key, m, obj, movable, cut_layers))))]
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
            val = bool(obj.states[cls].get_value(*args))
            if not val and row['state'] == 'OnTop' and len(args) == 1:
                val = self._sleeping_on_top_fallback(obj, args[0])
            if not val and stacked and row['state'] == 'OnTop' and len(args) == 1:
                val = self._stacked_on_top(obj, args[0], movable)
                if not val:
                    self._note_a7_cut(key, obj, args[0], movable, cut_layers)
            result[key] = val
        if self.tool_cleanliness is not None:
            result['cross_contamination_violation'] = self.tool_cleanliness.has_violation()
        return result

    def _sleeping_on_top_fallback(self, obj, target):
        """Read-only geometric support fallback when PhysX sleeping omits ContactBodies."""
        if obj is None or target is None or self._get_held() is obj:
            return False
        try:
            contacts = self._placement_contacts(obj, ())
            robot_paths = {link.prim_path for link in self.robot.links.values()}
            if any(path in robot_paths for path in contacts):
                return False
            speed = float(self.torch.linalg.norm(obj.get_linear_velocity()))
            angular_speed = float(self.torch.linalg.norm(obj.get_angular_velocity()))
            if speed > .10 or angular_speed > .5:
                return False
            from omnigibson.object_states import VerticalAdjacency
            if VerticalAdjacency in obj.states:
                adj = obj.states[VerticalAdjacency].get_value()
                if target in adj.positive_neighbors:
                    return False
            from .surface_geometry import bottom_support_points
            from omnigibson.utils.sampling_utils import raytest
            pos = obj.get_position_orientation()[0]
            probes = self.torch.as_tensor(
                bottom_support_points(self._collision_vertices(obj)),
                dtype=pos.dtype, device=pos.device)
            ignored = [link.prim_path for body in (obj, self.robot) for link in body.links.values()]
            target_paths = {link.prim_path for link in target.links.values()}
            is_cloth = (
                getattr(obj, 'category', None) in {'rag', 'cloth', 'towel', 'dishtowel'}
                or getattr(obj, 'prim_type', None) == 1
                or hasattr(getattr(obj, 'root_link', None), 'compute_particle_positions')
            )
            max_gap = .025 if is_cloth else .015
            for probe in probes:
                start = probe + self.torch.tensor([0., 0., .03], device=pos.device)
                end = probe - self.torch.tensor([0., 0., .04], device=pos.device)
                hit = raytest(start, end, ignore_bodies=ignored)
                if not hit.get('hit') or hit.get('rigidBody') not in target_paths:
                    continue
                gap = float(probe[2] - hit['position'][2])
                normal_z = float(hit['normal'][2])
                if normal_z >= .9 and -.01 <= gap <= max_gap:
                    return True
        except Exception:
            return False
        return False

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
        if getattr(self, 'timed_processes', None) is not None:
            self.timed_processes.sync_all_states(self)
        if self.custom_tracker is not None: self._check_custom_motion()

    def _is_cleanliness_water_source(self, target):
        if not getattr(self, 'tool_cleanliness', None):
            return False
        roles = self.custom_spec.get('roles', {})
        name_to_role = {name: role for role, name in roles.items()}
        target_name = getattr(target, 'name', None)
        target_role = name_to_role.get(target_name)
        return target_role in self.tool_cleanliness.water_sources

    def _soak_preconditions(self, target):
        if self._is_cleanliness_water_source(target):
            tool = self._get_held()
            if tool is None:
                raise SkillError('empty_hand', 'A tool must be carried first')
            return None, []
        return super()._soak_preconditions(target)

    def _checked_soak(self, target, max_steps):
        if self._is_cleanliness_water_source(target):
            tool = self._get_held()
            if tool is None:
                raise SkillError('empty_hand', 'A tool must be carried first')
            from omnigibson.object_states import Saturated
            if Saturated in tool.states:
                sat = tool.states[Saturated]
                for system in list(tool.scene.system_registry.objects):
                    try:
                        if sat.get_value(system):
                            sat.set_value(system, False)
                    except Exception:
                        pass
            for _ in range(min(6, max_steps)):
                self._step(self.primitives._empty_action())
            return {
                'primitive': 'soak',
                'implementation': 'cleanliness_water_source_rinse',
                'water_source': getattr(target, 'name', 'sink'),
                'postcondition': 'tool_cleanliness_decontaminated',
            }
        return super()._checked_soak(target, max_steps)

    def _resolve_interior_surface_target(self, primitive, target):
        """When wiping an open container whose interior shelf holds the surface state, bind that shelf."""
        if primitive != 'wipe' or target is None or not hasattr(target, 'states'):
            return target
        from omnigibson.object_states import Covered, Open
        scene = getattr(target, 'scene', None)
        if scene is None:
            return target
        systems = list(scene.system_registry.objects)
        if Covered in target.states and any(target.states[Covered].get_value(sys) for sys in systems):
            return target
        if Open not in target.states:
            return target
        roles = self.custom_spec.get('roles', {})
        pred_objects = {
            row.get('object')
            for row in self.custom_spec.get('evaluation', {}).get('predicates', {}).values()
            if row.get('state') == 'Covered'
        }
        if self.tool_cleanliness is not None:
            pred_objects.update(self.tool_cleanliness.surface_levels.keys())
        if not pred_objects:
            return target
        live = {obj.name: obj for obj in self.env.scene.objects}
        t_low, t_high = target.aabb
        candidates = []
        for role_name in sorted(pred_objects):
            ent_name = roles.get(role_name)
            child = live.get(ent_name) if ent_name else None
            if child is None or child is target or Covered not in child.states:
                continue
            c_low, c_high = child.aabb
            c_center = [0.5 * float(c_low[i] + c_high[i]) for i in range(3)]
            if all(float(t_low[i]) - 0.08 <= c_center[i] <= float(t_high[i]) + 0.08 for i in range(3)):
                candidates.append(child)
        if not candidates:
            return target
        if not bool(target.states[Open].get_value()):
            raise SkillError('container_closed', 'Open the container before wiping its interior surface')
        for child in candidates:
            if any(child.states[Covered].get_value(sys) for sys in systems):
                return child
        return candidates[0]

    def _apply_open_state_joints(self, spec):
        """Benchmark side (2026-11-21): ``open_state_joints`` {role: [joint names]} limits an appliance's Open
        state (read and set) to the named joints, like the ``openable_joint_ids`` annotation other models carry.
        oven/ffitak has none, so opening its door also pulled both racks out 18.6 cm and a loaded rack bouncing
        back kept the closed oven "open" (C2 r2 lasagna, three close attempts)."""
        rows = spec.get('open_state_joints') or {}
        if not rows:
            return
        from omnigibson.object_states import Open
        live = {o.name: o for o in self.env.scene.objects}
        applied = {}
        for role, names in rows.items():
            obj = live.get(spec['roles'][role])
            if obj is None or Open not in obj.states:
                raise ValueError(f'open_state_joints: {role} has no Open state')
            joints = [obj.joints[n] for n in names]
            obj.states[Open].relevant_joints_info = (False, joints, [1] * len(joints))
            obj.states[Open].clear_cache()
            # 2026-11-23: the other movable joints (ffitak's two racks) are no longer Open joints, so the rc1
            # articulation hold no longer keeps them in; the door then dragged a loaded rack and the reference's
            # open/close was rejected for displaced contents (C2 r4 close, C3 open). No task slides a rack, so they
            # are locked at their pushed-in (lower) limit; limits of non-Open joints do not affect any Open threshold.
            locked = []
            for jname, joint in obj.joints.items():
                if jname in names or not getattr(joint, 'is_single_dof', False):
                    continue
                if getattr(joint, 'joint_type', None) not in ('PrismaticJoint', 'RevoluteJoint'):
                    continue
                low = float(joint.lower_limit)
                joint.set_pos(low)
                joint.upper_limit = low + 1e-4
                locked.append({'joint': jname, 'type': joint.joint_type, 'locked_at': low})
            applied[role] = {'open_state': list(names), 'locked_in': locked}
        self._open_state_joints_applied = applied
        if getattr(self, 'output', None) is not None:
            write_json(self.output / 'open_state_joints.json', {'audience': 'executor_private', 'applied': applied})

    def _released_inside_extra(self, held, target):
        # Benchmark side (2026-11-21): for an appliance with a declared Inside timed process, an item whose
        # centre rests within the appliance body (base link, door excluded) is inside its cavity. Native Inside
        # misses the cavity floor below ffitak's 41 x 22 cm racks, where dishes slide (C1 r1-r3, C2 r2 on 4090).
        roles = self.custom_spec.get('roles', {})
        name = getattr(target, 'name', None)
        appliances = {roles.get(r['appliance']) for r in self.custom_spec.get('timed_processes') or []
                      if r.get('containment', 'Inside') == 'Inside'}
        if name not in appliances:
            return False
        try:
            body = target.links.get('base_link') if hasattr(target, 'links') else None
            lo, hi = (body.aabb if body is not None else target.aabb)
            ilo, ihi = held.aabb
            c = [float(ilo[i] + ihi[i]) * 0.5 for i in range(3)]
            return all(float(lo[i]) <= c[i] <= float(hi[i]) for i in range(3))
        except Exception:
            return False

    def _checked_place_inside(self, target, max_steps, point=None):
        # Benchmark side (timed_processes v5): a declared appliance capacity rejects before any motion.
        if self.custom_spec.get('appliance_capacities'):
            check_appliance_capacity(self.custom_spec, {o.name: o for o in self.env.scene.objects},
                                     self._get_held(), target)
        return super()._checked_place_inside(target, max_steps, point=point)

    def _surface_action_preconditions(self, primitive, target):
        target = self._resolve_interior_surface_target(primitive, target)
        return super()._surface_action_preconditions(primitive, target)

    def _checked_surface_action(self, primitive, target, max_steps):
        resolved = self._resolve_interior_surface_target(primitive, target)
        res = super()._checked_surface_action(primitive, resolved, max_steps)
        if resolved is not target and isinstance(res, dict):
            res = {**res, 'resolved_surface_object': getattr(resolved, 'name', None)}
        return res

    def execute_visual(self, primitive, target, max_steps, **kwargs):
        if self.custom_tracker is None: return super().execute_visual(primitive,target,max_steps,**kwargs)
        # Transactional executor errors restore the scene. Do not award history
        # based on internal sampler candidates: milestones only use action ends.
        before_violations = copy.deepcopy(self.custom_tracker.violations)
        before_positions = self._motion_positions()
        before_values = self._predicate_values()
        proc_snap = self.timed_processes.before_action(self) if self.timed_processes is not None else None
        try:
            result = super().execute_visual(primitive,target,max_steps,**kwargs)
            if self.timed_processes is not None:
                public_proc = self.timed_processes.after_action(
                    self, proc_snap, primitive, details=result, error=None)
                if public_proc is not None:
                    result = {**result, 'public_process': public_proc}
            if self.tool_cleanliness is not None:
                public_tc = self._update_tool_cleanliness_after_action(primitive, target, result)
                if public_tc is not None and isinstance(result, dict):
                    result = {**result, 'public_tool_cleanliness': public_tc}
        except Exception as exc:
            if self.timed_processes is not None:
                self.timed_processes.after_action(self, proc_snap, primitive, details=None, error=exc)
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

    def _update_tool_cleanliness_after_action(self, primitive, target, result):
        if self.tool_cleanliness is None or primitive not in ('wipe', 'soak', 'grasp'):
            return None
        roles = self.custom_spec.get('roles', {})
        name_to_role = {name: role for role, name in roles.items()}
        held = self._get_held()
        held_role = name_to_role.get(getattr(held, 'name', None)) if held is not None else None
        if held_role not in self.tool_cleanliness.tools:
            return None
        if primitive == 'grasp':
            return self.tool_cleanliness.public_receipt('grasp')

        target_role = None
        if isinstance(result, dict) and result.get('resolved_surface_object'):
            target_role = name_to_role.get(result['resolved_surface_object'], result['resolved_surface_object'])
        grounding = result.get('private_grounding', {}) if isinstance(result, dict) else {}
        selected_prim = grounding.get('selected_object_prim')
        selected_obj = grounding.get('selected_object')

        if target_role is None and isinstance(target, dict) and target.get('role'):
            target_role = target['role']
        elif target_role is None and selected_obj is not None and hasattr(selected_obj, 'name'):
            target_role = name_to_role.get(selected_obj.name)
        elif target_role is None and selected_prim:
            for obj in self.env.scene.objects:
                if str(obj.prim_path) == str(selected_prim) or str(selected_prim).startswith(str(obj.prim_path) + '/'):
                    target_role = name_to_role.get(obj.name)
                    if target_role:
                        break

        if target_role is None and isinstance(result, dict) and result.get('water_source'):
            target_role = name_to_role.get(result['water_source'], result['water_source'])

        if target_role is None and isinstance(target, str):
            target_role = name_to_role.get(target, target)

        if primitive == 'wipe':
            compliant = None
            if target_role is not None:
                level = self.tool_cleanliness.get_surface_level(target_role)
                if level is not None:
                    compliant = self.tool_cleanliness.record_wipe(level, surface=target_role)
            return self.tool_cleanliness.public_receipt('wipe', compliant=compliant)
        elif primitive == 'soak':
            was_water = False
            if target_role is not None:
                was_water = self.tool_cleanliness.record_soak(target_role)
            return self.tool_cleanliness.public_receipt('soak_reset' if was_water else 'soak')
        return None

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
        result.update(robot_clock_record(self))
        if self.timed_processes is not None:
            result['timed_processes'] = self.timed_processes.summary(self)
        if self.tool_cleanliness is not None:
            result['tool_cleanliness'] = self.tool_cleanliness.to_dict()
        if getattr(self, 'stacked_on_top', False):
            strict_values = self._predicate_values(stacked=False)
            strict = self.custom_tracker.result(strict_values)
            result['evaluator_relaxations'] = [self.STACKED_ON_TOP_VERSION]
            result['strict_contact_evaluation'] = {
                'task_success': strict['task_success'] and not missing,
                'terminal_conditions': strict['terminal_conditions'],
                'terminal_satisfaction_fraction': strict['terminal_satisfaction_fraction']}
        if getattr(self, 'save_final_state', False):
            self._write_final_state(values)
        return result

    def _write_final_state(self, values):
        """Benchmark-side record (evaluator v9): final poses of every task role object plus all predicate values,
        so a later change of scoring rules can be re-judged offline. Never shown to the model."""
        live = {obj.name: obj for obj in self.env.scene.objects}
        poses = {}
        for role, name in self.custom_spec['roles'].items():
            obj = live.get(name)
            if obj is None:
                poses[role] = None; continue
            try:
                pos, quat = obj.get_position_orientation()
                lo, hi = obj.aabb
                poses[role] = {'name': name, 'position': [float(v) for v in pos], 'orientation': [float(v) for v in quat],
                               'aabb': [[float(v) for v in lo], [float(v) for v in hi]]}
            except Exception as exc:
                poses[role] = {'name': name, 'error': repr(exc)[:200]}
        write_json(self.output / 'final_state.json', {
            'audience': 'evaluator_private', 'roles': poses,
            'predicates': {k: bool(v) for k, v in values.items()},
            'strict_predicates': ({k: bool(v) for k, v in self._predicate_values(stacked=False).items()}
                                  if getattr(self, 'stacked_on_top', False) else None)})

    def provenance(self):
        result = super().provenance()
        result.update(task_instance=self.task_metadata,custom_task=True,
                      evaluator=self.custom_spec['evaluation'].get(
                          'score_type', 'custom_joint_terminal_and_public_history_v1'))
        if getattr(self, '_recipe_particle_output_policy', None) is not None:
            result['recipe_particle_output_policy'] = self._recipe_particle_output_policy.describe()
        if self.timed_processes is not None:
            result['timed_processes'] = TIMED_PROCESSES_VERSION
        if self.tool_cleanliness is not None:
            from .tool_cleanliness import VERSION as TOOL_CLEANLINESS_VERSION
            result['tool_cleanliness'] = TOOL_CLEANLINESS_VERSION
        if getattr(self, 'stacked_on_top', False):
            result['evaluator_relaxations'] = [self.STACKED_ON_TOP_VERSION]
        return result

def construction_mismatch(spec, position, orientation, scene_names, *, max_position_m=.05, max_yaw_degrees=5.):
    """Pure check (unit-tested): loaded robot start vs robot_start, and excluded entities still in the scene."""
    out={}
    start=spec.get('robot_start')
    if start:
        def yaw(q):
            return math.degrees(math.atan2(2*(q[3]*q[2]+q[0]*q[1]),1-2*(q[1]**2+q[2]**2)))
        dp=math.hypot(position[0]-start['position'][0],position[1]-start['position'][1])
        dy=abs((yaw(orientation)-yaw(start['orientation'])+180.)%360.-180.)
        if dp>max_position_m or dy>max_yaw_degrees:
            out['robot_start']={'loaded_xy':[round(position[0],3),round(position[1],3)],'loaded_yaw_deg':round(yaw(orientation),1),
                                'input_xy':list(start['position'][:2]),'input_yaw_deg':round(yaw(start['orientation']),1),
                                'position_error_m':round(dp,3),'yaw_error_deg':round(dy,1)}
    present=sorted(set(spec.get('scene_exclusions',[]))&set(scene_names))
    if present:
        out['scene_exclusions_present']=present
    return out
