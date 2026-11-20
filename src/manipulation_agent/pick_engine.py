"""Versioned development overlays for geometric pick; CPU-only loader.

Only an operator-controlled manifest chooses code. The model tool schema stays
unchanged. Activation happens between serialized actions and never resets a scene.
"""
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import traceback
import types

from .contracts import SkillError
from .records import now

_lock = threading.RLock()
_modules = {}


def _event(backend, kind, **values):
    with (backend.output / 'pick_engine_versions.jsonl').open('a') as stream:
        stream.write(json.dumps({'at': now(), 'pid': os.getpid(),
            'sim_step': backend.steps, 'kind': kind, **values}, allow_nan=False)+'\n')


def _load(path):
    manifest = json.loads(Path(path).read_text())
    files = manifest['files']
    payloads = {}
    for relative, digest in files.items():
        if relative not in ('grasp_geometry.py', 'executors/oracle_pick.py'):
            raise ValueError('Unexpected pick engine file')
        payload = (Path(manifest['root']) / relative).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError('Pick engine hash mismatch: '+relative)
        payloads[relative] = payload
    if set(payloads) != {'grasp_geometry.py', 'executors/oracle_pick.py'}:
        raise ValueError('Incomplete pick engine bundle')
    version = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if manifest['version'] != version:
        raise ValueError('Pick engine version mismatch')
    if version not in _modules:
        package = __package__+'._pick_'+version
        for suffix in ('', '.executors'):
            module = types.ModuleType(package+suffix)
            module.__path__ = []
            sys.modules[module.__name__] = module
        for name in ('contracts', 'records'):
            sys.modules[package+'.'+name] = sys.modules[__package__+'.'+name]
        for relative, name in [('grasp_geometry.py', '.grasp_geometry'),
                               ('executors/oracle_pick.py', '.executors.oracle_pick')]:
            module = types.ModuleType(package+name)
            module.__file__ = str(Path(manifest['root']) / relative)
            module.__package__ = module.__name__.rpartition('.')[0]
            sys.modules[module.__name__] = module
            exec(compile(payloads[relative], module.__file__, 'exec'), module.__dict__)
        _modules[version] = module.GeometryPick
    return manifest, _modules[version]


def prepare(backend, manifest_path=None):
    """Validate and activate a bundle without stepping physics or building CUDA."""
    with _lock:
        if getattr(backend, '_primitive_context', None):
            raise SkillError('engine_busy', 'Cannot update pick engine during an action')
        path = manifest_path or os.environ.get('MAS_PICK_ENGINE_MANIFEST')
        if not path:
            from .executors.oracle_pick import GeometryPick
            return GeometryPick, None
        manifest, engine = _load(path)
        prior = getattr(backend, '_pick_engine_version', None)
        if prior != manifest['version']:
            old = getattr(backend, '_geometry_pick_planners', {})
            dropped = list(old)
            backend._geometry_pick_planners = {}
            del old
            gc.collect()
            _event(backend, 'activated', previous_version=prior,
                version=manifest['version'], manifest=str(path), files=manifest['files'],
                dropped_planner_arms=dropped, robot_identity=id(backend.robot),
                scene_identity=id(backend.env))
            backend._pick_engine_version = manifest['version']
        return engine, manifest['version']


class VersionedGeometryPick:
    def __init__(self, backend):
        self.backend = backend

    def execute(self, **intent):
        # Harness invokes this on its one simulator thread. Keep the version
        # pinned until this entire call returns, even if the manifest changes.
        with _lock:
            engine, version = prepare(self.backend)
            _event(self.backend, 'call_begin', version=version,
                request_id=getattr(self.backend, '_active_request_id', None), intent=intent)
            try:
                return engine(self.backend).execute(**intent)
            except SkillError:
                raise
            except Exception as exc:
                # A new backend's setup/recording error must be an action error,
                # not escape the bridge and shut down the live simulator.
                _event(self.backend, 'call_error', version=version,
                    failure_type=type(exc).__name__, failure=str(exc),
                    traceback=traceback.format_exc())
                raise SkillError('geometry_pick_runtime_error',
                    f'{type(exc).__name__}: {str(exc)[:1000]}', changed=True) from exc
            finally:
                _event(self.backend, 'call_end', version=version)
