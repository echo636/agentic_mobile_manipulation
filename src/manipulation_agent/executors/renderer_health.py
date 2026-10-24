"""Read native renderer failures without calling Python from native log threads."""
from pathlib import Path
import re

from ..records import now, write_json


class RendererHealth:
    def __init__(self, output):
        from isaacsim import SimulationApp

        self.output = Path(output)
        self.failure = None
        self.recorded = False
        self.stream = None
        self.pending = b''
        self.closed = False
        self.log_path = self.output.resolve() / 'native_runtime.log'
        # OG clears sys.argv before launching Kit. Use the supported launcher
        # extra_args instead, scoped to this adapter's process. Keep native
        # logging: a Python logger acquires the GIL on every native log thread
        # and can block a native call that waits for that thread while holding it.
        self.launcher_defaults = SimulationApp.DEFAULT_LAUNCHER_CONFIG
        self.previous_args = self.launcher_defaults.get('extra_args', [])
        self.extra_args = [arg for arg in self.previous_args
                           if not arg.startswith('--/log/file=')]
        self.extra_args.append('--/log/file=' + str(self.log_path))
        self.launcher_defaults['extra_args'] = self.extra_args

    def _read_native_log(self):
        if self.closed or self.failure is not None:
            return
        if self.stream is None:
            try:
                self.stream = self.log_path.open('rb')
            except FileNotFoundError:
                return  # Kit has not opened its log during early construction.
        while chunk := self.stream.read(65536):
            lines = (self.pending + chunk).split(b'\n')
            self.pending = lines.pop()
            for line in lines:
                # Allocation errors alone can be recoverable. Match only the
                # renderer's explicit permanent scene failure, not other sources.
                match = re.search(rb'\[(?:Error|Fatal)\] \[omni\.hydra\] (.*)', line)
                if match and b'Rendering will not resume until a new scene is loaded.' in match[1]:
                    self.failure = {'at': now(), 'source': 'omni.hydra',
                        'message': match[1].decode('utf-8', errors='replace')[:2048],
                        'native_log': str(self.log_path),
                        'code': 'native_renderer_scene_unavailable'}
                    return

    def check(self):
        self._read_native_log()
        if self.failure is None:
            return
        if not self.recorded:
            write_json(self.output / 'renderer_failure.json', {
                'status': 'failed', **self.failure,
                'validation_level': 'Native renderer failure; no task score inferred',
                'task_success': None})
            self.recorded = True
        raise RuntimeError('Native renderer cannot produce RGB after scene creation failure; '
                           'see renderer_failure.json')

    def close(self):
        self.closed = True
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.launcher_defaults.get('extra_args') is self.extra_args:
            self.launcher_defaults['extra_args'] = self.previous_args
