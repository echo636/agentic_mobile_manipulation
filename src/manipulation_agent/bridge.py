"""Loopback HTTP bridge. HTTP workers queue work; the simulator stays on the main thread."""
from __future__ import annotations

import concurrent.futures
import json
import queue
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import tool_specs

CLOSED_REPLAY_SECONDS = 10

def rpc(url: str, name: str, arguments: dict, request_id: str, timeout: float = 300) -> dict:
    body = json.dumps({"name": name, "arguments": arguments, "request_id": request_id}).encode()
    request = urllib.request.Request(url.rstrip("/") + "/call", data=body,
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def serve(harness, port: int) -> None:
    jobs = queue.Queue(maxsize=32)
    shutdown = threading.Event()
    # A native symbolic action can legitimately take more than five minutes.
    # Its execution deadline is checked by the owner/supervisor; transport waits
    # must allow that deadline plus cleanup/score delivery, not truncate the RPC.
    rpc_timeout = harness.budget.wall_seconds + 120 if getattr(harness, 'profile', None) == 'official' else 300

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, payload):
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The request ID remains replayable when the client reconnects.

        def do_GET(self):
            if self.path == "/healthz":
                catalog = harness.tool_specs() if hasattr(harness, "tool_specs") else tool_specs()
                self.send(200, {"ready": True, "closed": harness.closed, "tools": catalog, "rpc_timeout_seconds": rpc_timeout})
            elif self.path.startswith("/image/") and hasattr(harness, "image_bytes"):
                ref = self.path.removeprefix("/image/")
                try:
                    data, mime = harness.image_bytes(ref)
                except KeyError:
                    return self.send(404, {"error": "unknown_image_ref"})
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/call":
                return self.send(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or set(body) != {"name", "arguments", "request_id"}:
                    raise ValueError("Expected name, arguments and request_id")
                if not isinstance(body["name"], str) or not isinstance(body["arguments"], dict) or not isinstance(body["request_id"], str):
                    raise ValueError("Invalid request types")
                future = concurrent.futures.Future()
                jobs.put_nowait((body, future))
            except (ValueError, queue.Full):
                return self.send(400, {"error": "invalid_or_overloaded_request"})
            try:
                result = future.result(timeout=rpc_timeout)
            except concurrent.futures.TimeoutError:
                # Do not claim cancellation: the action may already be running.
                return self.send(504, {"error": "outcome_unknown", "retry_with_same_request_id": True})
            self.send(200, result)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    harness.start_standalone_clock()
    harness.recorder.event("bridge_started", {"host": "127.0.0.1", "port": server.server_port})
    print(f"MAS_BRIDGE_READY=http://127.0.0.1:{server.server_port}", flush=True)
    closed_at = None
    try:
        while not shutdown.is_set():
            # Keep a short replay window so a timed-out finish can be retried idempotently.
            if harness.closed:
                closed_at = closed_at or time.monotonic()
                if time.monotonic() - closed_at > CLOSED_REPLAY_SECONDS:
                    break
            elif harness.deadline.expired:
                harness.call('finish', {'outcome':'aborted','reason':'Episode wall-clock deadline exhausted'}, 'episode-deadline-finish')
            elif not harness.deadline.managed and time.monotonic() - harness.started > harness.budget.wall_seconds:
                harness.call("finish", {"outcome": "aborted", "reason": "Service wall-clock budget exhausted"}, "service-timeout")
            if hasattr(harness, 'tick_background') and not harness.closed:
                harness.tick_background()
            try:
                active = getattr(getattr(harness, 'surround', None), 'active', False)
                body, future = jobs.get(timeout=0 if active else 0.2)
            except queue.Empty:
                continue
            try:
                result = harness.call(body["name"], body["arguments"], body["request_id"])
            except Exception as exc:
                future.set_result({"ok": False, "error": {"code": "runtime_failure", "type": type(exc).__name__}})
                raise
            else:
                future.set_result(result)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
