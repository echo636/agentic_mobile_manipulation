"""The optional finish guard defers early finishes in the MCP proxy and is inert when off."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manipulation_agent.tools import tool_specs
from manipulation_agent.tools.finish_guard import ATTEMPTS, MAX_DEFERRALS, THRESHOLDS, FinishGuard


def budget(remaining, limit=400):
    return {'scope': 'agent_steps', 'steps_used': limit - remaining, 'steps_remaining': remaining, 'max_steps': limit}


def completed(primitive, remaining):
    return {'ok': True, 'effect': {'primitive': primitive, 'status': 'completed'}, 'execution_budget': budget(remaining)}


def failed(remaining, code='pre_condition_error'):
    return {'ok': False, 'error': {'code': code, 'message': 'x'}, 'execution_budget': budget(remaining)}


ABORT = {'outcome': 'aborted', 'reason': 'Only wiping done; meal preparation and storage were not completed.'}


class FinishGuardRuleTests(unittest.TestCase):
    def guard_after_progress(self):
        guard = FinishGuard()
        guard.observe({'ok': True, 'execution_budget': budget(400)}, 'initialize', {})
        guard.observe(completed('grasp', 399), 'act', {'primitive': 'grasp'})
        guard.observe(completed('wipe', 398), 'act', {'primitive': 'wipe', 'intent': 'Wipe the dining table.'})
        return guard

    def test_no_budget_seen_never_defers(self):
        self.assertIsNone(FinishGuard().deferral(ABORT))

    def test_early_abort_is_deferred_with_own_receipts_only(self):
        guard = self.guard_after_progress()
        result = guard.deferral(ABORT)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'finish_deferred_budget_remaining')
        self.assertIs(result['error']['world_may_have_changed'], False)
        message = result['error']['message']
        self.assertIn('398 act/look steps remain', message)
        self.assertIn('grasp x1, wipe x1', message)
        self.assertIn('- step 1: grasp\n', message)
        self.assertIn('- step 2: wipe: Wipe the dining table.', message)
        self.assertEqual(result['finish_guard'], {'deferred': {'stop': 1}})

    def test_repeat_stop_needs_failed_attempts_in_a_row(self):
        guard = self.guard_after_progress()
        self.assertIsNotNone(guard.deferral(ABORT))
        guard.observe(failed(397), 'act', {})
        guard.observe(failed(396), 'act', {})
        again = guard.deferral({'outcome': 'blocked', 'reason': 'still stuck'})
        self.assertIn('2 failed act/look attempt(s) in a row', again['error']['message'])
        for remaining in range(395, 395 - ATTEMPTS, -1):
            guard.observe(failed(remaining), 'act', {})
        self.assertIsNone(guard.deferral(ABORT))

    def test_completed_act_restarts_the_failure_count(self):
        guard = self.guard_after_progress()
        guard.deferral(ABORT)
        guard.observe(failed(397), 'act', {})
        guard.observe(failed(396), 'act', {})
        guard.observe(completed('open', 395), 'act', {})
        guard.observe(failed(394), 'act', {})
        self.assertIsNotNone(guard.deferral(ABORT))

    def test_rejection_that_used_no_step_is_not_an_attempt(self):
        guard = self.guard_after_progress()
        guard.deferral(ABORT)
        for _ in range(ATTEMPTS + 2):
            guard.observe(failed(398, 'stale_revision'), 'act', {})
        self.assertEqual(guard.failures_in_row, 0)
        self.assertIsNotNone(guard.deferral(ABORT))

    def test_stop_deferrals_are_capped(self):
        guard = self.guard_after_progress()
        for _ in range(MAX_DEFERRALS['stop']):
            self.assertIsNotNone(guard.deferral(ABORT))
        self.assertIsNone(guard.deferral(ABORT))

    def test_low_budget_accepts_immediately(self):
        guard = FinishGuard()
        guard.observe(completed('grasp', THRESHOLDS['aborted'] - 1), 'act', {})
        self.assertIsNone(guard.deferral(ABORT))
        guard.observe(completed('grasp', THRESHOLDS['achieved'] - 1), 'act', {})
        self.assertIsNone(guard.deferral({'outcome': 'achieved', 'reason': 'done'}))

    def test_achieved_is_deferred_once_then_needs_one_fresh_call(self):
        guard = self.guard_after_progress()
        done = {'outcome': 'achieved', 'reason': 'done'}
        self.assertIn('Verify the instruction against fresh RGB', guard.deferral(done)['error']['message'])
        self.assertIn('no look or act followed', guard.deferral(done)['error']['message'])
        self.assertIsNone(guard.deferral(done))  # capped at two
        guard = self.guard_after_progress()
        guard.deferral(done)
        guard.observe({'ok': True, 'effect': {'primitive': 'look', 'status': 'completed'},
                       'execution_budget': budget(397)}, 'look', {})
        self.assertIsNone(guard.deferral(done))

    def test_state_and_history_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guard.json'
            guard = FinishGuard(path)
            guard.observe(completed('grasp', 399), 'act', {})
            guard.deferral(ABORT)
            resumed = FinishGuard(path)
            self.assertEqual(resumed.deferrals, {'stop': 1})
            self.assertEqual(resumed.completed, [(1, 'grasp', '', None)])
            self.assertIn('followed the previous deferral', resumed.deferral(ABORT)['error']['message'])
            resumed.observe({'ok': True, 'closed': True}, 'finish', ABORT)
            history = json.loads(path.read_text())['history']
            self.assertEqual([item['decision'] for item in history], ['deferred', 'deferred', 'accepted'])
            self.assertEqual(history[0]['outcome'], 'aborted')
            self.assertEqual(history[0]['steps_remaining'], 399)


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Requires MCP SDK')
class MCPFinishGuardTests(unittest.IsolatedAsyncioTestCase):
    async def serve(self, calls, **options):
        from manipulation_agent.mcp_server import create_server

        def rpc(url, name, arguments, request_id, **kwargs):
            calls.append(name)
            if name == 'finish':
                return {'ok': True, 'closed': True, 'agent_outcome': arguments.get('outcome')}
            return completed('grasp', 399)
        health = json.dumps({'ready': True, 'tools': tool_specs('minimal')}).encode()
        self.patches = [patch('manipulation_agent.mcp_server.fetch', return_value=health),
                        patch('manipulation_agent.mcp_server.rpc', side_effect=rpc)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        server = await create_server('http://127.0.0.1:1', **options)
        return lambda name, **arguments: server._tool_manager.get_tool(name).fn(**arguments)

    async def test_guard_off_is_the_default_and_passes_finish_through(self):
        calls = []
        tool = await self.serve(calls)
        await tool('act', primitive='grasp')
        result = json.loads((await tool('finish', **ABORT))[0].text)
        self.assertTrue(result['closed'])
        self.assertEqual(calls, ['act', 'finish'])

    async def test_guard_on_defers_before_the_bridge_and_leaves_the_catalog_unchanged(self):
        calls = []
        tool = await self.serve(calls, finish_guard=True)
        await tool('act', primitive='grasp')
        result = json.loads((await tool('finish', **ABORT))[0].text)
        self.assertEqual(result['error']['code'], 'finish_deferred_budget_remaining')
        self.assertEqual(calls, ['act'])  # the deferred finish never reached the bridge


if __name__ == '__main__': unittest.main()
