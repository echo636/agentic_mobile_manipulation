"""A model finish must interrupt an outstanding action through the MCP proxy."""
import asyncio
import importlib.util
import json
import threading
import unittest
from unittest.mock import patch

from manipulation_agent.mcp_server import create_server
from manipulation_agent.tools import tool_specs


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Requires MCP SDK')
class MCPFinishInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def test_finish_reaches_bridge_before_pending_action_completes(self):
        entered = threading.Event()
        cancelled = threading.Event()
        order = []
        def rpc(url, name, arguments, request_id, **kwargs):
            order.append(name)
            if name == 'act':
                entered.set()
                if not cancelled.wait(5): raise TimeoutError('finish never reached bridge')
                return {'ok': False, 'error': {'code': 'episode_cancelled'}}
            if name == 'finish':
                cancelled.set()
                return {'ok': True, 'closed': True}
            return {'ok': True}
        health = json.dumps({'ready': True, 'tools': tool_specs('minimal')}).encode()
        with patch('manipulation_agent.mcp_server.fetch', return_value=health), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc):
            server = await create_server('http://127.0.0.1:1')
            action = asyncio.create_task(server._tool_manager.get_tool('act').fn())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                ordinary = asyncio.create_task(server._tool_manager.get_tool('look').fn())
                await asyncio.sleep(.02)
                self.assertEqual(order, ['act'])
                result = await asyncio.wait_for(server._tool_manager.get_tool('finish').fn(), 2)
                self.assertTrue(json.loads(result[0].text)['closed'])
                await asyncio.wait_for(action, 2)
                await asyncio.wait_for(ordinary, 2)
                self.assertEqual(order, ['act', 'finish', 'look'])
            finally:
                cancelled.set()
                await action


if __name__ == '__main__': unittest.main()
