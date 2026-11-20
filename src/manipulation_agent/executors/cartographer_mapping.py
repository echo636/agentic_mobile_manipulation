"""Private sensor-built Cartographer probability maps with ideal localization.

This module owns a CPU worker, not a simulator thread. Depth projection supplies
per-camera origins and explicit hits/misses; the worker never reads scene maps,
object registries, task truth, a NavMesh, or a previous mapping session.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import socket
import subprocess
import time
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class OccupancySnapshot:
    width: int
    height: int
    resolution: float
    origin: tuple[float, float]
    occupancy: bytes
    sequence: int
    timestamp: float
    metadata: Mapping[str, Any]


def _coordinates(value: Sequence[float], size: int) -> list[float]:
    result = [float(x) for x in value]
    if len(result) != size or not all(math.isfinite(x) for x in result):
        raise ValueError(f'Expected {size} finite coordinates')
    return result


class CartographerMapper:
    """One append-only probability map per episode, driven on its owner thread.

    Map rows increase world Y and columns increase world X. ``origin`` is the
    center of cell (row=0, column=0). Bytes encode free=0, occupied=100,
    unobserved=255. This is native Cartographer range insertion with private
    ground-truth localization, not scan matching or loop-closure SLAM.
    """

    def __init__(self, output_dir, runtime=None, deadline=None, resolution=.05):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.deadline = deadline
        self.resolution = float(resolution)
        if not math.isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError('Mapping resolution must be positive and finite')
        default = Path(__file__).resolve().parents[5] / 'runtimes' / 'cartographer_probability_20261004_r2'
        self.runtime = Path(runtime or os.environ.get('MAS_CARTOGRAPHER_RUNTIME', default)).resolve()
        executable = self.runtime / 'bin' / 'mas_cartographer_worker'
        if not executable.is_file():
            raise FileNotFoundError(f'Native Cartographer mapper is missing: {executable}')
        self._stderr = (self.output_dir / 'worker.stderr.log').open('ab', buffering=0)
        env = os.environ.copy()
        # Only this private worker sees its copied native runtime dependencies.
        env['LD_LIBRARY_PATH'] = str(self.runtime / 'lib') + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
        try:
            self._process = subprocess.Popen(
                [str(executable), str(self.output_dir), str(self.resolution)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
                env=env, bufsize=0,
            )
        except BaseException:
            self._stderr.close()
            raise
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        os.set_blocking(self._process.stdin.fileno(), False)
        self._writer = selectors.DefaultSelector()
        self._writer.register(self._process.stdin, selectors.EVENT_WRITE)
        self._buffer = bytearray()
        self._sequence = 0
        self._closed = False
        self._input_log = (self.output_dir / 'sensor_packets.jsonl').open('a', buffering=1)
        self._runtime_record = {
            'status': 'running', 'host': socket.gethostname(), 'pid': self._process.pid,
            'gpu_uuid': None, 'worker': str(executable), 'runtime': str(self.runtime),
            'worker_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'started_at_unix': time.time(), 'output': str(self.output_dir),
            'localization': 'private_simulator_pose', 'map_source': 'current_episode_depth_only',
            'native_algorithm': 'Cartographer ProbabilityGridRangeDataInserter2D',
            'precomputed_walkability': False, 'scan_matching': False,
        }
        manifest = self.runtime / 'manifest.json'
        if manifest.is_file():
            self._runtime_record['runtime_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        self._write_runtime_record()

    @property
    def pid(self):
        return self._process.pid

    def _write_runtime_record(self):
        (self.output_dir / 'worker.json').write_text(json.dumps(self._runtime_record, indent=2) + '\n')

    def _receive(self):
        while b'\n' not in self._buffer:
            if self.deadline is not None:
                self.deadline.check()
            if self._selector.select(.1):
                block = os.read(self._process.stdout.fileno(), 65536)
                if not block:
                    raise RuntimeError(f'Cartographer worker exited ({self._process.poll()}); see {self.output_dir / "worker.stderr.log"}')
                self._buffer.extend(block)
        line, _, remainder = self._buffer.partition(b'\n')
        self._buffer[:] = remainder
        return json.loads(line)

    def _send(self, line):
        pending = memoryview(line)
        while pending:
            if self.deadline is not None:
                self.deadline.check()
            if self._process.poll() is not None:
                raise RuntimeError(f'Cartographer worker exited ({self._process.returncode}); see {self.output_dir / "worker.stderr.log"}')
            if self._writer.select(.1):
                try:
                    count = os.write(self._process.stdin.fileno(), pending)
                except BlockingIOError:
                    continue
                pending = pending[count:]

    def update(self, scans, *, pose, timestamp):
        if self._closed:
            raise RuntimeError('Mapper is closed')
        if self.deadline is not None:
            self.deadline.check()
        timestamp = float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError('Timestamp must be finite')
        sensor_packets = []
        for scan in scans:
            sensor_packets.append({
                'view': str(scan['view']), 'origin': _coordinates(scan['origin'], 3),
                'returns': [_coordinates(p, 3) for p in scan['returns']],
                'misses': [_coordinates(p, 3) for p in scan['misses']],
            })
        sequence = self._sequence + 1
        packet = {'sequence': sequence, 'pose': _coordinates(pose, 3), 'timestamp': timestamp, 'scans': sensor_packets}
        line = json.dumps(packet, separators=(',', ':'), allow_nan=False) + '\n'
        self._input_log.write(line)
        self._send(line.encode())
        response = self._receive()
        if response['sequence'] != sequence:
            raise RuntimeError('Cartographer response does not match its sensor packet')
        path = self.output_dir / response['file']
        if path.parent != self.output_dir or path.suffix != '.bin':
            raise RuntimeError('Invalid Cartographer snapshot filename')
        cells = path.read_bytes()
        width, height = int(response['width']), int(response['height'])
        if width < 1 or height < 1 or len(cells) != width * height or set(cells) - {0, 100, 255}:
            raise RuntimeError('Invalid Cartographer occupancy payload')
        resolution = float(response['resolution'])
        if resolution != self.resolution:
            raise RuntimeError('Cartographer changed map resolution')
        origin = tuple(_coordinates(response['origin'], 2))
        self._sequence = sequence
        return OccupancySnapshot(width, height, resolution, origin, cells, sequence, timestamp,
            {**response, 'file': str(path), 'worker_pid': self.pid,
             'map_source': 'current_episode_depth_only', 'precomputed_walkability': False})

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._input_log.close()
        self._selector.close()
        self._writer.close()
        try:
            self._process.stdin.close()
        except BrokenPipeError:
            pass
        # A cleanup grace period is not an action/navigation time budget.
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process.stdout.close()
        self._stderr.close()
        self._runtime_record.update(status='closed' if self._process.returncode == 0 else 'failed',
                                    exit_code=self._process.returncode, stopped_at_unix=time.time(),
                                    last_sequence=self._sequence)
        self._write_runtime_record()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
