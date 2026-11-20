"""Transactional native pick execution; cameras are an observer, never a controller.

The publisher embeds this module in the two-file pick bundle, so already running
hosts can load it between actions without changing their imported host classes.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import time
import uuid

import numpy as np

from manipulation_agent.contracts import SkillError
from manipulation_agent.records import now, write_json
from manipulation_agent.video import EpisodeVideo


class PickObserverVideo(EpisodeVideo):
    VIEWS = ('spectator', 'left_wrist', 'right_wrist')


def _surround_video_sensors(backend):
    return {**backend.rig, **backend.extra_rgb_sensors, 'spectator': backend.spectator}


class PickRecordingSeries:
    """Preserve the historical prefix and index honest, contiguous video segments.

    Host recording outside Manip can continue through this facade. Its global
    step numbers are translated to each segment's local timeline. No missing
    camera frames are synthesized to extend the old seven-camera recording.
    """
    def __init__(self, backend, previous):
        self.b = backend
        self.views = tuple(_surround_video_sensors(backend))
        self.closed = False
        self.active = None
        self.end_step = backend.steps
        self.context = ('continuation', {}, None)
        self.manifest = {'status': 'running', 'segmented': True, 'segments': [],
                         'kind': 'segmented_recording_index',
                         'file': 'recording_segments.json', 'views_after_transition': list(self.views)}
        if previous is not None:
            summary = previous.finish(backend.steps)
            self.manifest['segments'].append({'kind': 'historical_prefix', 'start_step': 0,
                'end_step': backend.steps, 'root': str(previous.output), 'video': deepcopy(summary)})
        elif backend.steps:
            self.manifest['unrecorded_prefix_steps'] = backend.steps
        self.save()

    def save(self):
        write_json(self.b.output / 'recording_segments.json', self.manifest)

    def mark(self, name, arguments, request_id):
        self.context = (name, arguments, request_id)
        if self.active is not None:
            self.active.mark(name, arguments, request_id)

    def mark_phase(self, phase):
        if self.active is not None:
            self.active.mark_phase(phase)

    def append(self, pixels, env_step, kind, *, capture_env_step=None, **kwargs):
        if self.closed:
            return
        if self.active is None:
            self.offset = self.end_step
            root = self.b.output / 'observer_segments' / uuid.uuid4().hex
            root.mkdir(parents=True)
            self.active = EpisodeVideo(root, fps=1 / float(self.b.og.sim.get_sim_step_dt()),
                size=self.b.image_size, views=self.views,
                execution_caption='Surround RGB continuation; native physics.')
            self.active.mark(*self.context)
        self.active.append({key: pixels[key] for key in self.views}, env_step-self.offset, kind,
            capture_env_step=None if capture_env_step is None else capture_env_step-self.offset, **kwargs)
        # A retained simulator may never call finish(). Flush the encoder at
        # the already captured observation boundary so the last action is
        # exportable without another action, render, or simulator shutdown.
        if kind == 'observation_boundary':
            self.checkpoint(env_step)

    def checkpoint(self, env_step):
        """Seal only the current video segment; later recording stays enabled."""
        if self.active is None:
            return
        try:
            self.begin_trial(env_step)
        except (OSError, RuntimeError, ValueError) as exc:
            # Recording faults must not turn a successful native action into a
            # motor failure. Keep the fault visible and the next segment usable.
            self.manifest['checkpoint_failure'] = {
                'env_step': env_step, 'type': type(exc).__name__, 'message': str(exc)}
            self.manifest['status'] = 'failed'
            self.active = None
            self.end_step = env_step
            try:
                self.save()
            except OSError:
                pass

    def begin_trial(self, start_step):
        if self.active is not None:
            video = self.active.finish(start_step-self.offset)
            self.add('observer_continuation', self.offset, start_step, self.active.output, video)
            self.active = None
        if start_step != self.end_step:
            raise ValueError('Unrecorded steps before open-loop trial')

    def add(self, kind, start, end, root, video):
        if start != self.end_step:
            raise ValueError('Video segment timelines are not contiguous')
        self.manifest['segments'].append({'kind': kind, 'start_step': start, 'end_step': end,
                                          'root': str(root), 'video': deepcopy(video)})
        self.end_step = end
        self.save()

    def finish(self, final_env_step):
        self.begin_trial(final_env_step)
        self.closed = True
        complete = not self.manifest.get('unrecorded_prefix_steps') and not self.manifest.get('checkpoint_failure') and all(
            item['video'].get('every_env_step_recorded', False) for item in self.manifest['segments'])
        passed = complete and all(item['video'].get('status') == 'passed'
                                  for item in self.manifest['segments'])
        self.manifest.update(status='passed' if passed else 'failed', finished_at=now(),
                             final_env_step=final_env_step, every_env_step_recorded=complete)
        self.save()
        return self.manifest


def _restore_instance_attribute(obj, name, present, value):
    if present:
        setattr(obj, name, value)
    else:
        vars(obj).pop(name, None)


def _snapshot_difference(expected, actual):
    """Compare stored physical state, including velocities and grasp/controller state."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or expected.keys() != actual.keys():
            raise ValueError('Snapshot state keys differ')
        return max((_snapshot_difference(v, actual[k]) for k, v in expected.items()), default=0.)
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(expected) != len(actual):
            raise ValueError('Snapshot state lengths differ')
        return max((_snapshot_difference(a, b) for a, b in zip(expected, actual)), default=0.)
    if hasattr(expected, 'detach'):
        expected = expected.detach().cpu().numpy()
        actual = actual.detach().cpu().numpy()
    if isinstance(expected, (np.ndarray, int, float, np.number)):
        a, b = np.asarray(expected), np.asarray(actual)
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError('Invalid snapshot numeric state')
        if not np.allclose(a, b, rtol=1e-5, atol=1e-6):
            raise ValueError('Restored physical state differs from checkpoint')
        return float(np.max(np.abs(a.astype(float)-b.astype(float)))) if a.size else 0.
    if expected != actual:
        raise ValueError('Restored snapshot value differs')
    return 0.


