import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.pick_engine import prepare, VersionedGeometryPick


def bundle(tmp_path, label):
    root = tmp_path / label
    (root / 'executors').mkdir(parents=True)
    (root / 'grasp_geometry.py').write_text('LABEL = '+repr(label)+'\n')
    (root / 'executors/oracle_pick.py').write_text(
        'from ..grasp_geometry import LABEL\n'
        'class GeometryPick:\n'
        ' def __init__(self, backend): self.backend = backend\n'
        ' def execute(self, **intent): return LABEL, intent\n')
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
             for name in ('grasp_geometry.py', 'executors/oracle_pick.py')}
    version = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    path = root / 'manifest.json'
    path.write_text(json.dumps(dict(root=str(root), version=version, files=files)))
    return path


def test_switch_preserves_scene_and_steps_but_discards_planner(tmp_path, monkeypatch):
    a, b = bundle(tmp_path, 'a'), bundle(tmp_path, 'b')
    backend = SimpleNamespace(output=tmp_path, steps=91, robot=object(), env=object(),
                              _primitive_context=None, _geometry_pick_planners={'right': object()})
    robot, scene = backend.robot, backend.env
    monkeypatch.setenv('MAS_PICK_ENGINE_MANIFEST', str(a))
    assert VersionedGeometryPick(backend).execute(operation='pick')[0] == 'a'
    backend._geometry_pick_planners['left'] = object()
    monkeypatch.setenv('MAS_PICK_ENGINE_MANIFEST', str(b))
    assert VersionedGeometryPick(backend).execute(operation='pick')[0] == 'b'
    assert backend.robot is robot and backend.env is scene and backend.steps == 91
    assert backend._geometry_pick_planners == {}
    events = [json.loads(line) for line in (tmp_path / 'pick_engine_versions.jsonl').read_text().splitlines()]
    activations = [r for r in events if r['kind'] == 'activated']
    assert len(activations) == 2
    assert activations[1]['previous_version'] == activations[0]['version']
    assert activations[1]['dropped_planner_arms'] == ['left']
    assert [r['kind'] for r in events].count('call_begin') == 2


def test_busy_or_tampered_bundle_cannot_replace_live_planner(tmp_path):
    manifest = bundle(tmp_path, 'busy')
    planner = object()
    backend = SimpleNamespace(output=tmp_path, steps=0, _primitive_context={'phase': 'lift'},
                              _geometry_pick_planners={'right': planner})
    with pytest.raises(SkillError, match='during an action'):
        prepare(backend, manifest)
    backend._primitive_context = None
    (manifest.parent / 'grasp_geometry.py').write_text('LABEL = "tampered"')
    with pytest.raises(ValueError, match='hash mismatch'):
        prepare(backend, manifest)
    assert backend._geometry_pick_planners['right'] is planner


def test_backend_setup_error_is_an_action_failure_and_next_call_still_works(tmp_path, monkeypatch):
    manifest = bundle(tmp_path, 'raises')
    data = json.loads(manifest.read_text())
    payload = manifest.parent/'executors/oracle_pick.py'
    payload.write_text(payload.read_text().replace('return LABEL, intent', "raise RuntimeError('renderer setup failed')"))
    data['files']['executors/oracle_pick.py'] = hashlib.sha256(payload.read_bytes()).hexdigest()
    data['version'] = hashlib.sha256(json.dumps(data['files'], sort_keys=True).encode()).hexdigest()
    manifest.write_text(json.dumps(data))
    backend = SimpleNamespace(output=tmp_path, steps=1727, robot=object(), env=object(), _primitive_context=None)
    monkeypatch.setenv('MAS_PICK_ENGINE_MANIFEST', str(manifest))
    with pytest.raises(SkillError, match='renderer setup failed') as error:
        VersionedGeometryPick(backend).execute(operation='pick')
    assert error.value.code == 'geometry_pick_runtime_error'
    good = bundle(tmp_path, 'after_error')
    monkeypatch.setenv('MAS_PICK_ENGINE_MANIFEST', str(good))
    assert VersionedGeometryPick(backend).execute(operation='pick')[0] == 'after_error'
    assert backend.steps == 1727
    events = [json.loads(line) for line in (tmp_path/'pick_engine_versions.jsonl').read_text().splitlines()]
    assert any(e['kind'] == 'call_error' and e['failure_type'] == 'RuntimeError' for e in events)
    assert events[-1]['kind'] == 'call_end'
