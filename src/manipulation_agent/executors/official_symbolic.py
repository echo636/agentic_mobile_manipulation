"""Direct pinned upstream symbolic primitives with a private RGB target adapter.

No custom navigation, carry, placement, preconditions, retries or state repair.
Upstream can teleport objects and set semantic states; this is not physical control.
"""
from contextlib import contextmanager
import hashlib
import inspect
import json
import time
import traceback
from pathlib import Path

from ..contracts import SkillError
from ..omnigibson_backend import OmniGibsonBackend
from ..records import now
from .omnigibson_rgb import RGBBackend

OFFICIAL_PRIMITIVES = (
    'grasp', 'place_on_top', 'place_inside', 'open', 'close', 'toggle_on', 'toggle_off',
    'soak_under', 'soak_inside', 'wipe', 'cut', 'place_near_heating_element',
    'navigate_to', 'release',
)
PROTOCOL = 'rgb_official_symbolic_initialized_navigation_v2'


class OfficialSymbolicBackend(RGBBackend):
    official_symbolic = True

    def __init__(self, *args, **kwargs):
        # The base class's volume-placement override must never be selected.
        kwargs['inside_placement'] = 'symbolic_raycast'
        super().__init__(*args, **kwargs)
        from omnigibson.action_primitives.symbolic_semantic_action_primitives import SymbolicSemanticActionPrimitiveSet
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveErrorGroup
        from .symbolic_compat import adapt_symbolic_navigation_signature
        self._primitive_enum = SymbolicSemanticActionPrimitiveSet
        self._primitive_error_group = ActionPrimitiveErrorGroup
        self._navigation_endpoint_compat = adapt_symbolic_navigation_signature(self.primitives)
        self._inside_primitive = False
        self._official_terminated = False
        if set(p.name.lower() for p in self._primitive_enum) != set(OFFICIAL_PRIMITIVES):
            raise RuntimeError('Pinned official primitive inventory changed')
        # Symbolic registers inherited NAVIGATE_TO but skips the planner in its
        # constructor. Fail startup if the official planner cannot be prepared,
        # instead of admitting 100 policies to a deterministically broken tool.
        from omnigibson.action_primitives.curobo import CuRoboMotionGenerator, CuRoboEmbodimentSelection
        from omnigibson.action_primitives import curobo as native_curobo
        from curobo.wrap.reacher.evaluator import TrajEvaluatorConfig
        from curobo.rollout.arm_base import ArmBaseConfig
        from curobo.types.base import TensorDeviceType
        from .curobo_compat import (trajectory_evaluator_device, scene_mesh_cache_size,
                                    collision_mesh_cache_capacity)
        with self._startup_stage('official_navigation_planner'):
            configs=self.robot.curobo_path
            # Navigation also runs arm IK while validating a candidate base
            # pose, so all three native embodiments are required.
            required=(CuRoboEmbodimentSelection.DEFAULT,CuRoboEmbodimentSelection.ARM,CuRoboEmbodimentSelection.BASE)
            if not all(k in configs for k in required):
                raise RuntimeError('Robot does not provide official arm/base navigation configurations')
            device=f'cuda:{self.torch.cuda.current_device()}'
            tensor_args=TensorDeviceType(device=self.torch.device(device))
            self._navigation_mesh_cache = scene_mesh_cache_size(self.robot, self.og.sim.floor_plane)
            with trajectory_evaluator_device(TrajEvaluatorConfig,tensor_args), \
                    trajectory_evaluator_device(ArmBaseConfig,tensor_args,'from_dict'), \
                    collision_mesh_cache_capacity(native_curobo, self._navigation_mesh_cache['capacity']):
                self.primitives._motion_generator=CuRoboMotionGenerator(
                    robot=self.robot,robot_cfg_path={k:configs[k] for k in required},device=device,
                    batch_size=self.primitives._curobo_batch_size,collision_activation_distance=.02)
            if not all(k in self.primitives._motion_generator.mg for k in required):
                raise RuntimeError('Official navigation planner is missing a required embodiment')
            self._record_navigation_diagnostics('initialized')
        self._official_sources = {}
        for cls in type(self.primitives).__mro__:
            if cls is object:
                continue
            path = inspect.getsourcefile(cls)
            if path:
                self._official_sources[cls.__name__] = {'file': path, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        path=inspect.getsourcefile(CuRoboMotionGenerator)
        self._official_sources['CuRoboMotionGenerator']={'file':path,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    def _record_navigation_diagnostics(self, phase):
        from .curobo_compat import planner_cache_snapshot
        entry = {'at': now(), 'phase': phase, 'sim_step': self.steps,
                 'audience': 'executor_private', 'allocation': self._navigation_mesh_cache}
        # Diagnostic collection must not replace an original CUDA failure.
        try:
            entry.update(planner_cache_snapshot(self.primitives._motion_generator))
        except Exception as exc:
            entry['diagnostic_error'] = f'{type(exc).__name__}: {exc}'
        with (self.output / 'official_navigation_diagnostics.jsonl').open('a') as stream:
            stream.write(json.dumps(entry) + '\n')

    def _step(self, action):
        # Deliberately bypass RGBBackend._step and its pose/carry projection.
        if self._official_terminated:
            raise SkillError('simulation_ended', 'Simulation has ended', changed=True)
        obs, reward, terminated, truncated, info = self.env.step(action)
        self.steps += 1
        for metric in self.evaluator.metrics:
            metric.step(self.env, action, obs, reward, terminated, truncated, info)
        # Goal success may terminate the task on the first yielded action; allow
        # the upstream primitive to finish its own settling and postconditions.
        # A time-limit truncation still prevents additional environment steps.
        self._official_terminated = bool(truncated)
        finite = bool(self.torch.isfinite(self.robot.get_joint_positions()).all())
        with (self.output / 'official_control_steps.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': now(), 'sim_step': self.steps,
                'action': action.detach().cpu().tolist(), 'finite_robot_joints': finite,
                'terminated': bool(terminated), 'truncated': bool(truncated), 'audience': 'offline_only'}) + '\n')
        if not finite:
            raise SkillError('physics_instability', 'Nonfinite robot joints after official action', changed=True)
        self._video_frame('env_step')

    @contextmanager
    def _bounded_internal_physics(self, max_steps):
        """Count upstream sampler ticks separately, without pose/state repair."""
        original = self.og.sim.step_physics
        started = time.monotonic()
        before = self.sampling_physics_steps

        def step(*args, **kwargs):
            if self._inside_primitive:
                if self.sampling_physics_steps - before >= max_steps * 4 or time.monotonic() - started > 120:
                    raise SkillError('sampling_budget_exhausted', 'Upstream internal physics/time budget exhausted', changed=True)
                self.sampling_physics_steps += 1
            return original(*args, **kwargs)

        self.og.sim.step_physics = step
        try:
            yield
        finally:
            self.og.sim.step_physics = original
            self._inside_primitive = False

    def execute_visual(self, primitive, target, max_steps, **kwargs):
        if primitive not in OFFICIAL_PRIMITIVES or kwargs:
            raise SkillError('invalid_arguments', 'Only the official primitive signature is supported')
        if max_steps <= 0 or self._official_terminated:
            raise SkillError('budget_exhausted', 'No remaining simulation budget')
        if (primitive == 'release') != (target is None):
            raise SkillError('invalid_target', 'Only release takes a null target')
        obj = None
        grounding = None
        if target is not None:
            obj, _, grounding = self._ground(target)
        enum = getattr(self._primitive_enum, primitive.upper())
        before = self.steps
        sampling_before = self.sampling_physics_steps
        entry = {'at': now(), 'primitive': primitive, 'attempts': 1,
                 'target_object': getattr(obj, 'name', None), 'grounding': grounding,
                 'entrypoint': 'SymbolicSemanticActionPrimitives.apply_ref',
                 'start_step': before, 'audience': 'executor_private'}
        generator = None
        started = time.monotonic()
        try:
            if primitive == 'navigate_to':
                self._record_navigation_diagnostics('before_navigate_to')
            generator = self.primitives.apply_ref(enum, *([] if obj is None else [obj]), attempts=1)
            with self._bounded_internal_physics(max_steps):
                while True:
                    if time.monotonic() - started > 120:
                        raise SkillError('action_timeout', 'Official primitive time budget exhausted', changed=True)
                    self._inside_primitive = True
                    try:
                        action = next(generator)
                    except StopIteration:
                        break
                    finally:
                        self._inside_primitive = False
                    if self.steps - before >= max_steps:
                        raise SkillError('action_timeout', 'Official primitive step budget exhausted', changed=True)
                    self._step(action)
            entry['status'] = 'passed'
        except Exception as exc:
            entry.update(status='failed', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
            if isinstance(exc, SkillError):
                raise
            if isinstance(exc, self._primitive_error_group):
                reason = exc.exceptions[-1].reason.name.lower() if exc.exceptions else 'execution_error'
            else:
                reason = 'execution_error'
            # Preserve all upstream failure details privately; the public boundary
            # supplies fixed text and never returns object names or state values.
            raise SkillError(reason, str(exc), changed=True) from exc
        finally:
            if generator is not None:
                generator.close()
            if primitive == 'navigate_to':
                self._record_navigation_diagnostics('after_navigate_to')
            entry.update(end_step=self.steps, env_steps=self.steps-before,
                         internal_physics_ticks=self.sampling_physics_steps-sampling_before,
                         duration_seconds=time.monotonic()-started)
            with (self.output / 'official_primitives.jsonl').open('a') as stream:
                stream.write(json.dumps(entry) + '\n')
        return {'primitive': primitive, 'implementation': 'official_apply_ref', 'attempts': 1,
                'steps': self.steps-before, 'internal_physics_ticks': self.sampling_physics_steps-sampling_before,
                'postcondition': 'upstream_primitive_returned; not whole-task success'}

    def provenance(self):
        result = OmniGibsonBackend.provenance(self)
        result.pop('inside_placement', None)
        result.update(executor='official_symbolic_apply_ref', control_protocol=PROTOCOL,
            symbolic_primitives=True, physical_control=False, official_attempts=1,
            custom_navigation=False, custom_carry=False, custom_placement=False,
            automatic_approach=False, failure_rollback=False, target_scope='selected_visual_object',
            target_pixel_controls_placement_location=False, model_visible_truth=False,
            robot_camera_views=['front','back','left','right'], stock_wrist_cameras_enabled=False,
            record_video=self.record_video, upstream_sources=self._official_sources,
            primitive_inventory=list(OFFICIAL_PRIMITIVES),
            navigation_planner={'implementation':'upstream_CuRoboMotionGenerator','initialized':True,
                'device':f'cuda:{self.torch.cuda.current_device()}','embodiments':['DEFAULT','ARM','BASE'],
                'compatibility':'explicit_trajectory_evaluator_and_graph_rollout_tensor_device',
                'mesh_cache': self._navigation_mesh_cache},
            navigation_endpoint_compatibility=self._navigation_endpoint_compat,
            known_upstream_limitations=['Official symbolic grasp/toggle do not enforce this project\'s previous distance or automatic-approach checks'])
        return result

    def evaluate(self):
        result = OmniGibsonBackend.evaluate(self)
        result.pop('ideal_navigation_distance_m', None)
        result.pop('inside_placement', None)
        result.update(protocol=PROTOCOL, observation_mode='rgb_only', official_submission_eligible=False,
            execution_kind='official_symbolic_state_and_pose_changes',
            time_metric_scope='env.step only; upstream internal physics ticks counted separately')
        return result
