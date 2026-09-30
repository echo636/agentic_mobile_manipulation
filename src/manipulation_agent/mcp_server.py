"""MCP stdio proxy using the official Python SDK and the shared tool catalog."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from .bridge import rpc
from .contracts import tool_specs


async def run(url: str):
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
    server = Server("manipulation-agentic-system")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(**spec) for spec in tool_specs()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        request_id = str(uuid.uuid4())
        result = await asyncio.to_thread(rpc, url, name, arguments, request_id)
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", default="http://127.0.0.1:29430")
    args = parser.parse_args()
    asyncio.run(run(args.bridge))


if __name__ == "__main__":
    main()
