"""Export every recorded third-person frame without resimulation or trimming."""
import json
import bisect
import math
from datetime import datetime
from pathlib import Path
import subprocess

from .records import now, write_json
from .streaming_video import file_hash
from .video import ffmpeg_executable


def export_spectator_video(run_dir: Path):
    run_dir = Path(run_dir)
    source = json.loads((run_dir / 'video.json').read_text())
    if source.get('status') != 'passed' or 'spectator' not in source.get('views', []):
        raise ValueError('A finalized video with a spectator view is required')
    input_path = run_dir / source['file']
    source_hash = file_hash(input_path)
    record_path = run_dir / 'spectator_video.json'
    output = run_dir / 'spectator.mp4'
    if record_path.exists():
        old = json.loads(record_path.read_text())
        if (old.get('status') == 'passed' and old.get('source_sha256') == source_hash
                and output.is_file() and file_hash(output) == old.get('sha256')):
            return old
        raise FileExistsError('Preserve the previous spectator export and its validation')
    size = source['width'] // 2
    index = source['views'].index('spectator')
    x, y = index % 2 * size, index // 2 * size
    record = {'status': 'running', 'started_at': now(), 'file': output.name,
              'exporter_sha256': file_hash(Path(__file__)),
              'source_file': source['file'], 'source_sha256': source_hash,
              'source_frame_count': source['frame_count'], 'crop': [x, y, size, size],
              'scope': 'entire continuous recorded episode, first frame through last frame',
              'time_basis': source.get('time_basis'), 'excluded': source.get('excluded'),
              'model_wait_time_included': False, 'spectator_model_visible': False,
              'synthesized_motion': False, 'trimmed': False}
    write_json(record_path, record)
    temporary = run_dir / 'spectator.encoding.mp4'
    command = [ffmpeg_executable(), '-v', 'error', '-nostdin', '-y', '-threads', '2',
               '-i', str(input_path), '-map', '0:v:0', '-an',
               '-vf', f'crop={size}:{size}:{x}:{y},setpts=PTS-STARTPTS',
               '-vsync', '0', '-c:v', 'libx264', '-threads', '2',
               '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart', str(temporary)]
    try:
        with (run_dir / 'spectator_encode.log').open('wb') as log:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                           stderr=log, timeout=600)
        probe = json.loads(subprocess.check_output(
            ['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-of', 'json',
             str(temporary)], text=True, timeout=120))['streams'][0]
        frame_count = int(probe['nb_read_frames'])
        duration = float(probe['duration'])
        checks = {'every_source_frame_exported': frame_count == source['frame_count'],
                  'full_duration': abs(duration - source['duration_seconds']) <= 1 / source['fps'] + .002,
                  'full_camera_extent': (probe['width'], probe['height']) == (size, size),
                  'browser_codec': probe['codec_name'] == 'h264' and probe['pix_fmt'] == 'yuv420p'}
        if not all(checks.values()):
            raise RuntimeError('Spectator export validation failed: ' + str(checks))
        temporary.replace(output)
        record.update(status='passed', finished_at=now(), checks=checks,
                      frame_count=frame_count, duration_seconds=duration, fps=source['fps'],
                      width=size, height=size, bytes=output.stat().st_size,
                      sha256=file_hash(output))
    except Exception as exc:
        record.update(status='failed', finished_at=now(), failure_type=type(exc).__name__, failure=str(exc))
        write_json(record_path, record)
        raise
    write_json(record_path, record)
    return record


