"""Trace Responses transport, preserving request bytes by default.

No prompts, images, arguments, response text or authorization headers are logged.
The explicit strict-MCP mode changes only outgoing robot tool schemas and records
both request digests. Neither mode trims history or rewrites model output.
"""
import argparse
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def end_to_end_headers(headers, *, request=False):
    excluded = {'connection', 'keep-alive', 'proxy-authenticate',
                'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}
    excluded.update(value.strip().lower() for value in headers.get('Connection', '').split(','))
    if request:
        excluded.update({'host', 'content-length'})
    return [(name, value) for name, value in headers.items() if name.lower() not in excluded]


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def strict_mcp_request(payload):
    """Require complete robot arguments at generation, retaining value constraints.

    This is a versioned wire protocol, not a repair of returned arguments. Native
    client tools keep their own optional-field contracts. Current robot schemas
    already close every object; fail explicitly if a future schema does not.
    """
    result = deepcopy(payload)

    def complete_required(schema):
        if isinstance(schema, dict):
            kind = schema.get('type', [])
            if kind == 'object' or (isinstance(kind, list) and 'object' in kind):
                properties = schema.get('properties')
                if not isinstance(properties, dict) or schema.get('additionalProperties') is not False:
                    raise ValueError('Strict robot tools require closed object schemas')
                schema['required'] = list(properties)
            for value in schema.values():
                complete_required(value)
        elif isinstance(schema, list):
            for value in schema:
                complete_required(value)

    for tool in result.get('tools', []):
        children = (tool.get('tools', []) if tool.get('type') == 'namespace'
                    and tool.get('name') == 'mcp__manipulation' else
                    [tool] if tool.get('type') == 'function'
                    and tool.get('name', '').startswith('mcp__manipulation__') else [])
        for child in children:
            if child.get('type') != 'function':
                continue
            complete_required(child['parameters'])
            child['strict'] = True
    return result


def tool_schema_metadata(items):
    """Record schema structure, never descriptions, defaults or input values."""
    rows = []
    for item in items[:32]:
        if not isinstance(item, dict):
            continue
        row = {'type': item.get('type'), 'name': item.get('name')}
        if 'strict' in item:
            row['strict'] = item['strict']
        parameters = item.get('parameters')
        if isinstance(parameters, dict):
            row.update(schema_sha256=canonical_sha256(parameters),
                       properties=sorted(parameters.get('properties', {})),
                       required=parameters.get('required', []),
                       additional_properties=parameters.get('additionalProperties'))
        if isinstance(item.get('tools'), list):
            row['tools'] = tool_schema_metadata(item['tools'])
        rows.append(row)
    return rows


def function_call_metadata(output):
    """Distinguish upstream missing fields from local argument loss."""
    rows = []
    for item in output[:32]:
        if not isinstance(item, dict) or item.get('type') != 'function_call':
            continue
        row = {'name': item.get('name')}
        try:
            arguments = json.loads(item.get('arguments', ''))
            row.update(argument_keys=sorted(arguments) if isinstance(arguments, dict) else None,
                       canonical_arguments_sha256=canonical_sha256(arguments))
        except (TypeError, ValueError):
            row['arguments_parse_error'] = True
        rows.append(row)
    return rows


class Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def record(self, **fields):
        with self.lock, self.path.open('a') as stream:
            stream.write(json.dumps({'at_unix': time.time(), **fields}) + '\n')

    def event(self, request_id, line):
        if not line.startswith(b'data: '):
            return
        try:
            event = json.loads(line[6:])
        except (ValueError, TypeError):
            return
        kind = event.get('type')
        if kind not in ('response.created', 'response.completed', 'response.incomplete',
                        'response.failed', 'error'):
            return
        response = event.get('response') or {}
        incomplete = response.get('incomplete_details') or {}
        error = response.get('error') or event.get('error') or {}
        usage = response.get('usage') or {}
        self.record(request_id=request_id, kind=kind, model=response.get('model'),
                    status=response.get('status'), incomplete_reason=incomplete.get('reason'),
                    error_code=error.get('code'), error_type=error.get('type'),
                    output_types=[item.get('type') for item in response.get('output', [])
                                  if isinstance(item, dict)],
                    function_calls=function_call_metadata(response.get('output', [])),
                    usage={k: usage[k] for k in ('input_tokens', 'output_tokens', 'total_tokens')
                           if k in usage})


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path != '/healthz':
            self.send_error(404)
            return
        mode = ('strict_mcp_tools_v1' if getattr(self.server, 'strict_mcp_tools', False)
                else 'unchanged_bytes')
        body = json.dumps({'ok': True, 'mode': mode}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/v1/responses':
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 64 * 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        request_id = str(uuid4())
        try:
            payload = json.loads(body)
            strict = getattr(self.server, 'strict_mcp_tools', False)
            forwarded_payload = strict_mcp_request(payload) if strict else payload
            forwarded = json.dumps(forwarded_payload, separators=(',', ':')).encode() if strict else body
        except (ValueError, TypeError, KeyError):
            self.send_error(400)
            return
        reasoning = payload.get('reasoning') or {}
        headers = dict(end_to_end_headers(self.headers, request=True))
        self.server.trace.record(request_id=request_id, kind='request',
            body_sha256=hashlib.sha256(body).hexdigest(), body_bytes=len(body),
            transport_mode='strict_mcp_tools_v1' if strict else 'unchanged_bytes',
            forwarded_body_sha256=hashlib.sha256(forwarded).hexdigest(),
            forwarded_body_bytes=len(forwarded),
            model=payload.get('model'), stream=payload.get('stream'),
            reasoning_effort=reasoning.get('effort'),
            max_output_tokens=payload.get('max_output_tokens'),
            input_items=len(payload.get('input', [])), tools=len(payload.get('tools', [])),
            tool_schemas=tool_schema_metadata(payload.get('tools', [])),
            forwarded_tool_schemas=tool_schema_metadata(forwarded_payload.get('tools', [])),
            forwarded_header_names=sorted(name.lower() for name in headers))
        request = Request(self.server.upstream, data=forwarded, headers=headers, method='POST')
        try:
            upstream = urlopen(request, timeout=600)
        except HTTPError as exc:
            upstream = exc
        except Exception as exc:
            self.server.trace.record(request_id=request_id, kind='connection_error',
                                     error_type=type(exc).__name__)
            self.send_error(502)
            return
        with upstream:
            self.send_response_only(upstream.status)
            response_headers = end_to_end_headers(upstream.headers)
            for name, value in response_headers:
                self.send_header(name, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            self.server.trace.record(request_id=request_id, kind='http_response',
                status=upstream.status, content_encoding=upstream.headers.get('Content-Encoding'),
                forwarded_header_names=sorted(name.lower() for name, _ in response_headers))
            digest = hashlib.sha256()
            response_bytes = 0
            disconnected = False
            try:
                for line in upstream:
                    digest.update(line)
                    response_bytes += len(line)
                    self.server.trace.event(request_id, line)
                    self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected = True
            finally:
                self.server.trace.record(request_id=request_id, kind='transport_end',
                    body_sha256=digest.hexdigest(), body_bytes=response_bytes,
                    client_disconnected=disconnected)
        self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--strict-mcp-tools', action='store_true',
                        help='Use strict_mcp_tools_v1 for robot tools; changes the wire protocol')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refuse to overwrite trace')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.upstream = 'https://api.gpt.ge/v1/responses'
    server.trace = Trace(args.output)
    server.strict_mcp_tools = args.strict_mcp_tools
    server.serve_forever()


if __name__ == '__main__':
    main()
