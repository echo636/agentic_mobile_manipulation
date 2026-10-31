"""The readiness launcher must execute its captured package, not live code."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def test_probe_uses_captured_package_in_child_environment(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    task_dir = repo / 'tasks/carry_in_and_put_away_groceries/v2_design'
    monkeypatch.syspath_prepend(str(task_dir))
    spec = importlib.util.spec_from_file_location('readiness_launcher', task_dir / 'run_readiness_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bddl = tmp_path / 'bddl'; bddl.mkdir()
    monkeypatch.setitem(sys.modules, 'run_demo_trial', SimpleNamespace(
        prepare_bddl_overlay=lambda *args: (bddl, None)))
    monkeypatch.setattr(module, 'build', lambda source, output, **kwargs: output.mkdir())
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args, **kwargs: 'GPU-offline\n')
    output = tmp_path / 'probe'
    monkeypatch.setattr(sys, 'argv', ['run_readiness_probe', '--output', str(output),
        '--behavior-root', str(tmp_path / 'behavior'), '--gpu', '3',
        '--appdata', str(tmp_path / 'appdata')])
    real_run = module.subprocess.run

    def launch(command, *, env, **kwargs):
        paths = env['PYTHONPATH'].split(module.os.pathsep)
        captured = output / 'source_snapshot/src'
        assert str(captured) in paths
        assert str(repo / 'src') not in paths
        assert (captured / 'manipulation_agent/executors/carry.py').read_bytes() == (
            repo / 'src/manipulation_agent/executors/carry.py').read_bytes()
        imported = real_run([sys.executable, '-c',
            'import manipulation_agent.executors.carry as c; print(c.__file__)'],
            env=env, cwd=tmp_path, capture_output=True, text=True, check=True)
        assert Path(imported.stdout.strip()).is_relative_to(captured)
        run = output / 'run'; run.mkdir()
        (run / 'result.json').write_text(json.dumps({'status': 'passed'}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, 'run', launch)
    assert module.main() == 0
    manifest = json.loads((output / 'probe_manifest.json').read_text())
    assert 'src/manipulation_agent/executors/carry.py' in manifest['source_sha256']
