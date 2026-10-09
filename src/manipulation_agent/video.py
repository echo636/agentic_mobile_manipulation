"""Offline RGB recording at every env.step, with an auditable frame timeline.

No simulator dependency here. Encoding runs in a project-owned ffmpeg subprocess;
frames are streamed rather than accumulated in memory. No synthesized motion.
"""
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from collections import deque

from .records import now, write_json

ENCODER_STALL_SECONDS = 90


def ffmpeg_executable():
    """Use the system encoder or the pinned imageio package's bundled binary."""
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError('Recording requires ffmpeg or imageio-ffmpeg') from exc


class EpisodeVideo:
    VIEWS = ('head', 'spectator', 'left_wrist', 'right_wrist')

    def __init__(self, output: Path, fps: float, size=512, views=None, demo_motion=False):
        self.output = output
        self.views = tuple(views or self.VIEWS)
        if self.views not in {self.VIEWS, ('front','back','left','right','spectator')}:
            raise ValueError('Unsupported recording layout')
        self.size = size; self.fps = float(fps)
        self.demo_motion = bool(demo_motion)
        self.rows = (len(self.views)+1)//2
        self.width = 2 * size; self.height = self.rows * size + 80
        self.count = 0; self.env_steps = []; self.markers = []; self.context = {}
        self.closed = False; self.process = None; self.log = None
        self.encoder_pool = None; self.encoder_pending = deque()
        self._encoder_stop = threading.Event()
        self._encoded_frames = 0
        self.recording_failure = None
        self._font = None
        self._camera_canvas = None
        self._camera_hashes = None
        self._camera_pixels = None
        self.fresh_frames = 0; self.held_frames = 0
        self.manifest = {'status':'running','file':'episode.mp4','poster':'video_poster.jpg',
                         'fps':self.fps,'width':self.width,'height':self.height,
                         'started_at':now(),'frame_count':0,'scope':'every_env_step_plus_observation_boundaries',
                         'spectator_model_visible':False, 'synthesized_motion':False,
                         'demo_motion':self.demo_motion,
                         'time_basis':'simulation control steps; model wait time omitted; boundary captures add one frame',
                         'excluded':'physics substeps and private volume-sampler candidate-search ticks',
                         'views':list(self.views),'markers':self.markers}
        write_json(output/'video.json', self.manifest)

    def mark(self, name, arguments, request_id):
        if self.closed: return
        self.context = {'tool':name,'primitive':arguments.get('primitive', name),'request_id':request_id}
        self.markers.append({**self.context,'frame_index':self.count,'seconds':self.count/self.fps})

    def _write_frame(self, raw):
        """Worker-only pipe I/O; no simulator or camera access here."""
        fd = self.process.stdin.fileno()
        remaining = memoryview(raw)
        last_progress = time.monotonic()
        while remaining:
            if self._encoder_stop.is_set():
                raise OSError('Encoder recording stopped')
            idle = time.monotonic() - last_progress
            if idle >= ENCODER_STALL_SECONDS:
                raise TimeoutError('Encoder pipe made no write progress within its stall limit')
            if not select.select([], [fd], [], min(.05, ENCODER_STALL_SECONDS-idle))[1]:
                continue
            try:
                written = os.write(fd, remaining)
            except BlockingIOError:
                continue
            if written <= 0:
                raise BrokenPipeError('Encoder pipe accepted no bytes')
            remaining = remaining[written:]
            last_progress = time.monotonic()
        self._encoded_frames += 1

    def _disable_encoder(self, exc):
        """Isolate failed recording and keep already written evidence intact."""
        self.closed = True
        self.recording_failure = {'failure_type':type(exc).__name__, 'failure':str(exc)}
        self._encoder_stop.set()
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.manifest['encoder_cleanup_pending'] = True
        if self.encoder_pool is not None:
            # The writer polls stop every 50ms and never uses blocking writes.
            # Join before closing the fd, so it cannot write to a reused number.
            self.encoder_pool.shutdown(wait=True, cancel_futures=True)
        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()
        if self.log is not None:
            self.log.close()
        self.manifest.update(status='failed', recording_disabled=True,
            finished_at=now(), frame_count=self.count, recorded_env_steps=len(self.env_steps),
            encoded_frames_completed=self._encoded_frames, every_env_step_recorded=False,
            encoder_exit_code=self.process.poll() if self.process is not None else None,
            markers=self.markers, **self.recording_failure)
        write_json(self.output/'video.json', self.manifest)

    def append(self, pixels, env_step: int, kind: str, *, capture_env_step=None, repeated=False,
               check_active=None):
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        if self.closed: return
        if set(pixels) != set(self.views):
            raise ValueError('Continuous recorder requires its configured RGB views')
        tiles = {name: np.asarray(pixels[name], dtype=np.uint8) for name in self.views}
        if any(a.shape != (self.size, self.size, 3) for a in tiles.values()):
            raise ValueError('Invalid video camera shape')
        if self._font is None:
            font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
            self._font = ImageFont.truetype(str(font_path), 16) if font_path.exists() else ImageFont.load_default()
        font = self._font
        # An explicit frame hold reuses immutable camera arrays. Only the
        # per-control-step annotation changes; hashes still describe raw RGB.
        if not (repeated and pixels is self._camera_pixels and self._camera_canvas is not None):
            self._camera_canvas = Image.new('RGB', (self.width, self.height), '#102637')
            draw = ImageDraw.Draw(self._camera_canvas)
            for i, name in enumerate(self.views):
                x,y=(i%2)*self.size,(i//2)*self.size
                self._camera_canvas.paste(Image.fromarray(tiles[name]), (x,y))
                draw.rectangle((x,y,x+self.size,y+25), fill='#102637')
                label = 'SPECTATOR - replay only' if name == 'spectator' else 'ROBOT RGB - '+name
                draw.text((x+8,y+3),label,fill='white',font=font)
            self._camera_hashes = {k:hashlib.sha256(v.tobytes()).hexdigest() for k,v in tiles.items()}
            self._camera_pixels = pixels
        canvas = self._camera_canvas.copy()
        draw = ImageDraw.Draw(canvas)
        title = f"env.step {env_step} | capture {env_step if capture_env_step is None else capture_env_step} | {self.context.get('primitive','initial RGB')} | {kind}"
        draw.text((12,self.rows*self.size+7),title,fill='white',font=font)
        caption = ('Demo motor: recorded arm motion; contact and final state remain idealized.'
                   if self.demo_motion else
                   'Ideal executor: instantaneous pose/state changes are recorded as executed.')
        draw.text((12,self.rows*self.size+32),caption,fill='#b6d6df',font=font)
        draw.text((12,self.rows*self.size+55),'Control-step timeline; labeled frame holds between captures. No motion interpolation.',fill='#b6d6df',font=font)
        if self.process is None:
            ffmpeg = ffmpeg_executable()
            self.log = (self.output/'video_encoder.log').open('wb')
            command = [ffmpeg,'-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pixel_format','rgb24',
                       '-video_size',f'{self.width}x{self.height}','-framerate',str(self.fps),'-i','pipe:0',
                       '-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20',
                       '-pix_fmt','yuv420p','-g',str(max(1,round(self.fps))),
                       '-movflags','+frag_keyframe+empty_moov+default_base_moof',str(self.output/'episode.mp4')]
            try:
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                                stderr=self.log, bufsize=0)
                os.set_blocking(self.process.stdin.fileno(), False)
            except OSError as exc:
                self._disable_encoder(exc)
                return
            self.encoder_pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='mas-video-encoder')
            self.manifest['encoder_pid'] = self.process.pid
            self.manifest['encoder_command'] = command
            canvas.save(self.output/'video_poster.jpg',quality=90)
        raw = np.asarray(canvas).tobytes()
        # Preserve order and bound queued memory to eight raw frames. Worker
        # touches only ffmpeg's pipe, never the simulator or camera tensors.
        while self.encoder_pending and (len(self.encoder_pending)>=8 or self.encoder_pending[0].done()):
            pending = self.encoder_pending[0]
            while not pending.done():
                # Backpressure remains bounded in memory, but closure can
                # interrupt it without waiting for the encoder's 90s watchdog.
                if check_active is not None: check_active()
                wait([pending], timeout=.05)
            self.encoder_pending.popleft()
            try:
                pending.result()
            except (OSError, TimeoutError) as exc:
                self._disable_encoder(exc)
                return
        self.encoder_pending.append(self.encoder_pool.submit(self._write_frame, raw))
        self.held_frames+=int(repeated);self.fresh_frames+=int(not repeated)
        row = {'frame_index':self.count,'video_seconds':self.count/self.fps,'env_step':env_step,
               'kind':kind, 'at':now(), 'camera_capture_env_step':env_step if capture_env_step is None else capture_env_step,
               'repeated_camera_frame':bool(repeated), **self.context,
               'rgb_sha256':dict(self._camera_hashes)}
        with (self.output/'video_frames.jsonl').open('a') as stream:
            stream.write(json.dumps(row)+'\n')
        if kind == 'env_step': self.env_steps.append(env_step)
        self.count += 1
        # Preserve completed fragments and a progress checkpoint after a native
        # simulator crash. Only finish() can mark a recording complete.
        if self.count == 1 or self.count % 60 == 0:
            self.manifest.update(frame_count=self.count, recorded_env_steps=len(self.env_steps),
                                 checkpoint_at=now(), markers=self.markers)
            write_json(self.output/'video.json', self.manifest)

    def finish(self, final_env_step):
        if self.recording_failure is not None:
            self.manifest.update(final_env_step=final_env_step,
                encoded_frames_completed=self._encoded_frames)
            path = self.output/'episode.mp4'
            if path.exists():
                self.manifest.update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            write_json(self.output/'video.json', self.manifest)
            return self.manifest
        if self.closed: return self.manifest
        self.closed = True
        if self.process:
            try:
                while self.encoder_pending:self.encoder_pending.popleft().result(timeout=ENCODER_STALL_SECONDS)
                self.process.stdin.close()
                code = self.process.wait(timeout=ENCODER_STALL_SECONDS)
            except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
                self._disable_encoder(exc)
                return self.finish(final_env_step)
            finally:
                if self.encoder_pool:self.encoder_pool.shutdown(wait=True,cancel_futures=True)
                self.log.close()
        else: code = None
        complete = self.env_steps == list(range(1,final_env_step+1))
        valid = code == 0 and self.count > 0 and complete
        self.manifest.update(status='passed' if valid else 'failed',finished_at=now(),frame_count=self.count,
                             duration_seconds=self.count/self.fps,final_env_step=final_env_step,
                             recorded_env_steps=len(self.env_steps),every_env_step_recorded=complete,
                             encoder_exit_code=code,markers=self.markers,fresh_camera_frames=self.fresh_frames,repeated_camera_frames=self.held_frames,
                             capture_policy='control-step CFR; explicit previous-frame hold between fresh captures; fresh action observation boundaries')
        path = self.output/'episode.mp4'
        if path.exists():
            self.manifest.update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        write_json(self.output/'video.json',self.manifest)
        if not valid: raise RuntimeError('Continuous video recording is incomplete; inspect video.json')
        return self.manifest
