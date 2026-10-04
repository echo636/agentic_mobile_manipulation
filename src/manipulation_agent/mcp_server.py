"""Official SDK FastMCP proxy for one owner-thread simulator episode.

All transports share the same bridge, action budget and image revision. HTTP is
loopback-only and is not a multi-tenant episode service. Simulator calls remain
queued by bridge.py; this process never calls the simulator API directly.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
import hashlib
import ipaddress
import json
import uuid
import urllib.request
from urllib.parse import quote

from .bridge import rpc
from .deadline import tool_wait_seconds


def fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=30) as response:
        return response.read()


def loopback_host(value):
    if value == 'localhost': return value
    try:
        if ipaddress.ip_address(value).is_loopback: return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('MCP HTTP supports loopback hosts only (one shared episode)')


async def create_server(url: str, *, host='127.0.0.1', port=29431):
    """Freeze the bridge catalog and register it without changing its schemas.

    SDK Tool/FuncMetadata support schemas from external catalogs. The permissive
    argument carrier intentionally preserves JSON types and unknown fields so
    the owner-thread harness can reject them and record the original request.
    It also preserves the existing nullable-default compatibility for old act
    clients; generating a Pydantic signature here would coerce or drop inputs.
    """
    from mcp import types
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.tools import Tool
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
    from pydantic import ConfigDict

    host = loopback_host(host)
    health = json.loads(await asyncio.to_thread(fetch, url.rstrip('/') + '/healthz'))
    if not health.get('ready'):
        raise RuntimeError('Simulator bridge is not ready')
    catalog = health['tools']
    rpc_timeout=health.get('rpc_timeout_seconds',tool_wait_seconds(1800))
    if len({spec['name'] for spec in catalog}) != len(catalog):
        raise ValueError('Duplicate bridge tool names')
    lock = asyncio.Lock()

    class BridgeArguments(ArgModelBase):
        model_config = ConfigDict(extra='allow')

        def model_dump_one_level(self):
            return dict(self.model_extra or {})

    def wrap(name):
        async def invoke(**arguments):
            # Serialize actions and RGB downloads: another HTTP client cannot
            # expire returned image refs midway through response assembly.
            async with lock:
                result = await asyncio.to_thread(rpc, url, name, arguments, str(uuid.uuid4()),timeout=rpc_timeout)
                content = [types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]
                observation = result.get('observation', {})
                if observation.get('observation_mode') == 'rgb_only':
                    for frame in observation['images']:
                        data = await asyncio.to_thread(fetch, url.rstrip('/') + '/image/' + quote(frame['image_ref'], safe=''))
                        if hashlib.sha256(data).hexdigest() != frame['sha256']:
                            raise RuntimeError('RGB image content hash mismatch')
                        content.append(types.TextContent(type='text', text=f'RGB view={frame["view"]}, image_ref={frame["image_ref"]}'))
                        content.append(types.ImageContent(type='image', data=base64.b64encode(data).decode(), mimeType=frame['mime_type']))
                return content
        return invoke

    registered = [Tool(
        fn=wrap(spec['name']), name=spec['name'], description=spec.get('description', ''),
        parameters=deepcopy(spec['inputSchema']), is_async=True,
        fn_metadata=FuncMetadata(arg_model=BridgeArguments),
        annotations=types.ToolAnnotations(**spec['annotations']) if spec.get('annotations') else None,
        meta=deepcopy(spec.get('_meta')),
    ) for spec in catalog]
    return FastMCP(
        'manipulation-agentic-system', tools=registered, host=host, port=port,
        instructions='One shared simulator episode. All clients share image revisions, budgets and closure. '
                     'Select targets from the latest RGB. Tool completion is not task success.',
        log_level='WARNING',
    )


async def run(url: str, *, transport='stdio', host='127.0.0.1', port=29431):
    server = await create_server(url, host=host, port=port)
    if transport == 'stdio':
        await server.run_stdio_async()
    elif transport == 'sse':
        await server.run_sse_async()
    elif transport == 'streamable-http':
        await server.run_streamable_http_async()
    else:
        raise ValueError('Unknown MCP transport')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', default='http://127.0.0.1:29430')
    parser.add_argument('--transport', choices=('stdio', 'sse', 'streamable-http'), default='stdio')
    parser.add_argument('--host', type=loopback_host, default='127.0.0.1')
    parser.add_argument('--port', type=int, default=29431)
    args = parser.parse_args()
    asyncio.run(run(args.bridge, transport=args.transport, host=args.host, port=args.port))


if __name__ == '__main__':
    main()
