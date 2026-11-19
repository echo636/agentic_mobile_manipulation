"""v12 facility: a bwrap mount failure before any model event is retried; other failures are not."""
from __future__ import annotations

import json

from manipulation_agent.clients import base
from manipulation_agent.clients.types import PreparedProject


def _project(script):
    return PreparedProject(['sh', '-c', script], None, None)


def test_bwrap_start_failure_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'BWRAP_RETRY_WAIT_SECONDS', 0.0)
    flag = tmp_path / 'tried'
    script = (f'if [ -e {flag} ]; then echo \'{{"type":"x"}}\'; exit 0; fi; touch {flag}; '
              "echo \"bwrap: Can't bind mount /oldroot/ on /newroot/: Unable to apply mount flags\" >&2; exit 1")
    result = base.run_process(_project(script), tmp_path, 30)
    assert result.returncode == 0
    assert (tmp_path / 'model_events.jsonl').read_text().strip() == '{"type":"x"}'
    retries = json.loads((tmp_path / 'bwrap_retries.json').read_text())
    assert len(retries) == 1 and (tmp_path / retries[0]['stderr']).read_text().startswith('bwrap:')


def test_other_failures_are_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'BWRAP_RETRY_WAIT_SECONDS', 0.0)
    result = base.run_process(_project('echo boom >&2; exit 3'), tmp_path, 30)
    assert result.returncode == 3 and not (tmp_path / 'bwrap_retries.json').exists()


def test_failure_after_model_events_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'BWRAP_RETRY_WAIT_SECONDS', 0.0)
    script = "echo '{\"type\":\"x\"}'; echo \"bwrap: Can't bind mount\" >&2; exit 1"
    result = base.run_process(_project(script), tmp_path, 30)
    assert result.returncode == 1 and not (tmp_path / 'bwrap_retries.json').exists()


def test_gives_up_after_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'BWRAP_RETRY_WAIT_SECONDS', 0.0)
    result = base.run_process(_project("echo \"bwrap: Can't bind mount\" >&2; exit 1"), tmp_path, 30)
    assert result.returncode == 1
    assert len(json.loads((tmp_path / 'bwrap_retries.json').read_text())) == base.BWRAP_RETRY_ATTEMPTS - 1
