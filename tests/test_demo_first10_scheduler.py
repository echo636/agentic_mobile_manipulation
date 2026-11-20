"""Guard the provider-serialization gate against the launch/dir-creation race."""
import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_demo_first10.py'
    spec = importlib.util.spec_from_file_location('run_demo_first10', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Running:
    def poll(self):
        return None


def test_new_process_blocks_another_launch_before_it_creates_its_directory(tmp_path):
    active = {1: {'process': Running(), 'trial': tmp_path / 'first'},
              2: {'process': None, 'trial': tmp_path / 'second'}}
    assert _module().has_unfinished_trial(active)


def test_preexisting_incomplete_attempt_blocks_launch_after_supervisor_restart(tmp_path):
    trial = tmp_path / 'first'
    trial.mkdir()
    active = {1: {'process': None, 'trial': trial}}
    assert _module().has_unfinished_trial(active)
    (trial / 'summary.json').write_text('{}')
    assert not _module().has_unfinished_trial(active)
