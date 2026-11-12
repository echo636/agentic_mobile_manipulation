"""Controller-side MCP extension: forward robot tools and add one visual planner."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .agent.planning_subagent import PlanningSubagent, PLANNER_TOOL
from .clients.planner import CodexPlannerRequest


async def create_server(session, planner):
    from mcp import types
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.tools import Tool
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
    from pydantic import ConfigDict

    catalog = (await session.list_tools()).tools
    lock = asyncio.Lock()

    class Arguments(ArgModelBase):
        model_config = ConfigDict(extra='allow')

        def model_dump_one_level(self):
            return dict(self.model_extra or {})

    def wrap(name):
        async def invoke(**arguments):
            async def call():
                if name == 'plan_task':
                    result = await asyncio.to_thread(planner.consult, arguments['progress'])
                    return [types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]
                result = await session.call_tool(name, arguments)
                planner.observe(name, arguments, result)
                return result  # Preserve native ImageContent and error flags verbatim.
            if name == 'finish':
                return await call()
            async with lock:
                return await call()
        return invoke

    catalog.append(types.Tool(**PLANNER_TOOL))
    return FastMCP('manipulation-with-planner', log_level='WARNING', tools=[Tool(
        name=spec.name, description=spec.description or '', parameters=spec.inputSchema,
        fn=wrap(spec.name), is_async=True, fn_metadata=FuncMetadata(arg_model=Arguments),
        annotations=spec.annotations, meta=spec.meta) for spec in catalog])


async def run(config):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    request = CodexPlannerRequest(output=config['output'], provider=config['provider'], gateway=config['gateway'])
    server_args = StdioServerParameters(command=config['upstream_command'],
                                        args=config['upstream_args'], env=dict(os.environ))
    async with stdio_client(server_args) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            catalog = (await session.list_tools()).tools
            action = next(tool for tool in catalog if tool.name == 'act')
            capabilities = action.description + '\nAvailable actions: ' + ', '.join(
                action.inputSchema['properties']['primitive']['enum'])
            planner = PlanningSubagent(instruction=config['instruction'], model=config['model'],
                effort=config['effort'], request=request, output=config['output'], capabilities=capabilities)
            try:
                server = await create_server(session, planner)
                await server.run_stdio_async()
            finally:
                planner.events.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(json.loads(args.config.read_text())))


if __name__ == '__main__':
    main()
