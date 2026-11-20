"""CPU state-machine/real-encoder tests. No simulator is imported or started."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

torch = pytest.importorskip('torch')

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.oracle_open_loop import (OpenLoopPickTrial, PickObserverVideo,
                                                          _snapshot_difference)
from manipulation_agent.executors.oracle_pick import GeometryPick
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.pick_engine import prepare


class Camera:
    def __init__(self, name, reads):
        self.name, self.reads = name, reads
        self._modalities = {'rgb', 'depth_linear'}
        self.render_product = SimpleNamespace(hydra_texture=SimpleNamespace(updates_enabled=True))
    def get_obs(self):
        assert self._modalities == {'rgb'}
        self.reads.append(self.name)
        return {'rgb': torch.full((32, 32, 4), 25+len(self.reads) % 200, dtype=torch.uint8)}, {}


def backend(tmp_path):
    reads, events, applications = [], [], []
    bottle = SimpleNamespace(name='bottle', position=torch.tensor([0., 0., 1.]))
    bottle.get_position_orientation = lambda: (bottle.position.clone(), torch.tensor([0., 0., 0., 1.]))
    robot = SimpleNamespace(q=torch.zeros(6), _ag_obj_in_hand={'left': None, 'right': None},
        _ag_release_counter={'left': None, 'right': None}, _ag_grasp_counter={'left': None, 'right': None},
        arm_names=['left', 'right'], trunk_control_idx=torch.arange(2),
        arm_control_idx={'right': torch.arange(2, 4), 'left': torch.arange(4, 6)},
        controller_action_idx={'trunk': torch.arange(2), 'arm_right': torch.arange(2, 4)},
        joint_lower_limits=-torch.ones(6), joint_upper_limits=torch.ones(6))
    robot.get_joint_positions = Mock(side_effect=lambda: robot.q.clone())
    robot.get_position_orientation = lambda: (torch.zeros(3), torch.tensor([0., 0., 0., 1.]))
    scene = SimpleNamespace(objects=[robot, bottle], active_systems={})
    metric = SimpleNamespace(count=0, state_cache={'position': torch.tensor([0.])})
    metric.step = lambda *args: setattr(metric, 'count', metric.count+1)
    task = SimpleNamespace(_reward=0., _done=False, _success=False, _info={},
                           _termination_conditions={'condition': SimpleNamespace(_done=False, _goal_status={})},
                           _reward_functions={'reward': SimpleNamespace(_reward=0., _info={}, _potential=1.)})

    class Sim:
        scenes = [scene]
        renders = 0
        loads = 0
        render_enabled = True
        controller = torch.zeros(6)
        velocity = torch.zeros(6)
        editing = False
        edits = 0
        @contextmanager
        def editing_usd(self):
            assert not self.editing
            self.editing = True
            self.edits += 1
            try: yield
            finally: self.editing = False
        def dump_state(self, serialized):
            return {'q': robot.q, 'bottle_position': bottle.position, 'velocity': self.velocity,
                    'controller': self.controller, 'held': robot._ag_obj_in_hand['right'] is not None}
        def load_state(self, state, serialized):
            self.loads += 1
            robot.q = state['q'].clone(); bottle.position = state['bottle_position'].clone()
            self.velocity = state['velocity'].clone(); self.controller = state['controller'].clone()
            robot._ag_obj_in_hand['right'] = bottle if state['held'] else None
        def render(self): self.renders += 1
        def get_sim_step_dt(self): return 1/30
        @contextmanager
        def render_on_step(self, enabled):
            old = self.render_enabled
            self.render_enabled = enabled
            yield
            self.render_enabled = old  # Deliberately matches pinned OG's lack of finally.

    sim = Sim()
    class Env:
        _automatic_reset = False
        _current_step = 0
        ignores_commands = False
        observations = 0
        def get_obs(self):
            self.observations += 1
            raise AssertionError('Full environment observations must not run during execution')
        def step(self, action):
            assert not sim.render_enabled
            applications.append(action.clone())
            if not self.ignores_commands: robot.q = action.clone()
            sim.controller = action.clone(); sim.velocity += .1
            bottle.position += .01
            robot._ag_obj_in_hand['right'] = bottle
            robot._ag_release_counter['right'] = 2
            task._success = True
            task._reward_functions['reward']._potential += 1
            self._current_step += 1
            random.random(); np.random.rand(); torch.rand(1)
            obs, _ = self.get_obs()
            return obs, 0., False, False, {}

    env = Env(); env.task = task; env.scene = scene
    b = SimpleNamespace(output=tmp_path, torch=torch, robot=robot, env=env, og=SimpleNamespace(sim=sim),
        steps=10, _command_q=robot.q.clone(), _gripper_commands={'left': 1., 'right': 1.},
        _verified_grasps={}, _primitive_context=None, deadline=EpisodeDeadline(),
        evaluator=SimpleNamespace(metrics=[metric]), _active_request_id='fixture', image_size=32,
        rig={name: Camera(name, reads) for name in ('front', 'back', 'left', 'right')},
        spectator=Camera('spectator', reads),
        extra_rgb_sensors={name: Camera(name, reads) for name in ('left_wrist', 'right_wrist')},
        video_render_flushes=2, video=None, _trace_waypoint=None)
    b._step = Mock(side_effect=AssertionError('Legacy observation/recording step must be bypassed'))
    b._prepare_spectator = Mock()
    b._position_rig = Mock()
    b._dispatch_rgb_annotations = Mock()
    b._hold_action = lambda: b._command_q.clone()
    b._indices = lambda group: robot.trunk_control_idx if group == 'trunk' else robot.arm_control_idx[group[4:]]
    b._held = lambda arm: None if robot._ag_obj_in_hand[arm] is None else bottle.name
    b._pose = lambda pose: {'position_m': pose[0].tolist(), 'quaternion_xyzw': pose[1].tolist()}
    b._eef = lambda arm: robot.get_position_orientation()
    b._body_posture_state = lambda: {'upper_body_tilt_deg': 0., 'upper_body_height_above_base_m': 1.14}
    class Texture:
        _updates = True
        @property
        def updates_enabled(self): return self._updates
        @updates_enabled.setter
        def updates_enabled(self, value):
            assert sim.editing, 'Hydra viewport changes must occur inside OG editing_usd'
            self._updates = value
    for sensor in b.rig.values():
        sensor.render_product.hydra_texture = Texture()
    event = lambda kind, **data: events.append({'kind': kind, **data})
    return b, event, reads, applications, events


def test_open_loop_runs_every_planned_command_before_endpoint_failure_and_rolls_back(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    b.env.ignores_commands = True
    before = deepcopy(b.og.sim.dump_state(False))
    original_step = b._step
    rng = (random.getstate(), np.random.get_state(), torch.get_rng_state())
    q = torch.arange(7).reshape(-1, 1).repeat(1, 6)*.1
    with pytest.raises(SkillError, match='endpoint'):
        with OpenLoopPickTrial(b, event) as trial:
            trial.stream(q, 'right')
    assert len(commands) == 6  # Tracking error exceeds the old guard partway through.
    assert torch.equal(torch.stack(commands)[:, :4], q[1:, :4])
    assert robot_read_count(b) == 2  # Only segment start and endpoint, no per-tick feedback.
    assert b.steps == 16 and b.evaluator.metrics[0].count == 6
    assert b.og.sim.loads == 1 and b.env._current_step == 0
    assert _snapshot_difference(before, b.og.sim.dump_state(False)) == 0
    assert b._step is original_step and 'get_obs' not in vars(b.env)
    assert b.og.sim.render_enabled and b.video is b._pick_recording_series
    assert all(sensor.render_product.hydra_texture.updates_enabled for sensor in b.rig.values())
    assert b.robot._ag_release_counter['right'] is None
    assert not b.env.task._success and b.env.task._reward_functions['reward']._potential == 1.
    assert random.getstate() == rng[0] and np.array_equal(np.random.get_state()[1], rng[1][1])
    assert torch.equal(torch.get_rng_state(), rng[2])
    assert set(reads) == {'spectator', 'left_wrist', 'right_wrist'}
    assert b.env.observations == 0
    assert b.og.sim.edits == 8  # Suspend and restore all four render products in USD scopes.
    manifest = json.loads((trial.root/'trial.json').read_text())
    assert manifest['rollback'] == 'restored_and_verified' and manifest['outcome'] == 'failed'
    assert manifest['video']['status'] == 'passed' and manifest['video']['every_env_step_recorded']
    assert (trial.root/'checkpoint.pt').exists() and (trial.root/'episode.mp4').stat().st_size > 0


def robot_read_count(b): return b.robot.get_joint_positions.call_count


@pytest.mark.parametrize('rejected', [False, True])
def test_native_transport_records_terminal_request_id_without_masking_rejection(tmp_path, rejected):
    from manipulation_agent.executors.native_transport import run

    b, _, reads, commands, _ = backend(tmp_path)
    bottle = b.env.scene.objects[1]
    b.robot._ag_obj_in_hand['right'] = bottle
    b._base_target = None
    b._carry_contents = {}
    b._resolve_object = lambda name: bottle
    b._grasp_state = lambda arm: {'target_link': 'base_link'}
    b._resolve_link = lambda obj, name: obj
    b._retention = lambda arm, obj, link: {'retained': True}
    failure = SkillError('native_transport_invalid_start', 'Retract before transport')
    b.execute_visual = Mock(side_effect=failure if rejected else None, return_value={'steps': 0})

    if rejected:
        with pytest.raises(SkillError) as error:
            run(b, {'image_ref': 'fixture', 'point': [.5, .5]})
        assert error.value is failure
    else:
        result = run(b, {'image_ref': 'fixture', 'point': [.5, .5]})
        assert result['verified'] is True
        assert result['object_pose_projection'] is False

    records = [json.loads(line) for line in (tmp_path/'native_transport.jsonl').read_text().splitlines()]
    assert [row['kind'] for row in records] == ['open_loop_checkpoint', 'open_loop_trial_end']
    assert all(row['request_id'] == 'fixture' for row in records)
    terminal = records[-1]
    assert terminal['outcome'] == ('failed' if rejected else 'passed')
    assert terminal['control_steps'] == 0 and not commands
    assert terminal['rollback'] == ('unchanged_no_physics_steps' if rejected else 'not_needed')
    assert terminal['failure'] == (str(failure) if rejected else None)
    assert b._native_transport_active is False and b._base_target is None
    views = [*b.rig, *b.extra_rgb_sensors, 'spectator']
    assert records[0]['video_views'] == terminal['video_views'] == views
    assert terminal['video']['views'] == views and set(reads) == set(views)
    assert b._position_rig.call_count == 2  # Initial and terminal captures.
    assert all(sensor.render_product.hydra_texture.updates_enabled for sensor in b.rig.values())


def annotation_dispatch_fixture(b, monkeypatch, complete):
    """Only the pinned completion API publishes a camera buffer in this fixture."""
    def dispatch(**kwargs):
        assert b.og.sim.editing
        assert kwargs == dict(delta_time=0.0, pause_timeline=False,
                              wait_for_render=True, rt_subframes=1)
        complete()
    package = ModuleType('omnigibson')
    package.__path__ = []
    lazy = ModuleType('omnigibson.lazy')
    lazy.omni = SimpleNamespace(replicator=SimpleNamespace(
        core=SimpleNamespace(orchestrator=SimpleNamespace(step=dispatch))))
    package.lazy = lazy
    monkeypatch.setitem(sys.modules, 'omnigibson', package)
    monkeypatch.setitem(sys.modules, 'omnigibson.lazy', lazy)
    b._dispatch_rgb_annotations = lambda: RGBBackend._dispatch_rgb_annotations(b)


@pytest.mark.parametrize('record_surround', [False, True])
def test_observer_records_current_motion_when_render_alone_leaves_stale_buffers(
        tmp_path, monkeypatch, record_surround):
    b, event, reads, commands, events = backend(tmp_path)
    captures = []
    sensors = [*b.rig.values(), b.spectator, *b.extra_rgb_sensors.values()]
    mounted_step = None
    def position_rig():
        nonlocal mounted_step
        assert not b.og.sim.editing
        mounted_step = b.env._current_step
    b._position_rig.side_effect = position_rig
    for sensor in sensors:
        sensor.pixels = torch.zeros((32, 32, 4), dtype=torch.uint8)
        def get_obs(sensor=sensor):
            assert sensor._modalities == {'rgb'} and not b.og.sim.editing
            reads.append(sensor.name)
            return {'rgb': sensor.pixels.clone()}, {}
        sensor.get_obs = get_obs
    def complete():
        assert all(sensor.render_product.hydra_texture.updates_enabled == record_surround
                   for sensor in b.rig.values())
        if record_surround:
            assert mounted_step == b.env._current_step
        captures.append(b.env._current_step)
        for sensor in sensors:
            sensor.pixels.fill_(20*b.env._current_step)
    annotation_dispatch_fixture(b, monkeypatch, complete)
    q = torch.arange(7).reshape(-1, 1).repeat(1, 6)*.1
    with OpenLoopPickTrial(b, event, record_surround=record_surround) as trial:
        trial.stream(q, 'right')
        trial.accepted = True
    rows = [json.loads(line) for line in (trial.root/'video_frames.jsonl').read_text().splitlines()]
    views = (*b.rig, *b.extra_rgb_sensors, 'spectator') if record_surround else PickObserverVideo.VIEWS
    assert trial.video.views == views and trial.manifest['video_views'] == list(views)
    for view in views:
        hashes = [row['rgb_sha256'][view] for row in rows]
        assert hashes[0] != hashes[3] != hashes[6]
        assert hashes[1:3] == [hashes[0]]*2  # Explicit stride repeats remain honest.
        assert hashes[4:6] == [hashes[3]]*2
    assert captures == [0, 3, 6, 6]
    assert len(commands) == 6 and b.steps == 16 and b.env._current_step == 6
    assert robot_read_count(b) == 2 and b.env.observations == 0
    assert set(reads) == set(views)
    assert b._position_rig.call_count == (len(captures) if record_surround else 0)


@pytest.mark.parametrize('fail_capture', [1, 2])
@pytest.mark.parametrize('record_surround', [False, True])
def test_annotation_failure_restores_render_scopes_and_failed_motion(
        tmp_path, monkeypatch, fail_capture, record_surround):
    b, event, reads, commands, events = backend(tmp_path)
    initial_updates = {name: name != 'front' for name in b.rig}
    with b.og.sim.editing_usd():
        for name, sensor in b.rig.items():
            sensor.render_product.hydra_texture.updates_enabled = initial_updates[name]
    original_step = b._step
    calls = 0
    def complete():
        nonlocal calls
        calls += 1
        if calls == fail_capture:
            raise RuntimeError('camera completion failed')
    annotation_dispatch_fixture(b, monkeypatch, complete)
    with pytest.raises(RuntimeError, match='camera completion failed'):
        with OpenLoopPickTrial(b, event, record_surround=record_surround):
            for _ in range(3): b._step(torch.ones(6)*.1)
    assert b._step is original_step and 'get_obs' not in vars(b.env)
    assert b.og.sim.render_enabled and not b.og.sim.editing
    assert {name: sensor.render_product.hydra_texture.updates_enabled
            for name, sensor in b.rig.items()} == initial_updates
    assert all(sensor._modalities == {'rgb', 'depth_linear'}
               for sensor in [*b.rig.values(), b.spectator, *b.extra_rgb_sensors.values()])
    assert b.env._current_step == 0 and torch.count_nonzero(b.robot.q) == 0
    assert len(commands) == (0 if fail_capture == 1 else 3)


def test_phase_timing_progress_accounts_for_each_executed_step(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    with OpenLoopPickTrial(b, event) as trial:
        trial.enter_phase('lift')
        for _ in range(2): b._step(torch.ones(6)*.1)
        trial.enter_phase('hold')
        b._primitive_context = {'phase': 'hold'}
        for _ in range(3): b._step(torch.ones(6)*.1)
        trial.accepted = True
    phases = {row['phase']: row for row in trial.manifest['phases']}
    assert phases['lift']['control_steps'] == 2 and phases['hold']['control_steps'] == 3
    assert phases['lift']['components']['native_environment_step']['calls'] == 2
    assert phases['hold']['components']['native_environment_step']['calls'] == 3
    assert all(row['seconds'] >= 0 for row in phases.values())
    progress = json.loads((trial.root/'progress.json').read_text())
    assert progress['phase'] == 'complete' and progress['control_steps'] == 5
    assert trial.manifest['component_timings']['native_environment_step']['calls'] == 5


def test_success_commits_and_records_hold_evidence_without_six_view_reads(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    with OpenLoopPickTrial(b, event) as trial:
        b._primitive_context = {'phase': 'hold'}
        for _ in range(3): b._step(torch.ones(6)*.1)
        trial.accepted = True
    assert b.og.sim.loads == 0 and b.steps == 13
    assert b.env._current_step == 3
    rows = [json.loads(line) for line in (tmp_path/'waypoint_trace.jsonl').read_text().splitlines()]
    assert [r['sim_step'] for r in rows] == [11, 12, 13]
    assert all(r['arms']['right']['held_object'] == 'bottle' for r in rows)
    assert trial.manifest['outcome'] == 'passed' and trial.manifest['rollback'] == 'not_needed'
    assert trial.video.views == ('spectator', 'left_wrist', 'right_wrist')
    assert b.env.observations == 0
    assert all(sensor._modalities == {'rgb', 'depth_linear'} for sensor in trial.sensors.values())


def test_returned_failed_postcondition_rolls_back_including_native_grasp(tmp_path, monkeypatch):
    b, event, reads, commands, events = backend(tmp_path)
    def failed_pick(self, *args):
        self.b._step(torch.ones(6)*.1)
        return {'reached': True, 'verified': False, 'reason': 'no_native_retention'}
    monkeypatch.setattr(GeometryPick, '_execute_pick', failed_pick)
    result = GeometryPick(b).execute('pick', 'bottle')
    assert result['verified'] is False and result['execution']['rollback'] == 'restored_and_verified'
    assert b.robot._ag_obj_in_hand['right'] is None and torch.count_nonzero(b.robot.q) == 0
    assert b.steps == 11


def test_planning_failure_does_not_reload_unchanged_physics(tmp_path, monkeypatch):
    b, event, reads, commands, events = backend(tmp_path)
    monkeypatch.setattr(GeometryPick, '_execute_pick', lambda *args: {'verified': False})
    result = GeometryPick(b).execute('pick', 'bottle')
    assert result['execution']['rollback'] == 'unchanged_no_physics_steps'
    assert b.og.sim.loads == 0 and not commands


def test_topology_change_never_claims_successful_restore(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    with pytest.raises(SkillError, match='topology'):
        with OpenLoopPickTrial(b, event) as trial:
            b._step(torch.zeros(6))
            b.env.scene.objects.pop()
    assert trial.manifest['rollback'] == 'failed'
    assert json.loads((trial.root/'trial.json').read_text())['rollback'] == 'failed'
    assert b.og.sim.loads == 0 and b.og.sim.render_enabled


def test_cancel_restores_world_but_keeps_recorded_attempt(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    with pytest.raises(SkillError, match='closure requested'):
        with OpenLoopPickTrial(b, event) as trial:
            b._step(torch.ones(6)*.1)
            b.deadline.request_stop()
            b._step(torch.ones(6)*.2)
    assert len(commands) == 1 and b.steps == 11
    assert trial.manifest['rollback'] == 'restored_and_verified'
    assert (trial.root/'episode.mp4').exists()


def test_preplanned_segments_all_execute_before_one_endpoint_check(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    b.env.ignores_commands = True
    first = torch.arange(4).reshape(-1, 1).repeat(1, 6)*.2
    second = torch.arange(3, 6).reshape(-1, 1).repeat(1, 6)*.2
    phases = []
    with pytest.raises(SkillError, match='endpoint'):
        with OpenLoopPickTrial(b, event) as trial:
            trial.stream_paths([('pregrasp', first), ('approach', second)], 'right', phases.append)
    assert len(commands) == 5 and robot_read_count(b) == 2
    assert phases == ['pregrasp', 'approach']
    assert trial.manifest['rollback'] == 'restored_and_verified'


def test_old_prefix_and_new_trials_and_continuation_remain_complete_segments(tmp_path):
    b, event, reads, commands, events = backend(tmp_path)
    views = (*b.rig, *b.extra_rgb_sensors, 'spectator')
    pixels = {name: np.zeros((32, 32, 3), dtype=np.uint8) for name in views}
    old = PickObserverVideo(tmp_path, fps=30, size=32, views=views)
    for step in range(1, 11): old.append(pixels, step, 'env_step')
    b.video = old
    with OpenLoopPickTrial(b, event) as first:
        b._step(torch.ones(6)*.1)
        first.accepted = True
    # Host recording and navigation must retain surround views after Manip.
    b.video.mark('wait', {}, 'outside')
    b.steps += 1
    b.video.append(pixels, b.steps, 'env_step')
    with OpenLoopPickTrial(b, event, record_surround=True) as navigation:
        for _ in range(3): b._step(torch.ones(6)*.15)
        navigation.accepted = True
    with OpenLoopPickTrial(b, event) as second:
        b._step(torch.ones(6)*.2)  # returned failure -> rollback, footage preserved
    b.video.append(pixels, b.steps, 'observation')
    result = b.video.finish(b.steps)
    assert old.closed and old.manifest['final_env_step'] == 10
    assert result['status'] == 'passed' and result['every_env_step_recorded']
    assert [(r['start_step'], r['end_step']) for r in result['segments']] == [
        (0, 10), (10, 11), (11, 12), (12, 15), (15, 16), (16, 16)]
    assert [r['video']['views'] for r in result['segments']] == [
        list(views), list(PickObserverVideo.VIEWS), list(views), list(views),
        list(PickObserverVideo.VIEWS), list(views)]
    assert result['views_after_transition'] == list(views)
    for segment in result['segments']:
        rows = [json.loads(line) for line in
                (Path(segment['root'])/'video_frames.jsonl').read_text().splitlines()]
        assert all(set(row['rgb_sha256']) == set(segment['video']['views']) for row in rows)


def test_audit_write_fault_cannot_prevent_rollback_or_hide_motor_failure(tmp_path, monkeypatch):
    b, event, reads, commands, events = backend(tmp_path)
    with pytest.raises(SkillError, match='original motor failure'):
        with OpenLoopPickTrial(b, event) as trial:
            b._step(torch.ones(6)*.1)
            def cannot_write(*args): raise OSError('NAS unavailable')
            monkeypatch.setattr('manipulation_agent.executors.oracle_open_loop.write_json', cannot_write)
            raise SkillError('motor_failed', 'original motor failure', changed=True)
    assert trial.manifest['rollback'] == 'restored_and_verified'
    assert trial.manifest['audit_error'] == 'NAS unavailable'
    assert b.og.sim.loads == 1 and b.og.sim.render_enabled


def test_retained_scene_exports_wait_tail_at_observation_and_keeps_recording(tmp_path):
    from manipulation_agent.executors.oracle_open_loop import PickRecordingSeries

    b, _, _, commands, _ = backend(tmp_path)
    b.steps = 0
    series = PickRecordingSeries(b, None)
    views = (*b.rig, *b.extra_rgb_sensors, 'spectator')
    pixels = {name: np.zeros((32, 32, 3), dtype=np.uint8) for name in views}
    for start, end in [(0, 90), (90, 150)]:
        series.mark('wait', {'steps': end-start}, f'wait-{end}')
        for step in range(start+1, end+1):
            series.append(pixels, step, 'env_step')
        series.append(pixels, end, 'observation_boundary')
        assert series.active is None and not series.closed
        segment = series.manifest['segments'][-1]
        assert (segment['start_step'], segment['end_step']) == (start, end)
        assert segment['video']['status'] == 'passed'
        # Actually decode every frame, including the encoder's final GOP.
        raw = subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
            '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames',
            '-of', 'json', str(Path(segment['root'])/'episode.mp4')], timeout=30)
        assert int(json.loads(raw)['streams'][0]['nb_read_frames']) == end-start+1
    assert b.steps == 0 and not commands and b.og.sim.renders == 0
    assert b.env._current_step == 0
    assert json.loads((tmp_path/'recording_segments.json').read_text())['status'] == 'running'


def test_observation_checkpoint_fault_stays_visible_without_failing_action(tmp_path):
    from manipulation_agent.executors.oracle_open_loop import PickRecordingSeries

    b, _, _, commands, _ = backend(tmp_path)
    b.steps = 0
    series = PickRecordingSeries(b, None)
    series.active = SimpleNamespace(finish=Mock(side_effect=OSError('encoder storage unavailable')))
    series.offset = 0
    series.checkpoint(90)
    assert series.active is None and not series.closed
    assert series.manifest['checkpoint_failure']['env_step'] == 90
    assert not commands and b.og.sim.renders == 0
    result = series.finish(90)
    assert result['status'] == 'failed'


def test_published_bundle_embeds_support_for_the_existing_two_file_loader(tmp_path):
    project = Path(__file__).resolve().parents[1]
    manifest = tmp_path/'active.json'
    subprocess.run([sys.executable, str(project/'scripts/publish_pick_engine.py'),
                    '--source', str(project), '--output', str(tmp_path/'engines'),
                    '--activate', str(manifest)], check=True, capture_output=True)
    data = json.loads(manifest.read_text())
    assert set(data['files']) == {'grasp_geometry.py', 'executors/oracle_pick.py'}
    assert set(data['embedded_sources']) == {'executors/oracle_open_loop.py'}
    payload = (Path(data['root'])/'executors/oracle_pick.py').read_text()
    assert 'import OpenLoopPickTrial' not in payload
    assert 'class OpenLoopPickTrial:' in payload
    b, event, reads, commands, events = backend(tmp_path/'fixture')
    b.output.mkdir()
    engine, _ = prepare(b, manifest)
    # Exercise the exact frozen execution class, including its embedded transaction.
    engine._execute_pick = lambda self, *args: {'verified': False}
    result = engine(b).execute('pick', 'bottle')
    assert result['execution']['rollback'] == 'unchanged_no_physics_steps'


def test_snapshot_verification_rejects_corrupted_velocity_and_grasp_state():
    with pytest.raises(ValueError):
        _snapshot_difference({'velocity': torch.zeros(2)}, {'velocity': torch.ones(2)})
    with pytest.raises(ValueError):
        _snapshot_difference({'grasp': 'bottle'}, {'grasp': None})
