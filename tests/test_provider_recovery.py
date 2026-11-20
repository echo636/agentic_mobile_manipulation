"""Retry only an actual terminal model-service failure, never a model decision."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('controller_retry_test',
    Path(__file__).parents[1]/'scripts/run_codex_controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ProviderRecoveryTests(unittest.TestCase):
    def test_actual_queue_full_and_bad_gateway_are_retryable(self):
        for message in ['unexpected status 503 Service Unavailable: API_KEY_CONCURRENCY_QUEUE_FULL',
                        'unexpected status 502 Bad Gateway', '429 Too Many Requests',
                        'stream disconnected before completion: Transport error']:
            with self.subTest(message=message):
                self.assertTrue(controller.rate_limit_error([{'type':'turn.failed','error':{'message':message}}]))

    def test_old_transient_error_does_not_mask_terminal_auth_or_model_completion(self):
        old = {'type':'error','message':'Reconnecting... unexpected status 503 Service Unavailable'}
        self.assertFalse(controller.rate_limit_error([old]))
        self.assertFalse(controller.rate_limit_error([old, {'type':'turn.completed'}]))
        self.assertFalse(controller.rate_limit_error([old, {'type':'turn.failed',
            'error':{'message':'unexpected status 401 Unauthorized'}}]))


if __name__ == '__main__':
    unittest.main()
