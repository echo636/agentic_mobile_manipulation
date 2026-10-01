"""Offline RGB recording at every env.step, with an auditable frame timeline.

No simulator dependency here. Encoding runs in a project-owned ffmpeg subprocess;
frames are streamed rather than accumulated in memory. No synthesized motion.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections import deque

from .records import now, write_json


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

    def __init__(self, output: Path, fps: float, size=512, views=None):
        self.output = output
        self.views = tuple(views or self.VIEWS)
        if self.views not in {self.VIEWS, ('front','back','left','right','spectator')}:
            raise ValueError('Unsupported recording layout')
        self.size = size; self.fps = float(fps)
        self.rows = (len(self.views)+1)//2
        self.width = 2 * size; self.height = self.rows * size + 80
        self.count = 0; self.env_steps = []; self.markers = []; self.context = {}
        self.closed = False; self.process = None; self.log = None
        self.encoder_pool = None; self.encoder_pending = deque()
        self.fresh_frames = 0; self.held_frames = 0
        self.manifest = {'status':'running','file':'episode.mp4','poster':'video_poster.jpg',
                         'fps':self.fps,'width':self.width,'height':self.height,
                         'started_at':now(),'frame_count':0,'scope':'every_env_step_plus_observation_boundaries',
                         'spectator_model_visible':False, 'synthesized_motion':False,
                         'time_basis':'simulation control steps; model wait time omitted; boundary captures add one frame',
                         'excluded':'physics substeps and private volume-sampler candidate-search ticks',
                         'views':list(self.views),'markers':self.markers}
        write_json(output/'video.json', self.manifest)

    def mark(self, name, arguments, request_id):
        if self.closed: return
        self.context = {'tool':name,'primitive':arguments.get('primitive', name),'request_id':request_id}
        self.markers.append({**self.context,'frame_index':self.count,'seconds':self.count/self.fps})

    def append(self, pixels, env_step: int, kind: str, *, capture_env_step=None, repeated=False):
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        if self.closed: return
        if set(pixels) != set(self.views):
            raise ValueError('Continuous recorder requires its configured RGB views')
        tiles = {name: np.asarray(pixels[name], dtype=np.uint8) for name in self.views}
        if any(a.shape != (self.size, self.size, 3) for a in tiles.values()):
            raise ValueError('Invalid video camera shape')
        canvas = Image.new('RGB', (self.width, self.height), '#102637')
        draw = ImageDraw.Draw(canvas)
        font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        font = ImageFont.truetype(str(font_path), 16) if font_path.exists() else ImageFont.load_default()
        for i, name in enumerate(self.views):
            x,y=(i%2)*self.size,(i//2)*self.size
            canvas.paste(Image.fromarray(tiles[name]), (x,y))
            draw.rectangle((x,y,x+self.size,y+25), fill='#102637')
            label = 'SPECTATOR - replay only' if name == 'spectator' else 'ROBOT RGB - '+name
            draw.text((x+8,y+3),label,fill='white',font=font)
        title = f"env.step {env_step} | capture {env_step if capture_env_step is None else capture_env_step} | {self.context.get('primitive','initial RGB')} | {kind}"
        draw.text((12,self.rows*self.size+7),title,fill='white',font=font)
        draw.text((12,self.rows*self.size+32),'Ideal executor: instantaneous pose/state changes are recorded as executed.',fill='#b6d6df',font=font)
        draw.text((12,self.rows*self.size+55),'Control-step timeline; labeled frame holds between captures. No motion interpolation.',fill='#b6d6df',font=font)
        if self.process is None:
            ffmpeg = ffmpeg_executable()
            self.log = (self.output/'video_encoder.log').open('wb')
            command = [ffmpeg,'-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pixel_format','rgb24',
                       '-video_size',f'{self.width}x{self.height}','-framerate',str(self.fps),'-i','pipe:0',
                       '-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20',
                       '-pix_fmt','yuv420p','-g',str(max(1,round(self.fps))),
                       '-movflags','+frag_keyframe+empty_moov+default_base_moof',str(self.output/'episode.mp4')]
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log)
            self.encoder_pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='mas-video-encoder')
            self.manifest['encoder_pid'] = self.process.pid
            self.manifest['encoder_command'] = command
            canvas.save(self.output/'video_poster.jpg',quality=90)
        raw = np.asarray(canvas).tobytes()
        # Preserve order and bound queued memory to eight raw frames. Worker
        # touches only ffmpeg's pipe, never the simulator or camera tensors.
        while self.encoder_pending and (len(self.encoder_pending)>=8 or self.encoder_pending[0].done()):
            self.encoder_pending.popleft().result(timeout=90)
        self.encoder_pending.append(self.encoder_pool.submit(self.process.stdin.write,raw))
        self.held_frames+=int(repeated);self.fresh_frames+=int(not repeated)
        row = {'frame_index':self.count,'video_seconds':self.count/self.fps,'env_step':env_step,
               'kind':kind, 'at':now(), 'camera_capture_env_step':env_step if capture_env_step is None else capture_env_step,
               'repeated_camera_frame':bool(repeated), **self.context,
               'rgb_sha256':{k:hashlib.sha256(v.tobytes()).hexdigest() for k,v in tiles.items()}}
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
        if self.closed: return self.manifest
        self.closed = True
        if self.process:
            try:
                while self.encoder_pending:self.encoder_pending.popleft().result(timeout=90)
                self.process.stdin.close()
                code = self.process.wait(timeout=90)
            except BaseException:
                self.process.kill();self.process.wait();raise
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
