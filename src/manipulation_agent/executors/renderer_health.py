"""Detect an explicitly unrecoverable native renderer before waiting for RGB."""
from pathlib import Path

from ..records import now, write_json


class RendererHealth:
    def __init__(self, output):
        import carb.logging

        self.output = Path(output)
        self.failure = None
        self.recorded = False
        self.logging = carb.logging.acquire_logging()
        self.handle = self.logging.add_logger(self._on_log)

    def _on_log(self, source, level, filename, line, message):
        # Allocation errors alone can be recoverable. This native message says
        # scene creation failed permanently; waiting for fresh annotations can
        # otherwise spin forever inside Replicator's synchronous capture.
        # The logging callback only stores evidence, never calls into Kit or IO.
        if (self.failure is None and source == 'omni.hydra'
                and 'Rendering will not resume until a new scene is loaded.' in message):
            self.failure = {'at': now(), 'source': source, 'message': message[:2048],
                            'code': 'native_renderer_scene_unavailable'}

    def check(self):
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
        if self.handle is not None:
            self.logging.remove_logger(self.handle)
            self.handle = None
