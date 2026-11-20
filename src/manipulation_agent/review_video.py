"""Compact viewing edition. Original frames, model text and task scores stay intact."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys

from .records import now, write_json
from .replay import build_replay, render_replay


def frame_indices(begin, end, stride=2, motor=True):
    """Keep action endpoints; static tool intervals retain their latest frame."""
    if end <= begin:
        return []
    if not motor:
        return [end-1]
    return sorted(set(range(begin,end,stride)) | {end-1})


def build(run_dir, controller_dir=None, speed=2):
    from PIL import Image, ImageOps
    from .explained_video import make_panel
    data=build_replay(run_dir,controller_dir);raw=data['video']
    if not raw or raw['status']!='passed':raise ValueError('A verified source video is required')
    output=run_dir/'review.mp4'
    if output.exists():raise FileExistsError('Review edition already exists; preserve it or use a new output directory')
    fps=raw['fps'];steps=data['steps'];size=raw['width']*raw['height']*3
    run=json.loads((run_dir/'run.json').read_text())
    run['_final_model_messages']=data.get('model_final_messages',[])
    run['_video_note']=f'{speed}x simulation motion | waits compressed | real frames'
    manifest={'status':'running','started_at':now(),'host':platform.node(),'pid':os.getpid(),
      'interpreter':sys.executable,'gpu_uuid':None,'unit':os.environ.get('MAS_UNIT'),
      'renderer_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      'panel_source_sha256':hashlib.sha256(Path(__file__).with_name('explained_video.py').read_bytes()).hexdigest(),
      'file':output.name,'poster':'review_poster.jpg','fps':fps,'width':1280,'height':720,
      'source_sha256':raw['sha256'],'source_file':raw['file'],'source_frames':raw['frame_count'],
      'motion_speed_vs_source':speed,'model_wait':'omitted; bounded reading holds are editorial viewing time',
      'scope':'Every recorded tool call; motor frames subsampled, static intervals reduced to latest frame',
      'synthesized_motion':False,'raw_evidence_modified':False,
      'reasoning_availability':data['reasoning_availability'],
      'text_source':'Verbatim assistant output and provider-returned summaries; English renderer labels',
      'text_truncation':'Video panel may truncate long text; full verbatim text in HTML/JSON',
      'ffmpeg_version':subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0]}
    write_json(run_dir/'review_video.json',manifest)
    dec_log=(run_dir/'review_decode.log').open('wb');enc_log=(run_dir/'review_encoder.log').open('wb')
    decoder=subprocess.Popen(['ffmpeg','-v','error','-threads','2','-i',str(run_dir/raw['file']),
        '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE,stderr=dec_log)
    encoder=subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24',
        '-s','1280x720','-r',str(fps),'-i','pipe:0','-an','-c:v','libx264','-threads','2',
        '-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-movflags','+faststart',str(output)],
        stdin=subprocess.PIPE,stderr=enc_log)
    source_count=out_count=0;last=None;ledger=[];kept=set()
    def emit(panel, frame, count=1):
        nonlocal out_count
        canvas=panel.copy();canvas.paste(frame,(8,1));buf=canvas.tobytes()
        if out_count==0:canvas.save(run_dir/'review_poster.jpg',quality=92)
        for _ in range(count):encoder.stdin.write(buf)
        out_count+=count
    def segment(step, phase, end=None, hold=0, motor=True):
        nonlocal source_count,last
        start=out_count;begin=source_count
        panel=make_panel(step,phase,run,len(steps)).resize((1280,720),Image.Resampling.LANCZOS)
        selected=frame_indices(begin,end,speed,motor) if end is not None else []
        selected_set=set(selected)
        if end is not None:
            while source_count<end:
                buf=decoder.stdout.read(size)
                if len(buf)!=size:raise RuntimeError('Source ended before recorded frame count')
                if source_count in selected_set:
                    last=ImageOps.pad(Image.frombytes('RGB',(raw['width'],raw['height']),buf),
                        (667,718),method=Image.Resampling.BILINEAR,color='#101d2b')
                    emit(panel,last);kept.add(source_count)
                source_count+=1
        if hold and last is not None:emit(panel,last,max(1,round(hold*fps)))
        if out_count>start:
            ledger.append({'step':step['index'] if step else None,'request_id':step.get('request_id') if step else None,
                'phase':phase,'start_frame':start,'end_frame_exclusive':out_count,
                'start_seconds':start/fps,'end_seconds':out_count/fps,
                'source_start_frame':begin if end is not None else source_count-1,
                'source_end_frame_exclusive':source_count,'source_selected_frames':selected,
                'repeated_frame_hold':bool(hold),'tool_wall_seconds':step.get('tool_seconds') if step else None})
    markers={m['request_id']:m['frame_index'] for m in raw['markers']}
    try:
        first=markers[steps[0]['request_id']] if steps else raw['frame_count']
        segment(None,'initial',first,hold=.5,motor=False)
        for i,step in enumerate(steps):
            if source_count!=markers[step['request_id']]:raise RuntimeError('Non-monotonic source markers')
            end=markers[steps[i+1]['request_id']] if i+1<len(steps) else raw['frame_count']
            texts=step.get('model_messages',[])+step.get('model_reasoning_summaries',[])
            segment(step,'decision',hold=min(6,max(2,sum(len(t['text']) for t in texts)/60)) if texts else .2)
            segment(step,'execution',end,motor=step['is_motor_action'])
            segment(step,'result',hold=.8 if step['status']!='passed' else .3)
        if data.get('model_final_messages'):segment(None,'final',hold=3)
        segment(None,'evaluation',hold=2)
        if decoder.stdout.read(1):raise RuntimeError('Source has unrecorded extra frames')
        encoder.stdin.close()
        if encoder.wait(timeout=120) or decoder.wait(timeout=30):raise RuntimeError('ffmpeg failed')
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
            '-show_entries','stream=nb_read_frames,width,height','-of','json',str(output)],text=True))['streams'][0]
        if int(probe['nb_read_frames'])!=out_count:raise RuntimeError('Encoded frame count mismatch')
        with output.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        manifest.update(status='passed',finished_at=now(),frame_count=out_count,duration_seconds=out_count/fps,
            source_frames_read=source_count,source_frames_shown=len(kept),all_source_frames_once=False,
            all_tool_calls_represented=all(any(s['step']==x['index'] for s in ledger) for x in steps),
            bytes=output.stat().st_size,sha256=digest,segments=ledger)
        write_json(run_dir/'review_video.json',manifest);render_replay(run_dir,controller_dir)
        return manifest
    except BaseException as exc:
        manifest.update(status='failed',finished_at=now(),error=str(exc));write_json(run_dir/'review_video.json',manifest)
        raise
    finally:
        for proc in (decoder,encoder):
            if proc.poll() is None:proc.kill();proc.wait()
        dec_log.close();enc_log.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--controller-dir',type=Path);p.add_argument('--speed',type=int,choices=[2,3,4],default=2)
    args=p.parse_args();result=build(args.run_dir,args.controller_dir,args.speed)
    print(json.dumps({k:result[k] for k in ('status','duration_seconds','bytes')}))
