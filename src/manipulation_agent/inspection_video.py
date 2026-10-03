"""Readable viewing edition from archived frames, never resimulation.

Motor footage is retained at 1x simulation time. Reading holds paginate all
public assistant text and provider summaries; they are editorial, not inference
latency. The spectator camera is replay-only. Original artifacts are untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from .records import now, write_json
from .replay import build_replay

CAMERA_BOXES = {
    'front': (16, 54, 660),
    'back': (692, 54, 216),
    'left': (692, 288, 216),
    'right': (692, 522, 216),
    'spectator': (16, 756, 300),
}


def wrap_verbatim(text, width, measure):
    """Soft-wrap without dropping spaces, characters, or original newlines."""
    lines = []; line = ''; used = 0
    for char in text:
        size = measure(char) if char != '\n' else 0
        if char != '\n' and line and used + size > width:
            lines.append(line); line = ''; used = 0
        line += char; used += size
        if char == '\n':
            lines.append(line); line = ''; used = 0
    if line:
        lines.append(line)
    return lines or ['']


def paginate(text, width, measure, lines_per_page=24):
    lines = wrap_verbatim(text, width, measure)
    pages = [lines[i:i + lines_per_page] for i in range(0, len(lines), lines_per_page)]
    assert ''.join(''.join(page) for page in pages) == text
    return pages


def decision_text(step):
    sections = []
    for message in step.get('model_reasoning_summaries', []):
        sections.append('PROVIDER REASONING SUMMARY (verbatim)\n' + message['text'])
    for message in step.get('model_messages', []):
        sections.append('ASSISTANT OUTPUT (verbatim)\n' + message['text'])
    if not sections:
        sections.append('No new assistant text or provider summary recorded before this call.')
    sections.append('TOOL CALL\n' + json.dumps(
        {'tool': step['tool'], 'arguments': step['arguments']}, ensure_ascii=False, indent=2))
    return '\n\n'.join(sections)


def source_markers(steps, raw):
    """Keep post-close acknowledgments visible using an explicit last-frame hold."""
    markers = {m['request_id']: m['frame_index'] for m in raw['markers']}
    holds = []
    for i, step in enumerate(steps):
        if step['request_id'] in markers:
            continue
        before, after = step.get('before'), step.get('after')
        try:
            after_recording = datetime.fromisoformat(step['at']) > datetime.fromisoformat(raw['finished_at'])
        except (KeyError, TypeError, ValueError):
            after_recording = False
        closed_boundary = any(
            previous['tool'] == 'finish' and (previous.get('result') or {}).get('closed') is True
            and markers.get(previous['request_id']) == raw['frame_count']
            for previous in steps[:i])
        if not (step['tool'] == 'finish' and after_recording and closed_boundary
                and isinstance(raw.get('final_env_step'), int) and raw['frame_count'] > 0
                and (step.get('result') or {}).get('error', {}).get('code') == 'episode_closed'
                and before and before == after
                and before.get('capture', {}).get('sim_step') == raw.get('final_env_step')
                and before.get('images')
                and all(image.get('env_steps') == raw.get('final_env_step') for image in before['images'])
                and all(later['request_id'] not in markers for later in steps[i + 1:])):
            raise ValueError('Missing source video marker')
        markers[step['request_id']] = raw['frame_count']
        holds.append({'request_id': step['request_id'], 'step': step['index'],
            'source_frame': raw['frame_count'] - 1, 'source_env_step': raw['final_env_step'],
            'reason': 'finish returned episode_closed after recording ended; unchanged final observation',
            'synthetic_motion': False})
    return markers, holds


def build(run_dir: Path, controller_dir: Path | None = None, chars_per_second=24):
    from PIL import Image, ImageDraw
    from .explained_video import get_font, char_width

    if chars_per_second <= 0:
        raise ValueError('Reading speed must be positive')
    data = build_replay(run_dir, controller_dir); raw = data['video']
    if not raw or raw['status'] != 'passed':
        raise ValueError('A verified original video is required')
    if raw.get('views') != ['front', 'back', 'left', 'right', 'spectator']:
        raise ValueError('Requires archived four-direction RGB plus spectator')
    output = run_dir / 'inspection.mp4'; record = run_dir / 'inspection_video.json'
    if output.exists() or record.exists():
        raise FileExistsError('Preserve existing editions; use a new output directory')
    with (run_dir / raw['file']).open('rb') as f:
        source_hash = hashlib.file_digest(f, 'sha256').hexdigest()
    if source_hash != raw['sha256']:
        raise ValueError('Source video hash mismatch')
    steps = data['steps']; fps = raw['fps']; tile_size = raw['width'] // 2
    markers, post_recording_holds = source_markers(steps, raw)
    post_recording_ids = {hold['request_id'] for hold in post_recording_holds}
    manifest = {
        'status': 'running', 'started_at': now(), 'host': platform.node(),
        'pid': os.getpid(), 'interpreter': sys.executable, 'gpu_uuid': None,
        'unit': os.environ.get('MAS_UNIT'), 'file': output.name,
        'poster': 'inspection_poster.jpg', 'width': 1920, 'height': 1080, 'fps': fps,
        'source_file': raw['file'], 'source_sha256': source_hash,
        'source_frames': raw['frame_count'], 'motion_speed_vs_source': 1,
        'camera_boxes': CAMERA_BOXES, 'spectator_model_visible': False,
        'time_basis': '1x simulation frames plus editorial reading holds; not wall-clock latency',
        'reading_chars_per_second': chars_per_second, 'minimum_reading_hold_seconds': 5,
        'public_text_paginated_without_truncation': True,
        'reasoning_availability': data['reasoning_availability'],
        'text_contract': 'Only recorded public assistant output and provider-returned summaries, verbatim; no reconstructed hidden reasoning',
        'tool_result_scope': 'Video shows status/error/effect/job; full result and images are in synchronized HTML',
        'synthesized_motion': False, 'raw_evidence_modified': False,
        'post_recording_holds': post_recording_holds,
        'renderer_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    write_json(record, manifest)
    decode_log = (run_dir / 'inspection_decode.log').open('wb')
    encode_log = (run_dir / 'inspection_encode.log').open('wb')
    decoder = subprocess.Popen(['ffmpeg', '-v', 'error', '-threads', '2', '-i',
        str(run_dir / raw['file']), '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=decode_log)
    encoder = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo',
        '-pix_fmt', 'rgb24', '-s', '1920x1080', '-r', str(fps), '-i', 'pipe:0',
        '-an', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '22',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)],
        stdin=subprocess.PIPE, stderr=encode_log)
    source_count = out_count = 0; last = None; ledger = []; text_ledger = []; spectator_images = {}
    frame_size = raw['width'] * raw['height'] * 3

    def panel(step, phase, lines, page_no=1, page_total=1):
        img = Image.new('RGB', (1920, 1080), '#101d2b'); d = ImageDraw.Draw(img)
        d.rectangle((1000, 0, 1919, 1079), fill='#14283a')
        d.text((16, 10), 'FRONT RGB + SURROUND + THIRD PERSON', font=get_font(22), fill='#80d8cf')
        title = f'Step {step["index"]}/{len(steps)}' if step else 'Episode'
        d.text((1024, 26), title + ' | ' + phase.upper(), font=get_font(28), fill='#80d8cf')
        d.text((1024, 74), f'Page {page_no}/{page_total} | verbatim recorded text', font=get_font(20), fill='#e2c28c')
        for n, line in enumerate(lines):
            d.text((1024, 120 + n * 35), line.rstrip('\n'), font=get_font(24), fill='#eef3f8')
        notes = ['Spectator: replay only', 'Motion: 1x simulation time', 'Reading holds: editorial time',
                 'No interpolated motion', 'Full tool result: HTML replay']
        for i, note in enumerate(notes):
            d.text((342, 796 + i * 43), note, font=get_font(22), fill='#b7cbd9')
        note = ('Recording ended | last recorded frame held' if step and
                step['request_id'] in post_recording_ids else 'Pause to inspect | model/network waiting omitted')
        d.text((1024, 1020), note, font=get_font(21), fill='#9cb8c9')
        return img

    def composite(base, frame):
        img = base.copy(); d = ImageDraw.Draw(img)
        for view, (x, y, size) in CAMERA_BOXES.items():
            i = raw['views'].index(view)
            left = (i % 2) * tile_size; top = (i // 2) * tile_size
            crop = frame.crop((left, top, left + tile_size, top + tile_size))
            img.paste(crop.resize((size, size), Image.Resampling.BILINEAR), (x, y))
            d.rectangle((x, y, x + size, y + 26), fill='#14283a')
            label = 'THIRD PERSON | replay only' if view == 'spectator' else view.upper() + ' RGB'
            d.text((x + 5, y + 2), label, font=get_font(16), fill='white')
        return img

    def emit(base, count=1):
        nonlocal out_count
        if last is None:
            raise ValueError('No preceding source frame for reading hold')
        img = composite(base, last)
        if out_count == 0:
            img.save(run_dir / 'inspection_poster.jpg', quality=92)
        buf = img.tobytes()
        for _ in range(count):
            encoder.stdin.write(buf)
        out_count += count

    def entry(step, phase, begin, **extra):
        if step and step['request_id'] in post_recording_ids:
            extra.update(post_recording=True, footage_scope='last recorded frame; no new execution footage')
        ledger.append({'step': step['index'] if step else None,
            'request_id': step.get('request_id') if step else None,
            'phase': phase, 'start_frame': begin, 'end_frame_exclusive': out_count,
            'start_seconds': begin / fps, 'end_seconds': out_count / fps, **extra})

    def motion(step, end, phase='execution'):
        nonlocal source_count, last
        if end < source_count:
            raise ValueError('Non-monotonic source markers')
        begin = out_count; start_source = source_count
        action = step['arguments'].get('primitive', step['tool']) if step else 'Initial scene'
        text = action + '\n\nActual recorded simulation frames.\nTool result follows after execution.'
        base = panel(step, phase, wrap_verbatim(text, 862, lambda c: char_width(c, 24)))
        while source_count < end:
            buf = decoder.stdout.read(frame_size)
            if len(buf) != frame_size:
                raise RuntimeError('Original video ended before its recorded frame count')
            last = Image.frombytes('RGB', (raw['width'], raw['height']), buf)
            emit(base); source_count += 1
        if out_count > begin:
            entry(step, phase, begin, source_start_frame=start_source,
                  source_end_frame_exclusive=source_count, repeated_frame_hold=False)

    def reading(step, phase, text):
        if step and phase in {'decision', 'result'}:
            folder = run_dir / 'inspection_frames'; folder.mkdir(exist_ok=True)
            side = 'before' if phase == 'decision' else 'after'
            path = folder / f'step_{step["index"]:04d}_{side}_spectator.jpg'
            i = raw['views'].index('spectator'); x = (i % 2) * tile_size; y = (i // 2) * tile_size
            last.crop((x, y, x + tile_size, y + tile_size)).save(path, quality=92)
            spectator_images.setdefault(str(step['index']), {})[side] = {
                'file': str(path.relative_to(run_dir)), 'source_frame': source_count - 1,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'model_visible': False,
                'source': 'archived composite video boundary, not a model observation',
            }
        pages = paginate(text, 862, lambda c: char_width(c, 24))
        text_id = len(text_ledger)
        text_ledger.append({'step': step['index'] if step else None, 'phase': phase,
                           'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                           'pages': [''.join(page) for page in pages]})
        for n, lines in enumerate(pages):
            begin = out_count; duration = max(5, len(''.join(lines)) / chars_per_second)
            emit(panel(step, phase, lines, n + 1, len(pages)), max(1, round(duration * fps)))
            entry(step, phase, begin, source_start_frame=source_count - 1,
                  source_end_frame_exclusive=source_count, repeated_frame_hold=True,
                  text_id=text_id, page=n + 1, pages=len(pages))

    try:
        first = markers[steps[0]['request_id']] if steps else raw['frame_count']
        motion(None, first, 'initial')
        if last is None:
            raise ValueError('No initial frame before first tool; cannot invent a pre-action view')
        for i, step in enumerate(steps):
            if source_count != markers[step['request_id']]:
                raise ValueError('Source marker alignment failed')
            reading(step, 'decision', decision_text(step))
            end = markers[steps[i + 1]['request_id']] if i + 1 < len(steps) else raw['frame_count']
            motion(step, end)
            result = step.get('result') or {'missing_result': True}
            selected = {k: result[k] for k in ['ok', 'error', 'effect', 'closed', 'job', 'missing_result'] if k in result}
            reading(step, 'result', 'TOOL RESULT\n' + json.dumps(selected, ensure_ascii=False, indent=2)
                    + '\n\nFull returned data and observation images are available in the HTML replay.')
        tail = []
        for m in data.get('model_final_reasoning_summaries', []):
            tail.append('PROVIDER REASONING SUMMARY (verbatim)\n' + m['text'])
        for m in data.get('model_messages_after_last_sim_call', []) + data.get('model_final_messages', []):
            tail.append('ASSISTANT OUTPUT (verbatim)\n' + m['text'])
        if tail:
            reading(None, 'final', '\n\n'.join(tail))
        reading(None, 'evaluation', 'INDEPENDENT EVALUATION (not visible to the model)\n\n' + json.dumps(
            {'task_success': data['evaluation_offline_only']['task_success'],
             'agent_outcome': data['evaluation_offline_only']['agent_outcome'],
             'q_score': (data['evaluation_offline_only'].get('evaluation') or {}).get('goal_satisfaction_fraction')},
            ensure_ascii=False, indent=2))
        if source_count != raw['frame_count'] or decoder.stdout.read(1):
            raise RuntimeError('Source frame coverage mismatch')
        encoder.stdin.close()
        if encoder.wait(timeout=120) or decoder.wait(timeout=30):
            raise RuntimeError('ffmpeg failed')
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
            '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames,width,height',
            '-of', 'json', str(output)], text=True))['streams'][0]
        if int(probe['nb_read_frames']) != out_count:
            raise RuntimeError('Encoded frame count mismatch')
        with output.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        manifest.update(status='passed', finished_at=now(), frame_count=out_count,
            duration_seconds=out_count / fps, source_frames_read=source_count,
            all_source_frames_in_order=True, all_tool_calls_represented=all(
                any(s['request_id'] == step['request_id'] and s['phase'] == 'decision' for s in ledger)
                for step in steps), bytes=output.stat().st_size, sha256=digest,
            segments=ledger, text_pages=text_ledger, spectator_images=spectator_images)
        write_json(record, manifest)
        return manifest
    except BaseException as exc:
        manifest.update(status='failed', finished_at=now(), error=str(exc))
        write_json(record, manifest)
        raise
    finally:
        for process in (decoder, encoder):
            if process.poll() is None:
                process.kill(); process.wait()
        decode_log.close(); encode_log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--controller-dir', type=Path)
    parser.add_argument('--chars-per-second', type=float, default=24)
    args = parser.parse_args()
    result = build(args.run_dir, args.controller_dir, args.chars_per_second)
    print(json.dumps({k: result[k] for k in ['status', 'duration_seconds', 'frame_count']}))