def export_spectator_walltime(run_dir: Path, controller_dir: Path, fps=15):
    """Keep real waiting intervals using timestamped frames, without inventing motion.

    The untrimmed simulation-time file retains every source frame. This viewing
    edition samples that evidence at a fixed playback rate and holds the last
    recorded frame during model/network waits.
    """
    run_dir, controller_dir = Path(run_dir), Path(controller_dir)
    source = export_spectator_video(run_dir)
    controller = json.loads((controller_dir / 'controller.json').read_text())
    run = json.loads((run_dir / 'run.json').read_text())
    epoch = lambda value: datetime.fromisoformat(value).timestamp()
    start = run.get('execution_started_at_unix') or epoch(controller['started_at'])
    end = epoch(controller['finished_at'])
    frames = [json.loads(line) for line in (run_dir / 'video_frames.jsonl').read_text().splitlines()]
    times = [max(0., epoch(frame['at']) - start) for frame in frames]
    if (len(times) != source['frame_count'] or not times or
            any(b < a for a, b in zip(times, times[1:]))):
        raise ValueError('Wall-time export requires an ordered timestamp for every source frame')
    duration = max(end - start, times[-1])
    if duration <= 0 or fps <= 0:
        raise ValueError('Invalid wall-time interval')
    record_path = run_dir / 'spectator_walltime_video.json'
    output = run_dir / 'spectator_walltime.mp4'
    if record_path.exists():
        old = json.loads(record_path.read_text())
        if (old.get('status') == 'passed' and old.get('source_sha256') == source['sha256']
                and output.is_file() and file_hash(output) == old.get('sha256')):
            return old
        raise FileExistsError('Preserve the previous wall-time export and validation')
    count = math.ceil(duration * fps)
    record = {'status': 'running', 'started_at': now(), 'file': output.name,
              'exporter_sha256': file_hash(Path(__file__)),
              'source_file': source['file'], 'source_sha256': source['sha256'],
              'source_frame_count': len(frames), 'fps': fps, 'width': source['width'],
              'height': source['height'], 'execution_start_unix': start,
              'target_duration_seconds': duration, 'model_wait_time_included': True,
              'spectator_model_visible': False, 'synthesized_motion': False, 'trimmed': False,
              'scope': 'execution clock start to recorded controller completion; simulator initialization excluded',
              'timing': 'actual frame-record timestamps; latest recorded frame held during gaps; not pure model inference latency',
              'sampling': 'fixed playback rate; all source frames remain in spectator.mp4',
              'input_hashes': {name: file_hash(run_dir / name) for name in ('run.json', 'video_frames.jsonl')},
              'controller_sha256': file_hash(controller_dir / 'controller.json')}
    write_json(record_path, record)
    temporary = run_dir / 'spectator_walltime.encoding.mp4'
    log = (run_dir / 'spectator_walltime_encode.log').open('wb')
    decoder = encoder = None
    try:
        decoder = subprocess.Popen([ffmpeg_executable(), '-v', 'error', '-threads', '2',
            '-i', str(run_dir / source['file']), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
            stdout=subprocess.PIPE, stderr=log)
        encoder = subprocess.Popen([ffmpeg_executable(), '-v', 'error', '-y',
            '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size',
            f"{source['width']}x{source['height']}", '-framerate', str(fps), '-i', 'pipe:0',
            '-an', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '20',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(temporary)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        frame_bytes = source['width'] * source['height'] * 3
        index, pixels, used = -1, None, set()
        def read_frame():
            payload = decoder.stdout.read(frame_bytes)
            if len(payload) != frame_bytes:
                raise RuntimeError('Spectator decoder ended before its frame ledger')
            return payload
        for n in range(count):
            target = max(0, bisect.bisect_right(times, n / fps) - 1)
            while index < target:
                pixels = read_frame(); index += 1
            encoder.stdin.write(pixels)
            used.add(index)
        while index < len(frames) - 1:
            read_frame(); index += 1
        if decoder.stdout.read(1):
            raise RuntimeError('Spectator decoder contains unledgered frames')
        encoder.stdin.close()
        if encoder.wait(timeout=120) or decoder.wait(timeout=30):
            raise RuntimeError('Wall-time encoder or decoder failed; see retained log')
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
            '-show_streams', '-of', 'json', str(temporary)], text=True, timeout=120))['streams'][0]
        actual = float(probe['duration'])
        checks = {'full_execution_duration': abs(actual - duration) <= 1 / fps + .002,
                  'encoded_frame_count': int(probe['nb_read_frames']) == count,
                  'all_source_frames_decoded': index + 1 == len(frames),
                  'browser_codec': probe['codec_name'] == 'h264' and probe['pix_fmt'] == 'yuv420p'}
        if not all(checks.values()):
            raise RuntimeError('Wall-time validation failed: ' + str(checks))
        temporary.replace(output)
        record.update(status='passed', finished_at=now(), frame_count=count,
                      source_frames_displayed=len(used), duration_seconds=actual,
                      checks=checks, bytes=output.stat().st_size, sha256=file_hash(output))
    except BaseException as exc:
        record.update(status='failed', finished_at=now(), failure_type=type(exc).__name__, failure=str(exc))
        write_json(record_path, record)
        raise
    finally:
        for process in (decoder, encoder):
            if process is not None and process.poll() is None:
                process.kill(); process.wait()
        if decoder is not None and decoder.stdout is not None:
            decoder.stdout.close()
        if encoder is not None and encoder.stdin is not None and not encoder.stdin.closed:
            encoder.stdin.close()
        log.close()
    write_json(record_path, record)
    return record
