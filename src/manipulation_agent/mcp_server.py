"""MCP stdio proxy using the official Python SDK and the shared tool catalog."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
import base64
import hashlib
import urllib.request
from urllib.parse import quote

from .bridge import rpc

def fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=30) as response:
        return response.read()


async def run(url: str):
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
    server = Server("manipulation-agentic-system")

    @server.list_tools()
    async def list_tools():
        health = json.loads(await asyncio.to_thread(fetch, url.rstrip("/") + "/healthz"))
        return [types.Tool(**spec) for spec in health["tools"]]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        request_id = str(uuid.uuid4())
        result = await asyncio.to_thread(rpc, url, name, arguments, request_id)
        content = [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        observation = result.get("observation", {})
        if observation.get("observation_mode") == "rgb_only":
            for frame in observation["images"]:
                data = await asyncio.to_thread(fetch, url.rstrip("/") + "/image/" + quote(frame["image_ref"], safe=""))
                if hashlib.sha256(data).hexdigest() != frame["sha256"]:
                    raise RuntimeError("RGB image content hash mismatch")
                content.append(types.TextContent(type="text", text=f'RGB view={frame["view"]}, image_ref={frame["image_ref"]}'))
                content.append(types.ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType=frame["mime_type"]))
        return content

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", default="http://127.0.0.1:29430")
    args = parser.parse_args()
    asyncio.run(run(args.bridge))


if __name__ == "__main__":
    main()
