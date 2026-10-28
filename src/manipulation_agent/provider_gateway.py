"""Loopback request queue for a fixed model provider; no prompt or credential logging."""
from collections import deque
from contextlib import contextmanager
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import random
import select
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener
from .agent.image_pruning import prune_stale_images


HOP_HEADERS = {'host', 'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RequestQueue:
    """Limit HTTP work, releasing slots during retry backoff and simulator actions."""
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError('Request capacity must be positive')
        self.capacity = capacity
        self.active = 0
        self.waiters = deque()
        self.condition = threading.Condition()
        self.counts = {'requests': 0, 'retries': 0, 'completed': 0, 'disconnected': 0}

    def count(self, name):
        with self.condition:
            self.counts[name] += 1

    def status(self):
        with self.condition:
            return dict(capacity=self.capacity, active=self.active, queued=len(self.waiters), **self.counts)

    @contextmanager
    def slot(self, alive):
        ticket = object()
        acquired = False
        with self.condition:
            self.waiters.append(ticket)
        try:
            with self.condition:
                while self.active >= self.capacity or self.waiters[0] is not ticket:
                    if not alive():
                        raise ConnectionAbortedError('Waiting client disconnected')
                    self.condition.wait(.25)
                if not alive():
                    raise ConnectionAbortedError('Waiting client disconnected')
                self.waiters.popleft()
                self.active += 1
                acquired = True
            yield
        finally:
            with self.condition:
                if acquired:
                    self.active -= 1
                else:
                    self.waiters.remove(ticket)
                self.condition.notify_all()


def make_server(address, upstream, capacity, *, retry_base=2., ca_file=None, max_attempts=3,
                keep_latest_image_items=0):
    endpoint = urlsplit(upstream)
    if (endpoint.scheme not in {'http', 'https'} or not endpoint.netloc
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError('Expected fixed provider base URL without credentials or query')
    if address[0] not in {'127.0.0.1', '::1'}:
        raise ValueError('Provider queue must bind to loopback')
    if max_attempts < 1:
        raise ValueError('Provider attempts must be positive')
    if keep_latest_image_items < 0:
        raise ValueError('Image history retention must be nonnegative')
    queue = RequestQueue(capacity)
    context = ssl.create_default_context(cafile=ca_file)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def alive(self):
            if select.select([self.connection], [], [], 0)[0]:
                return bool(self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT))
            return True

        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            body = json.dumps(queue.status()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # This is a fixed provider proxy, never a general forward proxy.
            if self.path not in {'/responses', '/responses/compact'}:
                self.send_error(404)
                return
            if self.headers.get('Transfer-Encoding') or not self.headers.get('Content-Length'):
                self.send_error(411)
                return
            length = int(self.headers['Content-Length'])
            if not 0 <= length <= 128 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.close_connection = True
                return
            image_stats = None
            if keep_latest_image_items:
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    pass  # Preserve upstream handling of malformed requests.
                else:
                    image_stats = prune_stale_images(payload, keep_latest_image_items)
                    if image_stats['dropped_images']:
                        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            request = Request(upstream.rstrip('/') + self.path, body, headers, method='POST')
            # Local instrumentation may read these counts; they are not HTTP headers.
            request.image_pruning = dict(image_stats or {}, input_bytes=length, forwarded_bytes=len(body))
            queue.count('requests')
            sent_headers = False
            attempt = 0
            try:
                while self.alive():
                    attempt += 1
                    delay = min(30., retry_base * 2 ** min(attempt-1, 4))
                    with queue.slot(self.alive):
                        try:
                            response = opener.open(request, timeout=300)
                        except HTTPError as exc:
                            response = exc
                        except (URLError, TimeoutError, ConnectionError):
                            response = None
                        if response is None and attempt >= max_attempts:
                            self.send_error(502, 'Provider transport interrupted')
                            queue.count('completed')
                            return
                        if response is not None:
                            try:
                                if 300 <= response.status < 400:
                                    # A Location forwarded from loopback makes the
                                    # client leave this fixed provider queue and
                                    # can strip Authorization on the new origin.
                                    self.send_error(502, 'Provider upstream redirected request')
                                    queue.count('completed')
                                    return
                                # Let the native client see persistent failures and
                                # recover; replaying one request forever hides them.
                                if response.status not in {429, 502, 503, 504} or attempt >= max_attempts:
                                    self.send_response(response.status)
                                    for key, value in response.headers.items():
                                        if key.lower() not in HOP_HEADERS:
                                            self.send_header(key, value)
                                    self.send_header('Connection', 'close')
                                    self.end_headers()
                                    sent_headers = True
                                    # Once any response is forwarded, never replay it.
                                    # Native client continuation handles interrupted SSE.
                                    while chunk := response.read1(64 * 1024):
                                        self.wfile.write(chunk)
                                        self.wfile.flush()
                                    queue.count('completed')
                                    return
                                retry_after = response.headers.get('Retry-After', '')
                                if retry_after.isdecimal():
                                    delay = max(delay, min(300., float(retry_after)))
                            finally:
                                response.close()
                    queue.count('retries')
                    end = time.monotonic() + delay + random.uniform(0., min(1., delay))
                    while time.monotonic() < end:
                        if not self.alive():
                            return
                        time.sleep(max(0., min(.25, end-time.monotonic())))
            except (OSError, HTTPException):
                queue.count('disconnected')
                if not sent_headers:
                    # No request body, credentials or upstream exception enters logs.
                    try:
                        self.send_error(502, 'Provider transport interrupted')
                    except OSError:
                        pass
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    server.request_queue_size = 128
    server.request_queue = queue
    return server


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--upstream', required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--max-inflight', type=int, required=True)
    p.add_argument('--ca-file')
    p.add_argument('--keep-latest-image-items', type=int, default=0,
                   help='Keep this many newest image-bearing input items; 0 preserves all images')
    args = p.parse_args()
    server = make_server(('127.0.0.1', args.port), args.upstream, args.max_inflight, ca_file=args.ca_file,
                         keep_latest_image_items=args.keep_latest_image_items)
    print(json.dumps(dict(status='ready', port=args.port, max_inflight=args.max_inflight)), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
