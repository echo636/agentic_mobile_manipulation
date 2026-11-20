"""Index finalized recordings for browsers without changing video packets or time."""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .records import now, write_json
from .video import ffmpeg_executable


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def packet_signature(path):
    probe = shutil.which('ffprobe')
    if not probe:
        raise RuntimeError('ffprobe is required to verify lossless browser remuxing')
    result = subprocess.run([probe, '-v', 'error', '-select_streams', 'v:0',
        '-show_packets', '-show_streams', '-show_data_hash', 'sha256',
        '-show_entries', 'packet=pts,dts,data_hash:stream=codec_name,width,height,time_base',
        '-of', 'json', str(path)], check=True, capture_output=True, timeout=300)
    data = json.loads(result.stdout)
    canonical = {'streams': [{k: s.get(k) for k in ('codec_name','width','height','time_base')}
                             for s in data['streams']],
                 'packets': [{k: p.get(k) for k in ('pts','dts','data_hash')} for p in data['packets']]}
    return {'packets': len(canonical['packets']),
            'sha256': hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()}


def prepare_browser_video(directory: Path, source_name='episode.mp4'):
    """Retain fragmented source; atomically publish a verified fast-start copy."""
    directory = Path(directory)
    source = directory / source_name
    output = directory / 'browser_episode.mp4'
    manifest_path = directory / 'browser_video.json'
    with (directory / 'browser_video.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stat = source.stat()
        identity = {'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        if manifest_path.exists() and output.exists():
            cached = json.loads(manifest_path.read_text())
            if cached.get('status') == 'passed' and cached.get('source_identity') == identity:
                return cached
        temporary = directory / 'browser_episode.tmp.mp4'
        try:
            before = packet_signature(source)
            subprocess.run([ffmpeg_executable(), '-nostdin', '-v', 'error', '-y',
                '-i', str(source), '-map', '0:v:0', '-c:v', 'copy', '-copyts',
                '-use_editlist', '0', '-movflags', '+faststart', str(temporary)],
                check=True, capture_output=True, timeout=300)
            after = packet_signature(temporary)
            if before != after:
                raise RuntimeError('Browser copy changed video packets or timestamps')
            if identity != {'bytes': source.stat().st_size, 'mtime_ns': source.stat().st_mtime_ns}:
                raise RuntimeError('Source video changed during remux; retry after finalization')
            temporary.replace(output)
            result = {'status': 'passed', 'at': now(), 'file': output.name,
                'source': source.name, 'source_identity': identity,
                'source_sha256': file_hash(source), 'output_sha256': file_hash(output),
                'packet_signature': before, 'packets_and_timestamps_unchanged': True,
                'operation': 'stream_copy_faststart_no_reencoding'}
            write_json(manifest_path, result)
            return result
        finally:
            temporary.unlink(missing_ok=True)
