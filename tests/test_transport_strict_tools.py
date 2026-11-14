"""Exercise schema hardening at the real HTTP transport boundary."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from manipulation_agent.contracts import validate
from manipulation_agent.tools import tool_specs


def load_transport():
    spec = importlib.util.spec_from_file_location('strict_transport',
        Path(__file__).parents[1] / 'scripts/transport/trace_responses_transport.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('strict', [True, False])
def test_native_mcp_schemas_over_real_http_preserve_history_headers_and_reply(tmp_path, strict):
    module = load_transport()
    functions = [{'type': 'function', 'name': row['name'], 'strict': False,
                  'description': row['description'], 'parameters': row['inputSchema']}
                 for row in tool_specs('skills')]
    payload = {'model': 'test-model', 'stream': True, 'tools': [
        {'type': 'function', 'name': 'native_optional', 'strict': False,
         'parameters': {'type': 'object', 'properties': {'cursor': {'type': 'string'}},
                        'required': [], 'additionalProperties': False}},
        {'type': 'namespace', 'name': 'mcp__manipulation', 'tools': functions}],
        'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'PRIVATE-PROMPT'},
            {'type': 'input_image', 'image_url': 'PRIVATE-IMAGE'}]}]}
    original = copy.deepcopy(payload)
    body = json.dumps(payload, indent=2).encode()
    response = b'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n\n'
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            received.append((self.rfile.read(int(self.headers['Content-Length'])),
                             self.headers.get('Authorization'), self.headers.get('X-Codex-Turn-State')))
            self.send_response(200)
            self.send_header('Content-Length', str(len(response)))
            self.send_header('X-Codex-Turn-State', 'PRIVATE-REPLY-STATE')
            self.end_headers(); self.wfile.write(response)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    proxy.upstream = f'http://127.0.0.1:{upstream.server_port}/v1/responses'
    proxy.trace = module.Trace(tmp_path / 'trace.jsonl')
    proxy.strict_mcp_tools = strict
    workers = [threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, proxy)]
    for worker in workers: worker.start()
    try:
        request = Request(f'http://127.0.0.1:{proxy.server_port}/v1/responses', data=body,
                          headers={'Authorization': 'Bearer PRIVATE-TOKEN',
                                   'X-Codex-Turn-State': 'PRIVATE-REQUEST-STATE'})
        with urlopen(request, timeout=5) as reply:
            assert reply.read() == response
            assert reply.headers['X-Codex-Turn-State'] == 'PRIVATE-REPLY-STATE'
    finally:
        for server in (proxy, upstream): server.shutdown(); server.server_close()
        for worker in workers: worker.join()
    forwarded, auth, state = received[0]
    assert auth == 'Bearer PRIVATE-TOKEN'
    assert state == 'PRIVATE-REQUEST-STATE'
    sent = json.loads(forwarded)
    if strict:
        def strict_objects(schema):
            if isinstance(schema, dict):
                if 'properties' in schema:
                    assert set(schema['required']) == set(schema['properties'])
                    assert schema['additionalProperties'] is False
                for child in schema.values(): strict_objects(child)
            elif isinstance(schema, list):
                for child in schema: strict_objects(child)
        for tool in sent['tools'][1]['tools']:
            assert tool['strict'] is True
            strict_objects(tool['parameters'])
            original_tool = next(t for t in functions if t['name'] == tool['name'])
            # Keep all constraints while making each object schema explicit.
            def expected_schema(schema):
                result = copy.deepcopy(schema)
                if isinstance(result, dict):
                    if 'properties' in result:
                        result['required'] = list(result['properties'])
                    return {key: expected_schema(value) for key, value in result.items()}
                if isinstance(result, list):
                    return [expected_schema(value) for value in result]
                return result
            assert tool['parameters'] == expected_schema(original_tool['parameters'])
            if tool['name'] == 'act':
                args = {'primitive': 'navigate_to', 'revision': 0,
                        'target': {'image_ref': 'rgb-00001-front', 'point': [0.5, 0.5]},
                        'placement_yaw_degrees': None, 'placement_roll_degrees': None,
                        'wait_seconds': None}
                validate(args, original_tool['parameters'])
                validate(args, tool['parameters'])
        sent['tools'] = original['tools']
        assert sent == original
    else:
        assert forwarded == body
    trace_text = (tmp_path / 'trace.jsonl').read_text()
    assert 'PRIVATE-' not in trace_text
    rows = [json.loads(line) for line in trace_text.splitlines()]
    record = next(row for row in rows if row['kind'] == 'request')
    assert record['body_sha256'] == hashlib.sha256(body).hexdigest()
    if strict:
        assert record['transport_mode'] == 'strict_mcp_tools_v1'
        assert record['forwarded_body_sha256'] == hashlib.sha256(forwarded).hexdigest()
        schema = next(t for t in record['forwarded_tool_schemas'][1]['tools'] if t['name'] == 'read_skill')
        assert schema['strict'] is True


@pytest.mark.parametrize('nested', [False, True])
def test_strict_mode_refuses_an_open_robot_object_schema(nested):
    module = load_transport()
    schema = copy.deepcopy(next(row['inputSchema'] for row in tool_specs('skills') if row['name'] == ('act' if nested else 'read_skill')))
    target = schema['properties']['target'] if nested else schema
    target.pop('additionalProperties')
    payload = {'tools': [{'type': 'namespace', 'name': 'mcp__manipulation',
                         'tools': [{'type': 'function', 'name': 'read_skill', 'parameters': schema}]}]}
    with pytest.raises(ValueError, match='closed object schemas'):
        module.strict_mcp_request(payload)
