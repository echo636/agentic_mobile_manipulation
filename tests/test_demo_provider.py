"""The demo launcher must not silently fall back to the desktop login."""
import importlib.util
from pathlib import Path

import pytest


def _module():
    path=Path(__file__).resolve().parents[1]/'scripts/run_demo_trial.py'
    spec=importlib.util.spec_from_file_location('run_demo_trial',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dedicated_provider_guard_checks_endpoint_and_login(tmp_path):
    (tmp_path/'config.toml').write_text(
        '[model_providers.experiment]\nbase_url = "https://api.gpt.ge/v1"\n')
    (tmp_path/'auth.json').write_text(
        '{"auth_mode":"apikey","OPENAI_API_KEY":"test-only"}')
    verify=_module().verify_experiment_provider
    assert verify(tmp_path,'experiment','api.gpt.ge')=='api.gpt.ge'
    with pytest.raises(ValueError):
        verify(tmp_path,'experiment','different.example')
    with pytest.raises(ValueError):
        verify(tmp_path,'desktop','api.gpt.ge')
