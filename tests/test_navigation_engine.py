"""Integrity, call pinning and scene preservation for the opt-in follower loader."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.navigation_engine import prepare
from manipulation_agent.executors import gt_navigation


def bundle(root, label):
    root.mkdir()
    original = Path(gt_navigation.__file__).read_text()
    payload = (original+'\nBUNDLE_LABEL = '+repr(label)+'\n').encode()
    (root/'gt_navigation.py').write_bytes(payload)
    path = root/'manifest.json'
    path.write_text(json.dumps(dict(format_version=1, root=str(root),
                                   source_sha256=hashlib.sha256(payload).hexdigest())))
    return path


def test_call_pins_classes_and_switch_keeps_scene_and_shared_error_boundary(tmp_path):
    a, b = bundle(tmp_path/'a', 'a'), bundle(tmp_path/'b', 'b')
    backend = SimpleNamespace(output=tmp_path, robot=object(), env=object(), steps=1703)
    before = (backend.robot, backend.env, backend.steps)
    greedy_a, smooth_a, version_a = prepare(backend, a)
    greedy_b, smooth_b, version_b = prepare(backend, b)
    assert version_a != version_b and smooth_a is not smooth_b
    assert (backend.robot, backend.env, backend.steps) == before
    # A caller already holding these types cannot be changed by a later switch.
    assert smooth_a.__mro__[1] is greedy_a and smooth_b.__mro__[1] is greedy_b
    for cls in (smooth_a, smooth_b):
        assert cls.next_pose.__globals__['NavigationError'] is gt_navigation.NavigationError
    rows = [json.loads(l) for l in (tmp_path/'navigation_engine_versions.jsonl').read_text().splitlines()]
    assert [r['version'] for r in rows] == [version_a, version_b]
    assert rows[0]['scene_identity'] == rows[1]['scene_identity']


def test_tampered_bundle_rejected_without_activation_or_scene_change(tmp_path):
    p = bundle(tmp_path/'a', 'a')
    backend = SimpleNamespace(output=tmp_path, robot=object(), env=object(), steps=12)
    (p.parent/'gt_navigation.py').write_text('raise AssertionError("must not execute")')
    with pytest.raises(SkillError, match='hash mismatch') as error:
        prepare(backend, p)
    assert error.value.code == 'navigation_engine_invalid' and backend.steps == 12
    assert not (tmp_path/'navigation_engine_versions.jsonl').exists()
