"""Actual HTTP transport checks, with a local provider and no model API calls."""
from concurrent.futures import ThreadPoolExecutor
import copy
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from manipulation_agent.provider_gateway import make_server
from manipulation_agent.image_pruning import prune_stale_images, count_images, PLACEHOLDER


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ProviderGatewayTests(unittest.TestCase):
    def setUp(self):
        proxy = patch.dict(os.environ, {'no_proxy': '127.0.0.1,localhost'})
        proxy.start()
        self.addCleanup(proxy.stop)

    def observation(self, revision):
        return {'type': 'custom_tool_call_output', 'call_id': 'call-'+str(revision),
                'output': [{'type': 'input_text', 'text': 'exact receipt '+str(revision)},
                           *[{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,'+str(revision)+view,
                              'detail': 'original'} for view in ('front', 'back', 'left', 'right')]]}

    def test_fixed_placeholders_preserve_prefix_until_previous_latest_images(self):
        payload = {'model': 'unchanged', 'input': [
            {'role': 'user', 'content': 'unchanged task'}, self.observation(1),
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'exact plan'}]},
            self.observation(2)]}
        before = copy.deepcopy(payload)
        stats = prune_stale_images(before)
        self.assertEqual(stats, {'image_items': 2, 'kept_images': 4, 'dropped_images': 4})
        self.assertEqual(before['input'][-1], payload['input'][-1])
        self.assertEqual(before['input'][1]['output'][0], payload['input'][1]['output'][0])
        self.assertEqual(before['input'][1]['output'][1], {'type': 'input_text', 'text': PLACEHOLDER})
        payload['input'].append(self.observation(3))
        after = copy.deepcopy(payload)
        prune_stale_images(after)
        self.assertEqual(before['input'][:3], after['input'][:3])
        self.assertNotEqual(before['input'][3], after['input'][3])
        self.assertEqual(count_images(after), 4)
        stable = copy.deepcopy(after)
        prune_stale_images(after)
        self.assertEqual(after, stable)
        control = copy.deepcopy(payload)
        prune_stale_images(control, 0)
        self.assertEqual(control, payload)

    def test_image_pruning_on_actual_http_preserves_current_views_and_response(self):
        seen = []
        response = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":42}}}\n\n'

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(response)

        payload = {'model': 'unchanged', 'input': [self.observation(1), self.observation(2)]}
        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as upstream:
            with serving(make_server(('127.0.0.1', 0), upstream, 1, keep_latest_image_items=1)) as local:
                for path in ('/responses', '/responses/compact'):
                    with urlopen(Request(local+path, json.dumps(payload).encode()), timeout=5) as result:
                        self.assertEqual(result.read(), response)
        self.assertEqual([path for path, _ in seen], ['/responses', '/responses/compact'])
        for _, forwarded in seen:
            self.assertEqual(count_images(forwarded), 4)
            self.assertEqual(forwarded['input'][-1], payload['input'][-1])
            self.assertEqual(forwarded['input'][0]['call_id'], payload['input'][0]['call_id'])

    def test_concurrency_waits_and_retries_without_changing_request_or_stream(self):
        lock = threading.Lock()
        state = dict(active=0, peak=0, calls={}, seen=[])

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                key = json.loads(body)['id']
                with lock:
                    state['active'] += 1
                    state['peak'] = max(state['peak'], state['active'])
                    state['calls'][key] = count = state['calls'].get(key, 0) + 1
                    state['seen'].append((self.path, body, self.headers['Authorization']))
                try:
                    time.sleep(.03)
                    if count == 1:
                        self.send_response(503)
                        self.end_headers()
                        self.wfile.write(b'{"code":"API_KEY_CONCURRENCY_QUEUE_FULL"}')
                    else:
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.end_headers()
                        self.wfile.write(b'data: ' + body + b'\n\ndata: [DONE]\n\n')
                finally:
                    with lock:
                        state['active'] -= 1

        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as upstream:
            gateway = make_server(('127.0.0.1', 0), upstream+'/v1', 2, retry_base=.001)
            with serving(gateway) as local:
                def request(i):
                    body = json.dumps(dict(id=i, model='unchanged-model', input='unchanged prompt')).encode()
                    response = urlopen(Request(local+'/responses', body,
                        {'Authorization': 'Bearer dummy', 'Content-Type': 'application/json'}), timeout=5)
                    with response:
                        self.assertEqual(response.headers['Content-Type'], 'text/event-stream')
                        self.assertEqual(response.read(), b'data: ' + body + b'\n\ndata: [DONE]\n\n')
                with ThreadPoolExecutor(max_workers=7) as pool:
                    list(pool.map(request, range(7)))
                status = gateway.request_queue.status()
        self.assertEqual(state['peak'], 2)
        self.assertEqual(state['calls'], {i: 2 for i in range(7)})
        self.assertTrue(all(path == '/v1/responses' and auth == 'Bearer dummy'
                            for path, body, auth in state['seen']))
        self.assertEqual(status['completed'], 7)
        self.assertEqual(status['retries'], 7)
        self.assertEqual(status['active'], 0)
        self.assertEqual(status['queued'], 0)

    def test_nonretryable_auth_failure_is_returned_once(self):
        calls = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(self.path)
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'authentication failed')

        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as upstream:
            gateway = make_server(('127.0.0.1', 0), upstream, 1, retry_base=.001)
            with serving(gateway) as local:
                with self.assertRaises(HTTPError) as raised:
                    urlopen(Request(local+'/responses', b'{}'), timeout=5)
                with raised.exception as response:
                    self.assertEqual(response.code, 401)
                    self.assertEqual(response.read(), b'authentication failed')
        self.assertEqual(calls, ['/responses'])

    def test_persistent_failure_reaches_client_with_original_error(self):
        calls = []
        body = b'{"error":{"code":"OPENAI_PERMANENT_STICKY_ACCOUNT_UNAVAILABLE"}}'

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(self.path)
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as upstream:
            gateway = make_server(('127.0.0.1', 0), upstream, 1, retry_base=.001)
            with serving(gateway) as local:
                with self.assertRaises(HTTPError) as raised:
                    urlopen(Request(local+'/responses', b'{}'), timeout=5)
                with raised.exception as response:
                    self.assertEqual(response.code, 503)
                    self.assertEqual(response.read(), body)
        self.assertEqual(len(calls), 3)
        self.assertEqual(gateway.request_queue.status()['active'], 0)

    def test_upstream_redirect_does_not_redirect_the_client(self):
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(308)
                self.send_header('Location', 'https://example.invalid/responses')
                self.end_headers()

        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as upstream:
            with serving(make_server(('127.0.0.1', 0), upstream, 1)) as local:
                with self.assertRaises(HTTPError) as raised:
                    urlopen(Request(local+'/responses', b'{}'), timeout=5)
                with raised.exception as response:
                    self.assertEqual(response.code, 502)
                    self.assertIsNone(response.headers.get('Location'))
                    self.assertIn(b'Provider upstream redirected request', response.read())


if __name__ == '__main__':
    unittest.main()
