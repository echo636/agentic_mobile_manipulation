"""Real installed CLI, local scripted provider/MCP only; never uses paid API.

Opt in with MAS_TEST_CODEX_LOCAL_PROVIDER=1. A 65-second tool exceeds Code
Mode's old yield intervals; only two provider requests are allowed.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from manipulation_agent.clients import ClientConfig, get_adapter

MCP = r'''
import json,sys,time
from pathlib import Path
marker=Path(sys.argv[1])
for line in sys.stdin:
 request=json.loads(line);method=request.get('method');ident=request.get('id')
 with marker.with_suffix('.rpc').open('a') as trace:trace.write(str(method)+'\n')
 if ident is None:continue
 if method=='initialize':
  result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'local-test','version':'1'}}
 elif method=='tools/list':
  result={'tools':[{'name':'delayed_result','description':'Local blocking test, no robot.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}
 elif method=='tools/call':
  marker.write_text('started');time.sleep(65);marker.write_text('finished')
  result={'content':[{'type':'text','text':'LOCAL_TOOL_FINISHED'}],'isError':False}
 else:result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
'''


@unittest.skipUnless(os.environ.get('MAS_TEST_CODEX_LOCAL_PROVIDER') == '1' and shutil.which('codex'),
                     'opt-in local provider / real Codex CLI test')
class BlockingMCPTests(unittest.TestCase):
    def test_blocking_tool_waits_without_intermediate_model_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'marker'
            server_script = root / 'mcp.py'
            server_script.write_text(MCP)
            requests = []
            errors = []

            class Provider(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    try:
                        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        requests.append({'at': time.monotonic(), 'request': request})
                        tools = request.get('tools', []) + [tool for part in request.get('input', [])
                            if part.get('type') == 'additional_tools' for tool in part.get('tools', [])]
                        names = [c.get('name') for t in tools for c in t.get('tools',[])]
                        if len(requests) == 1:
                            if 'exec' not in names:
                                shape = [{k: t.get(k) for k in ['type','name','namespace']} |
                                         {'children': [{k: c.get(k) for k in ['type','name']} for c in t.get('tools',[])]} for t in tools]
                                raise AssertionError('Direct MCP missing; tools: ' + json.dumps(shape)[:6000]
                                    + '; request keys: ' + str(list(request))
                                    + '; input types: ' + str([(x.get('type'),x.get('role')) for x in request.get('input',[]) if isinstance(x,dict)]))
                            item = {'type': 'custom_tool_call', 'id': 'ctc_local', 'call_id': 'call_local',
                                    'name': 'exec', 'input': 'text(await tools.mcp__manipulation__delayed_result({}));',
                                    'status': 'completed'}
                        else:
                            if marker.read_text() != 'finished':
                                raise AssertionError('Model invoked before tool completion')
                            item = {'type': 'message', 'id': 'msg_local', 'role': 'assistant',
                                    'status': 'completed', 'content': [
                                        {'type': 'output_text', 'text': 'LOCAL_TEST_OK', 'annotations': []}]}
                        response = {'id': f'resp_local_{len(requests)}', 'object': 'response',
                                    'status': 'completed', 'model': 'gpt-6-astra', 'output': [item],
                                    'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}}
                        events = [
                            {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                            {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
                            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                            {'type': 'response.completed', 'response': response},
                        ]
                        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as exc:
                        errors.append(str(exc))
                        self.send_error(400, str(exc))

            server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                output = root / 'output'
                output.mkdir()
                config = ClientConfig('codex', 'gpt-6-astra', 'Run delayed_result and finish.',
                    sys.executable, [str(server_script), str(marker)], output, timeout=None,
                    reasoning_effort='high', blocking_tool_yield_ms=2147483647, auto_compact_token_limit=48000)
                project = get_adapter('codex').prepare_project(config, 'Use the one direct MCP tool.', ['delayed_result'])
                overrides = {
                    'model_provider': 'local_fixture',
                    'model_providers.local_fixture.name': 'Local fixture, no paid API',
                    'model_providers.local_fixture.base_url': f'http://127.0.0.1:{server.server_port}',
                    'model_providers.local_fixture.wire_api': 'responses',
                    'model_providers.local_fixture.requires_openai_auth': False,
                    'model_providers.local_fixture.env_key': 'MAS_LOCAL_FIXTURE_KEY',
                    'model_providers.local_fixture.request_max_retries': 0,
                    'model_providers.local_fixture.stream_max_retries': 0,
                    'cli_auth_credentials_store': 'ephemeral',
                    'sqlite_home': str(root / 'state'), 'log_dir': str(root / 'logs'),
                }
                project.argv[-1:-1] = [part for k, v in overrides.items()
                                       for part in ['-c', k + '=' + json.dumps(v)]]
                home = root / 'codex_home'
                home.mkdir()
                process = subprocess.run(project.argv, input=project.stdin, text=True,
                    capture_output=True, timeout=95, env={**os.environ, 'CODEX_HOME': str(home),
                        'MAS_LOCAL_FIXTURE_KEY': 'local-dummy-not-a-secret',
                        'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'})
                self.assertFalse(errors, str(errors) + process.stderr[-5000:] + process.stdout[-3000:]
                                 + (marker.with_suffix('.rpc').read_text() if marker.with_suffix('.rpc').exists() else 'NO_MCP_RPC'))
                self.assertEqual(process.returncode, 0, process.stderr[-3000:] + process.stdout[-3000:])
                self.assertEqual(len(requests), 2)
                self.assertGreaterEqual(requests[1]['at'] - requests[0]['at'], 65)
                self.assertNotIn('Script running with cell ID', process.stdout)
                self.assertNotIn('unrecognized configuration', process.stdout)
                self.assertNotIn('Code Mode is unavailable', process.stdout)
                self.assertIn('LOCAL_TEST_OK', process.stdout)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
