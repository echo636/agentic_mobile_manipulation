"""Operator-selected follower versions, pinned once before a navigation call.

No simulator imports, resets, pose setters or changes to already-running followers.
The frozen host must explicitly opt into this loader; old hosts stay unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import types

from .contracts import SkillError
from .records import now
from .executors import gt_navigation as frozen

_lock = threading.RLock()
_modules = {}


def prepare(backend, manifest_path=None):
    """Select before the first command; the caller retains these classes to return."""
    with _lock:
        path = manifest_path or os.environ.get('MAS_NAV_ENGINE_MANIFEST')
        module, version = frozen, hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest()
        if path:
            try:
                manifest = json.loads(Path(path).read_text())
                if set(manifest) != {'format_version', 'root', 'source_sha256'} or manifest['format_version'] != 1:
                    raise ValueError('Unsupported navigation manifest')
                root = Path(manifest['root'])
                if not root.is_absolute():
                    raise ValueError('Absolute navigation bundle root required')
                source = root/'gt_navigation.py'
                payload = source.read_bytes()
                version = hashlib.sha256(payload).hexdigest()
                if version != manifest['source_sha256']:
                    raise ValueError('Navigation source hash mismatch')
                if version not in _modules:
                    name = __package__+'._navigation_'+version
                    module = types.ModuleType(name)
                    module.__file__, module.__package__ = str(source), __package__
                    sys.modules[name] = module
                    try:
                        exec(compile(payload, str(source), 'exec'), module.__dict__)
                        if not issubclass(module.SmoothGridFollower, module.GreedyGridFollower):
                            raise ValueError('Follower interface mismatch')
                        # Preserve the frozen host's exception boundary. Route
                        # planners keep their original NavigationError class.
                        module.NavigationError = frozen.NavigationError
                    except Exception:
                        sys.modules.pop(name, None)
                        raise
                    _modules[version] = module
                module = _modules[version]
            except Exception as exc:
                raise SkillError('navigation_engine_invalid', f'{type(exc).__name__}: {exc}') from exc
        row = dict(at=now(), kind='call_pinned', request_id=getattr(backend, '_active_request_id', None),
                   version=version, manifest=str(path) if path else None,
                   source=str(module.__file__), pid=os.getpid(), sim_step=backend.steps,
                   scene_identity=id(getattr(backend, 'env', None)), robot_identity=id(backend.robot))
        with (backend.output/'navigation_engine_versions.jsonl').open('a') as stream:
            stream.write(json.dumps(row)+'\n')
        return module.GreedyGridFollower, module.SmoothGridFollower, version
