"""Local Responses transport compatibility for experiment provider models.

The proxy never stores request bodies or credentials. It only removes fields
the upstream compatibility endpoint cannot accept, and streams its response.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


UPSTREAM = 'https://api.gpt.ge/v1/responses'


def trim_history_images(payload, keep=24):
    """Keep recent RGB frames below the provider's 50-image request limit.

    Images are removed only from earlier history blocks. Text feedback and
    the most recent visual observations retain their order and contents.
    """
    images = []

    def collect(value):
        if isinstance(value, list):
            for index, child in enumerate(value):
                if (isinstance(child, dict)
                        and child.get('type') in ('input_image', 'image')
                        and any(key in child for key in ('image_url', 'url', 'data'))):
                    images.append((value, index))
                else:
                    collect(child)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)

    collect(payload.get('input', []))
    by_list = {}
    for parent, index in images[:-keep]:
        by_list.setdefault(id(parent), (parent, []))[1].append(index)
    for parent, indices in by_list.values():
        for index in sorted(indices, reverse=True):
            del parent[index]
    return max(0, len(images) - keep)


def compatible_request(payload):
    model = payload.get('model', '')
    removed_include = False
    removed_reasoning = 0
    namespaced_functions = {}
    if model == 'qwen3.8-flash' and 'include' in payload:
        payload.pop('include')
        removed_include = True
    if model == 'gpt-5.6-luna' and 'include' in payload:
        payload.pop('include')
        removed_include = True
    if model == 'qwen3.8-flash' and isinstance(payload.get('reasoning'), dict):
        payload['reasoning'].pop('summary', None)
    if model == 'qwen3.8-flash' and isinstance(payload.get('input'), list):
        original = payload['input']
        payload['input'] = [item for item in original
                            if not (isinstance(item, dict)
                                    and item.get('type') == 'reasoning')]
        removed_reasoning = len(original) - len(payload['input'])
    if model == 'qwen3.8-flash' and isinstance(payload.get('tools'), list):
        flattened = []
        for tool in payload['tools']:
            if not isinstance(tool, dict):
                continue
            if tool.get('type') == 'function':
                flattened.append(tool)
            elif (tool.get('type') == 'namespace'
                  and str(tool.get('name', '')).startswith('mcp__')):
                for child in tool.get('tools', []):
                    if isinstance(child, dict):
                        flat_name = tool['name'] + '__' + child['name']
                        namespaced_functions[flat_name] = (tool['name'], child['name'])
                        flattened.append({**child, 'type': 'function',
                                          'name': flat_name})
        payload['tools'] = flattened
        if isinstance(payload.get('input'), list):
            for item in payload['input']:
                if (isinstance(item, dict) and item.get('type') == 'function_call'
                        and str(item.get('namespace', '')).startswith('mcp__')):
                    item['name'] = item['namespace'] + '__' + item['name']
                    item.pop('namespace')
    if model == 'gpt-5.6-luna' and isinstance(payload.get('input'), list):
        original = payload['input']
        payload['input'] = [item for item in original
                            if not (isinstance(item, dict)
                                    and item.get('type') == 'reasoning'
                                    and item.get('encrypted_content'))]
        removed_reasoning = len(original) - len(payload['input'])
        if removed_reasoning:
            for item in payload['input']:
                if (isinstance(item, dict)
                        and (item.get('type') in ('function_call', 'custom_tool_call')
                             or (item.get('type') == 'message'
                                 and item.get('role') == 'assistant'))):
                    item.pop('id', None)
    return payload, removed_include, removed_reasoning, namespaced_functions


def restore_namespaces(event, namespaced_functions):
    def restore(item):
        if isinstance(item, dict) and item.get('type') == 'function_call':
            match = namespaced_functions.get(item.get('name'))
            if match:
                item['namespace'], item['name'] = match
    restore(event.get('item'))
    response = event.get('response')
    if isinstance(response, dict):
        for item in response.get('output', []):
            restore(item)
    return event


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        if self.path != '/healthz':
            self.send_error(404)
            return
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/v1/responses':
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > 32 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
            payload, removed_include, removed_reasoning, namespaced_functions = compatible_request(payload)
            old_images_removed = trim_history_images(payload)
            forwarded = json.dumps(payload, separators=(',', ':')).encode()
        except (ValueError, TypeError):
            self.send_error(400)
            return
        headers = {'Content-Type': 'application/json'}
        for name in ('Authorization', 'Accept', 'OpenAI-Beta'):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        request = Request(UPSTREAM, data=forwarded, headers=headers, method='POST')
        try:
            upstream = urlopen(request, timeout=600)
        except HTTPError as error:
            upstream = error
        except Exception as error:
            print(f'upstream_connection_error={type(error).__name__}', file=sys.stderr,
                  flush=True)
            self.send_error(502)
            return
        with upstream:
            self.send_response(upstream.status)
            for name in ('Content-Type', 'Cache-Control', 'X-Request-Id'):
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                if payload.get('stream'):
                    for line in upstream:
                        if line.startswith(b'data: '):
                            try:
                                event = restore_namespaces(json.loads(line[6:]),
                                                           namespaced_functions)
                                line = b'data: ' + json.dumps(event, separators=(',', ':')).encode() + b'\n'
                            except (ValueError, TypeError):
                                pass
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    body = upstream.read()
                    if namespaced_functions and upstream.status == 200:
                        response = json.loads(body)
                        body = json.dumps(restore_namespaces(
                            {'response': response}, namespaced_functions)['response']).encode()
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.close_connection = True
        print(f'model={payload.get("model")} status={upstream.status} '
              f'include_removed={removed_include} '
              f'encrypted_reasoning_removed={removed_reasoning} '
              f'old_images_removed={old_images_removed}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'listening=127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
