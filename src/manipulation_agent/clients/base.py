"""Shared process runner; deadline starts upstream after simulator/MCP initialization."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Callable

from .types import ClientCapability, ClientRunResult, PreparedProject
from .events import parse_events


def run_process(project: PreparedProject, output: Path, timeout: float,
                on_start: Callable[[int], None] | None = None) -> ClientRunResult:
    started = time.monotonic()
    timed_out = False
    kwargs = {"start_new_session": True, "text": True}
    if project.cwd is not None:
        kwargs["cwd"] = str(project.cwd)
    if project.env_overlay:
        kwargs["env"] = {**os.environ, **project.env_overlay}
    with (output / "model_events.jsonl").open("w") as stdout, (output / "client.stderr.log").open("w") as stderr:
        process = subprocess.Popen(project.argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, **kwargs)
        if on_start:
            on_start(process.pid)
        try:
            process.communicate(project.stdin, timeout=timeout)
            finished = time.time()
        except subprocess.TimeoutExpired:
            finished = time.time()  # Exclude termination and parsing from execution time.
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=20)
    return ClientRunResult(process.returncode, timed_out, finished,
                           time.monotonic() - started, process.pid)


class ClientAdapter:
    name: str
    required_flags: tuple[str, ...] = ()
    help_args: tuple[str, ...] = ("--help",)

    def probe(self) -> ClientCapability:
        executable = shutil.which(self.name)
        if executable is None:
            return ClientCapability(self.name, "unavailable", reason="Executable is not installed on PATH")
        try:
            version = subprocess.check_output([self.name, "--version"], text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
            if self.required_flags:
                help_text = subprocess.check_output([self.name, *self.help_args], text=True, stderr=subprocess.DEVNULL, timeout=10)
                missing = [flag for flag in self.required_flags if flag not in help_text]
                if missing:
                    return ClientCapability(self.name, "unsupported", version, executable,
                                            "Required CLI flags missing: " + ", ".join(missing))
            return ClientCapability(self.name, "available", version, executable)
        except (OSError, subprocess.SubprocessError) as exc:
            return ClientCapability(self.name, "unsupported", executable=executable,
                                    reason="CLI capability check failed: " + type(exc).__name__)

    def prepare_project(self, config, instructions: str, tool_names: list[str]) -> PreparedProject:
        raise NotImplementedError

    def run(self, project, output, timeout, on_start=None):
        return run_process(project, output, timeout, on_start)

    def parse(self, path: Path):
        return parse_events(path, self.name)
