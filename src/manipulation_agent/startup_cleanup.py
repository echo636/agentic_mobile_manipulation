"""Close an already-launched simulator when backend construction did not return."""
import sys


def shutdown_partial_simulator():
    """Never import OmniGibson or replace the original constructor exception.

    Pinned og.shutdown() calls exit(0) when app is absent. Only its initialized
    app branch is appropriate here; normal backends still own their close().
    A native crash cannot be caught by this Python cleanup path.
    """
    og = sys.modules.get('omnigibson')
    if og is None or getattr(og, 'app', None) is None:
        return {'status': 'skipped', 'reason': 'no_initialized_omnigibson_app'}
    try:
        og.shutdown()
    except BaseException as exc:
        return {'status': 'failed', 'reason': 'partial_startup_shutdown_failed',
                'error_type': type(exc).__name__, 'error': str(exc)}
    return {'status': 'passed', 'reason': 'partial_startup_app_shutdown_completed'}
