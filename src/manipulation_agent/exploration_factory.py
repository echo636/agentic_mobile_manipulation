"""Operator-owned episode launch adapter. No model-supplied shell commands.

Launch manifests must point at a resource-admitting environment-only launcher.
The launcher must write env/run.json and expose the existing serialized bridge.
It must never start a second model controller.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
from .bridge import rpc

def launcher_identity(process):
    identity = {"pid": process.pid, "host": socket.gethostname()}
    try:
        identity["start_ticks"] = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        identity["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (OSError, IndexError):
        # Missing identity is uncertain, never evidence that a simulator exited.
        pass
    return identity

def health(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url.rstrip("/") + "/healthz", timeout=3) as reply:
        return json.load(reply)

class BridgeEpisode:
    def __init__(self, spec, process=None):
        self.spec, self.process = spec, process
        self.url = spec["bridge"]
        record = json.loads((Path(spec["run_dir"]) / "env/run.json").read_text())
        actual = record["config"]
        if any(actual.get(k) != v for k,v in spec["identity"].items()):
            raise ValueError("Actual episode task/instance/seed differs from fixed schedule")
        status = health(self.url)
        if record.get("status") != "running" or not status.get("ready") or status.get("closed"):
            raise RuntimeError("Recorded episode is not ready; preserve ownership for reconciliation")
        self.tools = status["tools"]

    def call(self, name, arguments, request_id):
        return rpc(self.url, name, arguments, request_id)

    def collect(self):
        deadline = time.monotonic() + self.spec.get("cleanup_timeout", 600)
        path = Path(self.spec["run_dir"]) / "env/run.json"
        while time.monotonic() < deadline:
            record = json.loads(path.read_text())
            if record.get("status") != "running" and record.get("evaluation") is not None:
                if self.process is not None:
                    self.process.wait(timeout=max(.1, deadline-time.monotonic()))
                elif (path.parents[1] / "worker_manifest.json").is_file():
                    # A reattached environment has no local Popen handle. The
                    # evaluator can finish while Kit still owns its GPU lease.
                    # Only the standard worker's post-release marker proves the
                    # next episode can acquire the same resources.
                    closed_path = path.parents[1] / "worker_closed.json"
                    try:
                        closed = json.loads(closed_path.read_text())
                        command = json.loads((path.parents[1] / "simulator_command.json").read_text())
                    except (OSError, ValueError):
                        closed = command = {}
                    if (closed.get("resource_leases_released") is not True or
                            not command.get("unit") or closed.get("unit") != command["unit"]):
                        time.sleep(.2)
                        continue
                return {"official_task_success": record["evaluation"].get("official_task_success"),
                        "evaluation": record["evaluation"], "run_dir": self.spec["run_dir"]}
            time.sleep(.2)
        raise TimeoutError("Owned episode closure/evaluation is incomplete; preserve evidence")

class BridgeFactory:
    def __init__(self, data_root):
        self.data_root = Path(data_root).resolve()

    def validate(self, spec):
        run = Path(spec["run_dir"])
        if not run.is_absolute() or not run.resolve().is_relative_to(self.data_root):
            raise ValueError("Episode outputs must be under the configured NAS/data root")
        url = urlsplit(spec["bridge"])
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or
                url.path not in ("", "/") or url.query or url.username or url.password):
            raise ValueError("Use a loopback bridge without credentials")
        command = spec["launch_command"]
        if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
            raise ValueError("Operator launcher must be an argv array")
        if spec.get("launcher_contract") != "resource_leased_environment_only":
            raise ValueError("Require the resource-admitting, single-owner environment-only launcher contract")
        return run

    def start(self, spec, *, on_launch=None):
        run = self.validate(spec)
        if run.exists():
            raise ValueError("New scheduled episode must have a fresh run directory")
        # Configured launcher owns initialization, GPU/host leases, cgroups and logs.
        # It is never generated or edited by the robot model.
        process = subprocess.Popen(spec["launch_command"], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        if on_launch is not None:
            on_launch(launcher_identity(process))
        deadline = time.monotonic() + spec.get("startup_timeout", 1800)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Environment-only launcher exited before readiness")
            try:
                record = json.loads((run / "env/run.json").read_text())
                ready = health(spec["bridge"])
                if record.get("status") == "running" and ready.get("ready") and not ready.get("closed"):
                    return BridgeEpisode(spec, process)
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        raise TimeoutError("Episode startup watchdog; launcher retains its failure evidence")

    def attach(self, active):
        spec = active["spec"]
        self.validate(spec)
        record = json.loads((Path(spec["run_dir"]) / "env/run.json").read_text())
        if record.get("status") != "running":
            raise RuntimeError("Recorded episode is no longer live; operator reconciliation required")
        return BridgeEpisode(spec)
