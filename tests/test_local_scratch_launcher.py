"""A run may put disposable USD/compiler files on local disk, keeping evidence on NAS."""
import importlib.util
from pathlib import Path


def launcher():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_exploration_episode.py'
    spec = importlib.util.spec_from_file_location('_scratch_launcher', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest():
    return dict(gpu=1, gpu_uuid='GPU-test', env_sh='/tmp/test-env.sh',
                python='/usr/bin/python3', manip_timing='explicit_fast_v1')


def test_local_scratch_is_explicit_and_is_in_actual_systemd_command():
    m = manifest(); m['local_scratch_root'] = '/tmp/private/instance_309_13'
    argv, unit, env = launcher().command(m, Path('/nas/runs/instance_309_13'), Path('/frozen'))
    assert env['TMPDIR'] == '/tmp/private/instance_309_13'
    assert env['TORCHINDUCTOR_CACHE_DIR'] == '/tmp/private/instance_309_13/torchinductor'
    assert 'TMPDIR=' + env['TMPDIR'] in argv
    assert env['OMNIGIBSON_APPDATA_PATH'] == '/nas/runs/instance_309_13/appdata'
    assert env['MAS_MANIP_TIMING'] == 'explicit_fast_v1'
    assert launcher().runtime_storage_paths(Path('/nas/runs/instance_309_13'), env) == [
        '/nas/runs/instance_309_13', '/nas/runs/instance_309_13/appdata',
        '/tmp/private/instance_309_13']


def test_historical_manifest_keeps_its_original_scratch_layout():
    _, _, env = launcher().command(manifest(), Path('/nas/runs/old_run'), Path('/frozen'))
    assert env['TMPDIR'] == '/nas/runs/old_run/tmp'
    assert env['TORCHINDUCTOR_CACHE_DIR'] == '/nas/runs/old_run/tmp/torchinductor'
    assert launcher().runtime_storage_paths(Path('/nas/runs/old_run'), env)[-1] == env['TMPDIR']


def test_navigation_manifest_reaches_actual_native_systemd_command(tmp_path):
    path = tmp_path/'navigation.json'
    path.write_text('{}')
    m = manifest(); m['navigation_engine_manifest'] = str(path)
    argv, _, env = launcher().command(m, Path('/nas/runs/new_run'), Path('/frozen'))
    assert env['MAS_NAV_ENGINE_MANIFEST'] == str(path)
    assert 'MAS_NAV_ENGINE_MANIFEST=' + str(path) in argv
