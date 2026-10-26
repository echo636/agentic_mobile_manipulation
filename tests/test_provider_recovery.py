"""Retry only an actual terminal model-service failure, never a model decision."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('controller_retry_test',
    Path(__file__).parents[1]/'scripts/run_codex_controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ProviderRecoveryTests(unittest.TestCase):
    def test_route_fork_is_opt_in_bounded_and_does_not_replay_pending_tools(self):
        route = {'type':'turn.failed','error':{'message':'OPENAI_PERMANENT_STICKY_ACCOUNT_UNAVAILABLE'}}
        choose = controller.provider_recovery_mode
        limits = dict(resumes=0, forks=0, max_resumes=0, max_forks=1)
        self.assertEqual(choose([route], **limits), 'fork')
        self.assertIsNone(choose([route], **{**limits, 'max_forks':0}))
        self.assertIsNone(choose([route], **{**limits, 'forks':1}))
        started = {'type':'item.started','item':{'type':'mcp_tool_call','id':'moving'}}
        self.assertIsNone(choose([started, route], **limits))
        done = {**started, 'type':'item.completed'}
        self.assertEqual(choose([started, done, route], **limits), 'fork')
        self.assertIsNone(choose([route, {'type':'turn.completed'}], **limits))
        for message in ['401 Unauthorized OPENAI_PERMANENT_STICKY_ACCOUNT_UNAVAILABLE',
                        '403 Forbidden OPENAI_PERMANENT_STICKY_ACCOUNT_UNAVAILABLE',
                        'PERMANENT_OTHER_ERROR']:
            self.assertIsNone(choose([{'type':'turn.failed','error':{'message':message}}], **limits))

    def test_transient_resume_has_an_independent_budget_after_a_route_fork(self):
        transient = [{'type':'turn.failed','error':{'message':'unexpected status 503 Service Unavailable'}}]
        choose = controller.provider_recovery_mode
        self.assertEqual(choose(transient, resumes=0, forks=1, max_resumes=1, max_forks=1), 'resume')
        self.assertIsNone(choose(transient, resumes=1, forks=1, max_resumes=1, max_forks=1))

    def test_actual_queue_full_and_bad_gateway_are_retryable(self):
        for message in ['unexpected status 503 Service Unavailable: API_KEY_CONCURRENCY_QUEUE_FULL',
                        'unexpected status 502 Bad Gateway', '429 Too Many Requests',
                        'Selected model is at capacity. Please try a different model.',
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
