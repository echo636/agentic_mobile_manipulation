import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


def test_real_http_forwarding_is_unchanged_and_trace_excludes_secrets(tmp_path):
    spec = importlib.util.spec_from_file_location('transport_trace',
        Path(__file__).parents[1] / 'scripts/trace_responses_transport.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    body = json.dumps({'model': 'test', 'input': [{'text': 'PRIVATE-PROMPT'}],
        'stream': True, 'tools': [{'type': 'namespace', 'name': 'robot', 'tools': [
            {'type': 'function', 'name': 'look', 'parameters': {'type': 'object',
                'properties': {'decision': {}, 'yaw_degrees': {}},
                'required': ['decision', 'yaw_degrees'], 'additionalProperties': False}}]}]}).encode()
    response = ('data: ' + json.dumps({'type': 'response.completed', 'response': {
        'model': 'returned-model', 'status': 'completed', 'output': [
            {'type': 'message', 'text': 'PRIVATE-REPLY'},
            {'type': 'function_call', 'name': 'robot.look',
             'arguments': json.dumps({'decision': {'reason': 'PRIVATE-ARGUMENT'}})}],
        'usage': {'output_tokens': 2}}}) + '\n\n').encode()
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def do_POST(self):
            received.append((self.headers.get('Authorization'),
                             self.rfile.read(int(self.headers['Content-Length'])),
                             self.headers.get('X-Codex-Turn-State'),
                             self.headers.get('User-Agent')))
            self.send_response(200); self.send_header('Content-Length', str(len(response)))
            self.send_header('X-Codex-Turn-State', 'PRIVATE-RETURN-STATE')
            self.end_headers(); self.wfile.write(response)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    proxy.upstream = f'http://127.0.0.1:{upstream.server_port}/v1/responses'
    proxy.trace = module.Trace(tmp_path / 'trace.jsonl')
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, proxy)]
    for thread in threads: thread.start()
    try:
        request = Request(f'http://127.0.0.1:{proxy.server_port}/v1/responses', data=body,
                          headers={'Authorization': 'Bearer PRIVATE-TOKEN',
                                   'X-Codex-Turn-State': 'PRIVATE-REQUEST-STATE',
                                   'User-Agent': 'PRIVATE-ORIGINAL-CLIENT'})
        with urlopen(request, timeout=5) as result:
            assert result.read() == response
            assert result.headers.get('X-Codex-Turn-State') == 'PRIVATE-RETURN-STATE'
        assert received == [('Bearer PRIVATE-TOKEN', body,
                             'PRIVATE-REQUEST-STATE', 'PRIVATE-ORIGINAL-CLIENT')]
    finally:
        for server in (proxy, upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join()
    text = (tmp_path / 'trace.jsonl').read_text()
    assert 'PRIVATE-' not in text
    rows = [json.loads(line) for line in text.splitlines()]
    completed = next(row for row in rows if row['kind'] == 'response.completed')
    assert completed['model'] == 'returned-model'
    assert completed['output_types'] == ['message', 'function_call']
    assert completed['status'] == 'completed'
    assert completed['function_calls'] == [{'name': 'robot.look',
        'argument_keys': ['decision'], 'canonical_arguments_sha256':
        module.canonical_sha256({'decision': {'reason': 'PRIVATE-ARGUMENT'}})}]
    request = next(row for row in rows if row['kind'] == 'request')
    schema = request['tool_schemas'][0]['tools'][0]
    assert schema['properties'] == ['decision', 'yaw_degrees']
    assert schema['required'] == ['decision', 'yaw_degrees']
