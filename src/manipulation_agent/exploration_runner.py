"""Isolated frozen-controller runner; calls the original Harness via a host broker."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
import shutil
import socketserver
import subprocess
import threading
import time
import uuid
from .exploration_workspace import safe_tree, tree_hash
from .records import write_json

# The child has only stdlib/API/frozen code/output, no repository/assets/GPU/network.
ENTRY = """
import importlib.util,json,sys,traceback
from pathlib import Path
from api.runtime import Context
sys.path.insert(0,'/bundle')
try:
 spec=importlib.util.spec_from_file_location('controller','/bundle/controller.py')
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 with Context('/robot.sock') as ctx:
  ctx.bind_manip(getattr(mod,'Manip',None))
  invocation=Path('/runtime/invocation.json')
  result=ctx.call('manip',allow_draft=True,**json.loads(invocation.read_text())) if invocation.exists() else mod.main(ctx)
 if not isinstance(result,dict) or type(result.get('ok')) is not bool:
  raise ValueError('main must return a dict with boolean ok')
 with open('/output/controller_result.json','x') as f:json.dump(result,f,allow_nan=False)
except BaseException:
 traceback.print_exc()
 sys.exit(1)
"""

def run_controller(bundle, expected_hash, output, call, allowed_tools, *, sandbox=True, invocation=None):
    if not sandbox:
        raise ValueError("Unsandboxed controller execution is forbidden")
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if tree_hash(safe_tree(bundle)) != expected_hash:
        raise ValueError("Bundle content differs from registration")
    output.mkdir(parents=True, exist_ok=False)
    exports = output / "exports"
    exports.mkdir()
    runtime = output / "runtime"
    (runtime / "api").mkdir(parents=True)
    (runtime / "api/__init__.py").touch()
    shutil.copyfile(Path(__file__).with_name("exploration_context.py"), runtime / "api/runtime.py")
    (runtime / "entry.py").write_text(ENTRY)
    if invocation is not None:
        write_json(runtime / 'invocation.json', invocation)
    calls = []
    from .exploration_manip import FrozenManip
    latest = [None]
    def dispatch(name, args, ident):
        try:
            reply = call(name, args, ident)
        except Exception as exc:
            failed_reply = getattr(exc, 'reply', None)
            if isinstance(failed_reply, dict) and isinstance(failed_reply.get('observation'), dict):
                latest[0] = failed_reply['observation']
            raise
        if isinstance(reply.get('observation'), dict):
            latest[0] = reply['observation']
        return reply
    procedures = FrozenManip(bundle, dispatch, lambda: latest[0], output / 'procedure_trace.jsonl')
    serialization = threading.Lock()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            name = ident = None
            try:
                raw = self.rfile.readline(65537)
                if len(raw) > 65536:
                    raise ValueError("Oversized robot request")
                request = json.loads(raw)
                name, args, ident = request["name"], request["arguments"], request["request_id"]
                stage = request.get('stage')
                if not isinstance(args, dict) or (name not in allowed_tools and not (name == 'manip' and stage in {'begin', 'end'})):
                    raise ValueError("Controller tool outside frozen contract")
                with serialization:
                    if stage is not None:
                        if name != 'manip' or stage not in {'begin', 'end'}:
                            raise ValueError('Invalid procedure boundary')
                        reply = procedures.begin(args, ident) if stage == 'begin' else procedures.end(args, ident)
                    else:
                        reply = procedures.child(name, args, ident)
                    if stage != 'begin':
                        calls.append({"tool": name, "request_id": ident, "ok": reply.get("ok")})
            except Exception as e:
                reply = {"ok": False, "error": {"code": "controller_rpc_failed", "type": type(e).__name__,
                                               "message": str(e)[:500]}}
                if latest[0] is not None:
                    reply['observation'] = latest[0]
                if isinstance(name, str) and isinstance(ident, str):
                    calls.append({"tool": name, "request_id": ident, "ok": False,
                                  "error": reply["error"]})
            self.wfile.write(json.dumps(reply, allow_nan=False).encode() + b"\n")
    socket_root = Path(os.environ.get("MAS_EXPLORATION_SOCKET_ROOT",
                       str(Path(tempfile.gettempdir()) / ("mas-exploration-" + str(os.getuid())))))
    socket_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    sock = socket_root / (uuid.uuid4().hex[:20] + ".sock")
    if len(os.fsencode(sock)) > 100:
        raise ValueError("Configure a shorter MAS_EXPLORATION_SOCKET_ROOT on NAS/data")
    server = socketserver.UnixStreamServer(str(sock), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    if not sandbox:
        # Only CPU tests may supply their own runner; never silently fall back.
        server.shutdown(); server.server_close(); sock.unlink()
        raise ValueError("Unsandboxed controller execution is forbidden")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        server.shutdown(); server.server_close(); sock.unlink()
        raise RuntimeError("bubblewrap required")
    cmd = [bwrap, "--die-with-parent", "--cap-drop", "ALL", "--unshare-net", "--unshare-pid",
           "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
           "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for name in ("bin", "sbin", "lib", "lib64"):
        path = Path("/") / name
        if path.is_symlink():
            cmd += ["--symlink", os.readlink(path), str(path)]
        elif path.exists():
            cmd += ["--ro-bind", str(path), str(path)]
    cmd += ["--ro-bind", str(bundle), "/bundle", "--ro-bind", str(runtime), "/runtime",
            "--bind", str(exports), "/output", "--ro-bind", str(sock), "/robot.sock",
            "--chdir", "/output", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "PYTHONPATH", "/runtime", "/usr/bin/python3", "/runtime/entry.py"]
    limiter = shutil.which("prlimit")
    if not limiter:
        server.shutdown(); server.server_close(); sock.unlink()
        raise RuntimeError("prlimit required for controller address-space and output limits")
    cmd = [limiter, "--as=1073741824", "--nofile=128", "--fsize=67108864", "--", *cmd]
    started = time.monotonic()
    try:
        with (output / "stdout.log").open("w") as out, (output / "stderr.log").open("w") as err:
            process = subprocess.run(cmd, stdout=out, stderr=err, start_new_session=True)
        result_path = exports / "controller_result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else None
        incomplete = procedures.incomplete()
        record = {"exit_code": process.returncode, "controller_result": result,
                  "clean": process.returncode == 0 and isinstance(result, dict) and not incomplete and not procedures.failed,
                  "bundle": expected_hash, "calls": calls,
                  "procedures": [row for row in procedures.records if row['kind'] != 'procedure_child'],
                  "duration_seconds": time.monotonic() - started, "unattended": True,
                  "no_live_agent": True, "sandbox": "bubblewrap_offline_no_gpu"}
        write_json(output / "execution.json", record)
        return record
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)
        sock.unlink(missing_ok=True)