class OpenLoopPickTrial:
    """Native transaction with Manip or surround recording; failure rolls back."""
    def __init__(self, backend, event, *, record_surround=False):
        self.b, self.event = backend, event
        self.record_surround = record_surround
        self.sensors = (_surround_video_sensors(backend) if record_surround else
                        {'spectator': backend.spectator,
                         **{key: backend.extra_rgb_sensors[key] for key in ('left_wrist', 'right_wrist')}})
        self.accepted = False
        self.stack = ExitStack()
        self.samples = []
        self.steps = 0
        self.pixels = None
        self.capture_step = 0
        self.timings = {}
        self.phases = []
        self.phase_started = None
        self.manifest = {'mode': 'open_loop_rollback', 'video_views': list(self.sensors),
                         'video_used_for_control': False, 'rollback': 'not_needed',
                         'accounting': 'attempt steps and failure evidence never rewind',
                         'state_equivalence': 'stored physical state, not bitwise solver determinism'}

    @contextmanager
    def measure(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            row = self.timings.setdefault(name, {'seconds': 0., 'calls': 0})
            row['seconds'] += time.perf_counter()-start
            row['calls'] += 1

    def enter_phase(self, name):
        clock = time.perf_counter()
        if self.phase_started is not None:
            self.phases.append({'phase': self.phase_name, 'seconds': clock-self.phase_started,
                'start_control_step': self.phase_start_step, 'end_control_step': self.steps,
                'control_steps': self.steps-self.phase_start_step,
                'components': {key: {field: value[field]-self.phase_components.get(key, {}).get(field, 0)
                                     for field in value} for key, value in self.timings.items()}})
        self.phase_name, self.phase_started, self.phase_start_step = name, clock, self.steps
        self.phase_components = deepcopy(self.timings)
        self.progress()

    def progress(self):
        if self.manifest.get('progress_write_error'):
            return
        try:
            write_json(self.root / 'progress.json', {'updated_at': now(),
                'request_id': self.manifest['request_id'], 'phase': self.phase_name,
                'control_steps': self.steps, 'global_step': self.b.steps,
                'wall_seconds': time.perf_counter()-self.started_clock,
                'phase_seconds': time.perf_counter()-self.phase_started,
                'completed_phases': self.phases, 'components': self.timings})
        except OSError as error:
            self.manifest['progress_write_error'] = str(error)

    def _identities(self):
        return tuple((id(scene), frozenset(id(obj) for obj in scene.objects),
                      tuple(sorted((name, id(system)) for name, system in scene.active_systems.items())))
                     for scene in self.b.og.sim.scenes)

    def _remember_attributes(self, obj, names):
        return (obj, {name: (name in vars(obj), deepcopy(getattr(obj, name)))
                      for name in names if hasattr(obj, name)})

    def _set_camera_updates(self, texture, enabled):
        # Hydra's setter changes viewportHandle USD attributes. OG requires its
        # edit scope both when suspending a camera and when restoring it.
        with self.b.og.sim.editing_usd():
            texture.updates_enabled = enabled

    def __enter__(self):
        b = self.b
        self.started_clock = time.perf_counter()
        if getattr(b.env, '_automatic_reset', False):
            raise SkillError('rollback_unavailable', 'Open-loop trials require automatic_reset=false')
        self.start_step = b.steps
        self.root = b.output / 'open_loop_trials' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.manifest.update(start_step=b.steps, started_at=now(),
                             request_id=getattr(b, '_active_request_id', None))
        self.state = deepcopy(b.og.sim.dump_state(serialized=False))
        self.identities = self._identities()
        from manipulation_agent.executors.particle_payload import snapshot_particle_state
        self.particles = snapshot_particle_state(b.og.sim)
        self.command = b._command_q.clone()
        self.grippers = dict(b._gripper_commands)
        self.initial_base_z = float(b.robot.get_position_orientation()[0][2])
        self.verified_grasps = deepcopy(getattr(b, '_verified_grasps', {}))
        task = b.env.task
        self.aux = [self._remember_attributes(b.env, ['_current_step']),
                    self._remember_attributes(task, ['_reward', '_done', '_success', '_info']),
                    self._remember_attributes(b.robot, ['_ag_release_counter', '_ag_grasp_counter'])]
        for item in task._termination_conditions.values():
            self.aux.append(self._remember_attributes(item, ['_done', '_goal_status']))
        for item in task._reward_functions.values():
            self.aux.append(self._remember_attributes(item, ['_reward', '_info', '_potential']))
        # Rebase motion caches after rollback, while retaining failed-attempt metrics.
        for metric in b.evaluator.metrics:
            self.aux.append(self._remember_attributes(metric, ['state_cache', 'next_state_cache']))
        self.rng = (random.getstate(), np.random.get_state(), b.torch.get_rng_state())
        self.cuda_rng = (b.torch.cuda.current_device(), b.torch.cuda.get_rng_state()) if b.torch.cuda.is_initialized() else None
        b.torch.save({'physical_state': self.state, 'command_q': self.command,
                      'grippers': self.grippers, 'verified_grasps': self.verified_grasps,
                      'rng': self.rng, 'cuda_rng': self.cuda_rng,
                      'logical_state': [values for _, values in self.aux],
                      'particles': [{'system': system.name, 'paths': paths,
                                     'transforms': transforms, 'velocities': velocities}
                                    for _, system, paths, transforms, velocities in self.particles]},
                     self.root / 'checkpoint.pt')
        self.manifest['checkpoint'] = str(self.root / 'checkpoint.pt')
        write_json(self.root / 'trial.json', self.manifest)
        try:
            # Restore instance lookup exactly, including inherited bound methods.
            for obj, name, replacement in [(b.env, 'get_obs', lambda: ({}, {})),
                                            (b, '_step', self.step)]:
                self.stack.callback(_restore_instance_attribute, obj, name,
                                    name in vars(obj), vars(obj).get(name))
                setattr(obj, name, replacement)
            scope = b.og.sim.render_on_step(False)
            scope.__enter__()
            # Pinned OG's context manager lacks finally: resume it normally even
            # when the trial raises, so its original render setting is restored.
            self.stack.callback(scope.__exit__, None, None, None)
            for sensor in b.rig.values():
                texture = sensor.render_product.hydra_texture
                previous = texture.updates_enabled
                self.stack.callback(self._set_camera_updates, texture, previous)
                self._set_camera_updates(texture, self.record_surround)
            video_type = EpisodeVideo if self.record_surround else PickObserverVideo
            self.video = video_type(self.root, fps=1 / float(b.og.sim.get_sim_step_dt()),
                size=b.image_size, views=tuple(self.sensors),
                execution_caption='Native execution; video is observation only.')
            self.video.mark('navigate_to' if self.record_surround else 'manip', {},
                            getattr(b, '_active_request_id', None))
            if not self.record_surround:
                b._prepare_spectator()
            self.capture()
            self.video.append(self.pixels, 0, 'observation', capture_env_step=0)
            if self.video.closed:
                raise RuntimeError('Observer video could not start; no motor steps executed')
            self.series = getattr(b, '_pick_recording_series', None)
            if self.series is None:
                self.series = PickRecordingSeries(b, b.video)
                b._pick_recording_series = self.series
            self.series.begin_trial(self.start_step)
            self.stack.callback(setattr, b, 'video', self.series)
            b.video = self.video
            self.event('open_loop_checkpoint', checkpoint=str(self.root / 'checkpoint.pt'),
                       trial=str(self.root), video_views=list(self.sensors))
            self.timings['checkpoint_and_setup'] = {'seconds': time.perf_counter()-self.started_clock, 'calls': 1}
            self.enter_phase('preflight')
            return self
        except BaseException:
            self.stack.close()
            if hasattr(self, 'video'):
                try:
                    self.video.finish(0)
                except Exception:
                    pass  # Preserve the setup error, before any motor step.
            raise

    def capture(self):
        with self.measure('observer_capture'):
            self._capture()

    def _capture(self):
        # Video never calls the six-view observation pipeline or reads depth/proprio.
        if self.record_surround:
            # The surround rig is mounted in software, so follow the measured
            # base before rendering each navigation capture. No physics step.
            self.b._position_rig()
            self.b._prepare_spectator()
        for _ in range(self.b.video_render_flushes):
            self.b.og.sim.render()
        # render() alone can leave shape-valid, stale annotator buffers. Finish
        # this capture before readback, without querying joints or stepping time.
        with self.measure('observer_annotation_barrier'):
            self.b._dispatch_rgb_annotations()
        pixels = {}
        for name, sensor in self.sensors.items():
            modalities = sensor._modalities
            try:
                sensor._modalities = {'rgb'}
                data, _ = sensor.get_obs()
            finally:
                sensor._modalities = modalities
            pixels[name] = data['rgb'].detach().cpu().numpy()[..., :3].copy()
        self.pixels, self.capture_step = pixels, self.steps

    def step(self, action):
        self.b.deadline.check(changed=True)
        # env.step retains native controller/contact/state/transition/task hooks.
        # Its unused observation fetch is scoped to an empty result above.
        with self.measure('native_environment_step'):
            obs, reward, terminated, truncated, info = self.b.env.step(action)
        self.b.steps += 1
        self.steps += 1
        with self.measure('official_metrics'):
            for metric in self.b.evaluator.metrics:
                metric.step(self.b.env, action, obs, reward, terminated, truncated, info)
        if not self.video.closed:
            interval = int(os.environ.get('MAS_EXECUTION_CAPTURE_INTERVAL', '3'))
            if interval < 1:
                raise ValueError('Execution capture interval must be positive')
            fresh = self.steps % interval == 0
            if fresh:
                self.capture()
            with self.measure('video_submit'):
                self.video.append(self.pixels, self.steps, 'env_step', capture_env_step=self.capture_step,
                                  repeated=not fresh, check_active=lambda: self.b.deadline.check(changed=True))
        if (getattr(self.b, '_primitive_context', None) or {}).get('phase') == 'hold':
            with self.measure('terminal_hold_measurements'):
                self.samples.append(self.hold_row())
        if self.steps % 30 == 0:
            with self.measure('progress_checkpoint'):
                self.progress()

    def hold_row(self):
        b = self.b
        arms = {}
        for arm in b.robot.arm_names:
            held = (getattr(b.robot, '_ag_obj_in_hand', {}) or {}).get(arm)
            arms[arm] = {'actual': b._pose(b._eef(arm)), 'held_object': b._held(arm),
                         'held_object_pose_world': None if held is None else b._pose(held.get_position_orientation())}
        return {'at': now(), 'sim_step': b.steps, 'phase': 'native_manipulation',
                'request_id': getattr(b, '_active_request_id', None), 'arms': arms,
                'base_pose_world': b._pose(b.robot.get_position_orientation()),
                'body_posture': b._body_posture_state(), 'execution_mode': 'open_loop_rollback',
                'sampling_scope': 'terminal hold verification; not control feedback'}

    def stream(self, q, arm):
        return self.stream_paths([('', q)], arm)

    def stream_paths(self, paths, arm, on_segment=None):
        b, robot = self.b, self.b.robot
        groups = ('trunk', 'arm_left', 'arm_right') if arm == 'both' else ('trunk', 'arm_' + arm)
        indices = b.torch.cat([b._indices(group) for group in groups])
        for (_, previous), (_, following) in zip(paths, paths[1:]):
            if float((previous[-1, indices]-following[0, indices]).abs().max()) > .12:
                raise SkillError('discontinuous_plan', 'Planned segment endpoints do not match')
        q = b.torch.cat([paths[0][1][:1], *[path[1:] for _, path in paths]])
        initial = robot.get_joint_positions()
        if float((q[0, indices].to(initial)-initial[indices]).abs().max()) > .12:
            raise SkillError('trajectory_start_changed', 'Measured start differs from planned start', changed=True)
        commands = q[1:].to(b._command_q)
        if not bool(b.torch.isfinite(commands).all()):
            raise SkillError('invalid_trajectory', 'Joint commands must be finite')
        actions = b._hold_action().expand(len(commands), -1).clone()
        for group in groups:
            joint_indices = b._indices(group)
            values = commands[:, joint_indices]
            if getattr(self, 'joint_limit_checks', True):
                values = values.clamp(robot.joint_lower_limits[joint_indices], robot.joint_upper_limits[joint_indices])
            actions[:, robot.controller_action_idx[group]] = values
        # No tracking/pose/contact read, IK, correction or replanning in this loop.
        boundaries, offset = {}, 0
        for name, path in paths:
            boundaries[offset] = name
            offset += len(path)-1
        for index, (command, action) in enumerate(zip(commands, actions)):
            if index in boundaries and on_segment is not None:
                on_segment(boundaries[index])
            b._command_q[indices] = command[indices]
            b._trace_waypoint = index
            self.step(action)
        measured = robot.get_joint_positions()
        final_error = float((q[-1, indices].to(measured)-measured[indices]).abs().max())
        self.event('trajectory_executed', samples=len(commands), execution='open_loop',
                   final_tracking_error_rad=final_error, per_tick_tracking_measured=False)
        if final_error > .35:
            raise SkillError('trajectory_endpoint_error', 'Planned segment endpoint was not reached', changed=True)

    def rollback(self):
        from manipulation_agent.executors.particle_payload import particle_state_unchanged, restore_particle_state
        if self._identities() != self.identities or not particle_state_unchanged(self.particles):
            raise ValueError('Scene topology changed; native snapshot cannot recreate removed USD objects')
        if self.steps:
            self.b.og.sim.load_state(deepcopy(self.state), serialized=False)
            restore_particle_state(self.particles)
        for obj, values in self.aux:
            for name, (present, value) in values.items():
                _restore_instance_attribute(obj, name, present, deepcopy(value))
        self.b._command_q = self.command.clone()
        self.b._gripper_commands = dict(self.grippers)
        self.b._verified_grasps = deepcopy(self.verified_grasps)
        self.b._geometry_pick_planners = {}
        if hasattr(self.b, '_model_pose_planners'):
            self.b._model_pose_planners = {}
        if hasattr(self.b, '_model_posture_planners'):
            self.b._model_posture_planners = {}
        random.setstate(self.rng[0])
        np.random.set_state(self.rng[1])
        self.b.torch.set_rng_state(self.rng[2])
        if self.cuda_rng is not None:
            self.b.torch.cuda.set_rng_state(self.cuda_rng[1], self.cuda_rng[0])
        difference = _snapshot_difference(self.state, self.b.og.sim.dump_state(serialized=False))
        self.manifest.update(rollback='restored_and_verified' if self.steps else 'unchanged_no_physics_steps',
                             max_snapshot_numeric_error=difference)

    def __exit__(self, exc_type, exc, tb):
        self.enter_phase('finalize_recording')
        self.manifest.update(end_step=self.b.steps, control_steps=self.steps,
                             outcome='passed' if self.accepted and exc is None else 'failed',
                             failure_type=None if exc is None else type(exc).__name__,
                             failure=None if exc is None else str(exc))
        try:
            try:
                if not self.video.closed:
                    self.capture()
                    self.video.append(self.pixels, self.steps, 'observation', capture_env_step=self.steps)
                self.manifest['video'] = self.video.finish(self.steps)
            except Exception as recording_error:
                self.manifest['recording_error'] = str(recording_error)
            if self.samples:
                try:
                    self.enter_phase('write_hold_evidence')
                    text = ''.join(json.dumps(row, allow_nan=False)+'\n' for row in self.samples)
                    (self.root / 'hold_samples.jsonl').write_text(text)
                    with (self.b.output / 'waypoint_trace.jsonl').open('a') as stream:
                        stream.write(text)
                except (OSError, ValueError) as evidence_error:
                    self.manifest['evidence_error'] = str(evidence_error)
                    self.accepted = False
                    self.manifest['outcome'] = 'failed'
            if not self.accepted or exc is not None:
                # Save failed outcome/video before restoring the physical world.
                self.persist()
                try:
                    self.enter_phase('rollback')
                    with self.measure('rollback'):
                        self.rollback()
                except Exception as restore_error:
                    self.manifest.update(rollback='failed', rollback_error=str(restore_error))
                    raise SkillError('rollback_failed', str(restore_error), changed=True) from restore_error
        finally:
            self.stack.close()
            # The host's next boundary/step must capture after a possible rewind.
            self.b._recorded_pixels = None
            self.manifest['finished_at'] = now()
            self.manifest.update(component_timings=self.timings,
                phases=self.phases,
                wall_seconds=time.perf_counter()-self.started_clock,
                timing_scope='inclusive component timings; no per-tick disk writes')
            self.persist()
            self.enter_phase('complete')
            self.persist()
            try:
                self.series.add('open_loop_trial', self.start_step, self.b.steps, self.root,
                    self.manifest.get('video', {'status': 'failed', 'every_env_step_recorded': False}))
                self.event('open_loop_trial_end', **self.manifest, trial=str(self.root))
            except (OSError, ValueError) as audit_error:
                self.manifest['audit_error'] = str(audit_error)
                self.persist()
        return False

    def persist(self):
        # NAS audit failure must never prevent rollback or hide its primary error.
        try:
            write_json(self.root / 'trial.json', self.manifest)
        except OSError as audit_error:
            self.manifest['audit_error'] = str(audit_error)
