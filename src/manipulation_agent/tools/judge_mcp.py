"""One-tool MCP server for the --finish-evidence judge on clients without a tool-free image call (agy).
argv: spec.json   spec: {"text": str, "images": [{"caption": str, "path": str}]}
Tool `look` returns the claims text and the evidence crops (already byte-limited). Measurement-free helper."""
import base64
import json
import sys
from pathlib import Path

from mcp import types
from mcp.server.fastmcp import FastMCP

SPEC = json.loads(Path(sys.argv[1]).read_text())
server = FastMCP('manipulation', log_level='WARNING')


@server.tool(name='look', description='Return the claims to check and the evidence crops.')
async def look() -> list:
    content = [types.TextContent(type='text', text=SPEC['text'])]
    for img in SPEC['images']:
        data = Path(img['path']).read_bytes()
        content.append(types.TextContent(type='text', text=img['caption']))
        content.append(types.ImageContent(type='image', data=base64.b64encode(data).decode(),
                                          mimeType=img.get('mime', 'image/jpeg')))
    return content


if __name__ == '__main__':
    server.run('stdio')
