"""Keep USD guard caller metadata without reading source files on every edit.

The pinned simulator only consumes filename/line/function from this three-frame
stack. Python's extract_stack also refreshes the source cache, issuing thousands
of shared-filesystem stat calls during scene restore. Error tracebacks retain
the original implementation. The USD context, guards and Fabric sync are intact.
"""
import importlib
import sys


class MetadataTraceback:
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def extract_stack(self, f=None, limit=None):
        frame = f if f is not None else sys._getframe(1)
        try:
            if limit != 3 or frame.f_code.co_name != 'editing_usd':
                return self.original.extract_stack(frame, limit=limit)
            result = self.original.StackSummary()
            for _ in range(limit):
                if frame is None:
                    break
                result.append(self.original.FrameSummary(
                    frame.f_code.co_filename, frame.f_lineno,
                    frame.f_code.co_name, lookup_line=False))
                frame = frame.f_back
            result.reverse()
            return result
        finally:
            del frame


def install_usd_caller_metadata():
    module = importlib.import_module('omnigibson.simulator')
    if not isinstance(module.traceback, MetadataTraceback):
        module.traceback = MetadataTraceback(module.traceback)
    return {'status': 'configured', 'scope': 'process_local_simulator_diagnostic_stack',
            'caller_fields': ['filename', 'lineno', 'name'],
            'source_line_lookup': 'lazy_on_error_only',
            'usd_context_guards_and_fabric_sync': 'original_unchanged'}
