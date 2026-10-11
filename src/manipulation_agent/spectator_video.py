"""Export every recorded third-person frame without resimulation or trimming."""
import json
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
