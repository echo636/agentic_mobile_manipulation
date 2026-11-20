"""CPU regression checks for state logging, live evidence and optional timings."""
import errno
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from manipulation_agent.append_log import LineWriter
from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend
from manipulation_agent.executors.profiling import action_timing, component
from manipulation_agent.omnigibson_backend import OmniGibsonBackend
from manipulation_agent.video import EpisodeVideo


def test_log_flushes_each_line_without_reopening_and_closes_idempotently(tmp_path):
    path = tmp_path / 'evidence.jsonl'
    original_open = Path.open
    opened = []
    def track_open(target, *args, **kwargs):
        if target == path and args and args[0] == 'a':
            opened.append(target)
        return original_open(target, *args, **kwargs)
    writer = LineWriter(path)
    with patch.object(Path, 'open', track_open):
        for step in range(3):
            writer.write(json.dumps({'step': step}) + '\n')
            # A different reader sees every completed tick before close().
            assert len(path.read_text().splitlines()) == step + 1
    assert len(opened) == 1
    stream = writer.stream
    writer.close()
    writer.close()
    assert stream.closed
    with pytest.raises(ValueError, match='closed'):
        writer.write('{}\n')


def test_timing_keeps_environment_and_metrics_order_and_original_arguments(tmp_path):
    action, obs, info = object(), object(), object()
    returned = (obs, .75, False, True, info)
    order = []
    def step_env(argument):
        assert argument is action
        order.append('env')
        return returned
    def step_metric(*args):
        assert args == (backend.env, action, *returned)
        assert backend.steps == 4
        order.append('metric')
    backend = SimpleNamespace(output=tmp_path, steps=3, env=SimpleNamespace(step=step_env),
                              evaluator=SimpleNamespace(metrics=[SimpleNamespace(step=step_metric)]))
    with action_timing(backend, 'manip'):
        OmniGibsonBackend._step(backend, action)
    assert order == ['env', 'metric']
    row = json.loads((tmp_path / 'performance.jsonl').read_text())
    assert row['status'] == 'passed' and row['primitive'] == 'manip'
    assert row['components']['environment_step']['calls'] == 1
    assert row['components']['evaluator_metrics']['calls'] == 1


def test_native_manip_gets_action_profile_without_extra_steps_or_observations(tmp_path, monkeypatch):
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'geometry_pick_v1')
    backend = SimpleNamespace(output=tmp_path, deadline=EpisodeDeadline())
    with patch('manipulation_agent.pick_engine.VersionedGeometryPick') as engine:
        engine.return_value.execute.return_value = {'reached': True}
        result = OracleWaypointBackend.execute_motor(backend, 'manip', operation='pick')
        engine.return_value.execute.assert_called_once_with(operation='pick')
    assert result == {'reached': True}
    row = json.loads((tmp_path / 'performance.jsonl').read_text())
    assert row['primitive'] == 'manip' and row['status'] == 'passed'


def test_action_profile_reports_component_deltas_and_retains_primary_error(tmp_path):
    backend = SimpleNamespace(output=tmp_path)
    with action_timing(backend, 'first'):
        with component(backend, 'trace'):
            pass
    with pytest.raises(SkillError, match='original'):
        with action_timing(backend, 'second'):
            with component(backend, 'trace'):
                raise SkillError('failed', 'original')
    rows = [json.loads(line) for line in (tmp_path / 'performance.jsonl').read_text().splitlines()]
    assert [r['status'] for r in rows] == ['passed', 'failed']
    assert [r['components']['trace']['calls'] for r in rows] == [1, 1]


def test_video_timeline_late_flush_failure_isolates_encoder_and_keeps_prior_evidence(tmp_path):
    views = ('front', 'back', 'left', 'right', 'spectator')
    video = EpisodeVideo(tmp_path, 30, size=32, views=views)
    pixels = {view: np.zeros((32, 32, 3), dtype=np.uint8) for view in views}
    try:
        video.append(pixels, 1, 'env_step')
        first = (tmp_path / 'video_frames.jsonl').read_text()
        assert json.loads(first)['env_step'] == 1
        original = video._frame_writer.stream
        failed_stream = Mock(wraps=original)
        failed_stream.flush.side_effect = OSError(errno.EIO, 'late NAS failure')
        video._frame_writer.stream = failed_stream
        video.append(pixels, 2, 'env_step', repeated=True)
        assert video.closed and video.manifest['status'] == 'failed'
        assert video.recording_failure['failure_type'] == 'OSError'
        assert video.process.poll() is not None and original.closed
        assert (tmp_path / 'video_frames.jsonl').read_text().startswith(first)
        assert video.finish(2)['status'] == 'failed'
    finally:
        if not video.closed:
            video.finish(1)
