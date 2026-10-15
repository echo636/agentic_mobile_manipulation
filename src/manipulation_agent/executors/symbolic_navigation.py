"""Native symbolic navigation combined with the project's manipulation backend.

Target selection stays visual. Native sampling, collision/IK and endpoint settling
are reused; no traversability path or waypoint execution is performed. This is a
hybrid executor: controlled carried assemblies still follow the robot's new pose.
"""
import hashlib
import inspect
import json
import time
from contextlib import contextmanager
from pathlib import Path

from ..contracts import SkillError
from ..records import now, write_json
from .official_navigation import initialize_official_navigation


class SymbolicNavigation:
    def _initialize_symbolic_navigation(self):
        from omnigibson.action_primitives.symbolic_semantic_action_primitives import (
            SymbolicSemanticActionPrimitives, SymbolicSemanticActionPrimitiveSet)
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveErrorGroup

        # Separate instance: logical held-object lookup must not change native
        # manipulation's grasp state or recursively call itself in nonideal mode.
        primitive = SymbolicSemanticActionPrimitives(self.env, self.robot)
        primitive._get_obj_in_hand = self._get_held
        self._navigation_primitives = primitive
        self._navigation_action = SymbolicSemanticActionPrimitiveSet.NAVIGATE_TO
        self._navigation_error_group = ActionPrimitiveErrorGroup
        self._navigation_attempts = inspect.signature(primitive.apply_ref).parameters['attempts'].default
        with self._startup_stage('official_navigation_planner'):
            self._navigation_compat = initialize_official_navigation(primitive, self.og.sim.floor_plane)
            self._navigation_compat['held_object_lookup'] = 'mainline_carry_state; navigation_instance_only'
            self._navigation_compat['upstream_sources'] = {
                cls.__name__: {'file': path, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                for cls in type(primitive).__mro__ if cls is not object
                if (path := inspect.getsourcefile(cls))}
            write_json(self.output / 'official_navigation_initialization.json', self._navigation_compat)

    @contextmanager
    def _navigation_physics_accounting(self):
        original = self.og.sim.step_physics

        def step(*args, **kwargs):
            self.deadline.check(changed=True)
            if self._inside_navigation_generator:
                self.sampling_physics_steps += 1
            return original(*args, **kwargs)

        self.og.sim.step_physics = step
        try:
            yield
        finally:
            self.og.sim.step_physics = original
            self._inside_navigation_generator = False

    def _navigate(self, target, max_steps):
        self.deadline.check()
        if max_steps <= 0:
            raise SkillError('budget_exhausted', 'No simulation steps remain')
        if target is None:
            raise SkillError('invalid_visual_target', 'Select a visible object to approach')
        before, sampling_before = self.steps, self.sampling_physics_steps
        started = time.monotonic()
        record = {'at': now(), 'status': 'running', 'audience': 'executor_private',
                  'strategy': 'official_symbolic', 'target_object': target.name,
                  'entrypoint': 'SymbolicSemanticActionPrimitives.apply_ref(NAVIGATE_TO)',
                  'attempts': self._navigation_attempts, 'start_step': before,
                  'start_position': self.robot.get_position_orientation()[0].tolist(),
                  'path_executed': False, 'carry_mode': 'controlled' if self.ideal_carry else 'official'}
        generator = None
        self._inside_navigation_generator = False
        try:
            # Keep the upstream retry default; do not silently use our GT planner
            # if the native sampler cannot find a valid collision-free stance.
            generator = self._navigation_primitives.apply_ref(self._navigation_action, target)
            with self._navigation_physics_accounting():
                while True:
                    self.deadline.check(changed=True)
                    self._inside_navigation_generator = True
                    try:
                        action = next(generator)
                    except StopIteration:
                        break
                    finally:
                        self._inside_navigation_generator = False
                    if self.steps - before >= max_steps:
                        raise SkillError('action_timeout', 'Navigation exceeded its physics-step budget', changed=True)
                    # RGBBackend._step preserves controlled contents before and
                    # after physics, including the first step after teleporting.
                    self._step(action)
            record['status'] = 'passed'
        except Exception as exc:
            record.update(status='failed', error_type=type(exc).__name__, error=str(exc))
            if isinstance(exc, SkillError):
                raise
            reason = 'execution_error'
            if isinstance(exc, self._navigation_error_group) and exc.exceptions:
                reason = exc.exceptions[-1].reason.name.lower()
            raise SkillError(reason, str(exc), changed=True) from exc
        finally:
            try:
                if generator is not None:
                    generator.close()
            finally:
                # The native endpoint can change pose before its first yield.
                # Synchronize payload even if cancellation/budget ends that yield.
                self._carry_follow()
                self.frames_revision = -1
                record.update(end_step=self.steps, env_steps=self.steps - before,
                              internal_physics_ticks=self.sampling_physics_steps - sampling_before,
                              end_position=self.robot.get_position_orientation()[0].tolist(),
                              duration_seconds=time.monotonic() - started)
                with (self.output / 'symbolic_navigation.jsonl').open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
        return {'primitive': 'navigate_to', 'strategy': 'official_symbolic',
                'nav_status': 'native_primitive_completed', 'steps': self.steps - before,
                'attempts': self._navigation_attempts, 'path_executed': False,
                'implementation': 'upstream_apply_ref_with_navigation_compat_and_project_carry',
                'postcondition': 'native_navigation_returned; inspect fresh RGB before manipulation'}
