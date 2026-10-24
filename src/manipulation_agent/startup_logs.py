"""Read finite native loading milestones without callbacks on Kit threads."""
import inspect


def read_startup_logs(directory, offset=0, identity=None, native_offset=0, native_identity=None):
    # Keep this function self-contained: the controller sends its source to the
    # worker's system Python, without importing simulator packages or using NAS
    # to load a second copy of the project during initialization.
    import hashlib
    import json
    import os
    from datetime import datetime
    from pathlib import Path
    import re

    def read(name, offset, identity, native=False):
        path = Path(directory) / name
        try:
            stream = path.open('rb')
        except FileNotFoundError:
            return [], 0, None
        with stream:
            stat = os.fstat(stream.fileno())
            current_identity = [stat.st_dev, stat.st_ino]
            if identity == current_identity and stat.st_size >= offset:
                stream.seek(offset)
            begin = stream.tell()
            data = stream.read()
        complete = data.rfind(b'\n') + 1
        consumed = complete
        if native and b'\0' in data:
            # Mapped logs can have holes between completed writes as well as a
            # preallocated suffix. Read later messages now, but revisit the
            # first hole next poll in case it is filled. Milestones deduplicate.
            consumed = data[:data.index(b'\0')].rfind(b'\n') + 1
        return data[:complete].replace(b'\0', b'\n').splitlines(), begin + consumed, current_identity

    lines, offset, identity = read('startup_stages.jsonl', offset, identity)
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    lines, native_offset, native_identity = read(
        'native_runtime.log', native_offset, native_identity, native=True)
    # These are finite resources in the scene, not timestamps, log byte counts,
    # general activity, or repeated warnings. The watchdog deduplicates each
    # resource/stage even if Kit repeats it at a newer time.
    patterns = (
        ('material', re.compile(r"\[Info\] \[omni\.hydra\] Setting new rendering material for prim '([^']+)'")),
        ('asset', re.compile(r"\[Info\] \[omni\.usd\].*OmniUsdResolver: Opening filesystem asset '([^']+)' for read")),
    )
    for raw in lines:
        line = raw.decode('utf-8', errors='replace')
        for kind, pattern in patterns:
            match = pattern.search(line)
            if match:
                try:
                    at = datetime.fromisoformat(line.split(' ', 1)[0].replace('Z', '+00:00'))
                    if at.tzinfo is None:
                        break
                except ValueError:
                    break
                resource = hashlib.sha256(match[1].encode()).hexdigest()
                events.append({'stage': 'native_renderer_' + kind + '_' + resource,
                               'status': 'passed' if kind == 'material' else 'running',
                               'at_unix': at.timestamp(), 'source': 'native_runtime.log'})
                break
    return dict(events=events, offset=offset, identity=identity,
                native_offset=native_offset, native_identity=native_identity)


def startup_reader_code(directory, row):
    args = (str(directory), row.get('startup_log_offset', 0), row.get('startup_log_identity'),
            row.get('startup_native_log_offset', 0), row.get('startup_native_log_identity'))
    return (inspect.getsource(read_startup_logs) + '\nimport json\n'
            + 'print(json.dumps(read_startup_logs(*' + repr(args) + ')))')
