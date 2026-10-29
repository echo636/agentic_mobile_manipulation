"""Actual CPU ffmpeg transport test; no robot/task-success claim."""
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
from manipulation_agent.video import EpisodeVideo

output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=False)
video=EpisodeVideo(output,fps=30,size=64)
pixels={name:np.zeros((64,64,3),dtype=np.uint8) for name in video.VIEWS}
video.append(pixels,0,'observation_boundary')
video.mark('act',{'primitive':'toggle_on'},'request1')
for i in range(1,31):
    pixels['head'][:,:,1]=i*8
    video.append(pixels,i,'env_step')
manifest=video.finish(30)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(output/'episode.mp4')],text=True))
stream=probe['streams'][0]
assert manifest['every_env_step_recorded']
assert int(stream['nb_read_frames'])==31
assert stream['codec_name']=='h264' and stream['pix_fmt']=='yuv420p'
subprocess.run(['ffmpeg','-v','error','-i',str(output/'episode.mp4'),'-f','null','-'],check=True)
(output/'validation.json').write_text(json.dumps({'status':'passed','level':'synthetic_rgb_encoder_only','frames':31,'codec':stream['codec_name'],'pix_fmt':stream['pix_fmt']},indent=2)+'\n')
print((output/'validation.json').read_text())
