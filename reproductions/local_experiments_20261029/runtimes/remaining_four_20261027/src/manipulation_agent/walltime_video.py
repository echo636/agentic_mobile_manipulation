"""Real elapsed-time viewing edition; raw evidence and compact video stay immutable.

Tool boundaries and observation times are recorded timestamps. Control frames
inside a tool are spaced uniformly because the original recorder did not timestamp
each control frame. No motion interpolation or model reasoning is synthesized.
"""
from __future__ import annotations
import argparse
import bisect
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .records import now, write_json


def epoch(value): return datetime.fromisoformat(value).timestamp()


def timeline(events, frames, captures, controller):
    base=epoch(controller['started_at']); end=epoch(controller['finished_at'])
    calls=[e for e in events if e['kind']=='tool_call']
    results={e['request_id']:e for e in events if e['kind']=='tool_result'}
    bounds={};segments=[];cursor=0.;last_step=None
    for i,call in enumerate(calls,1):
        result=results.get(call['request_id'])
        if not result: raise ValueError('Cannot reconstruct a tool with no end timestamp')
        start=epoch(call['at'])-base; stop=epoch(result['at'])-base
        if start < -.5 or stop < start or start < cursor-.005:
            raise ValueError('Clock alignment or serialized tool ordering failed')
        start=max(0.,start);stop=max(start,stop)
        if start>cursor:segments.append({'phase':'outside_tool_wait','step':last_step,'start_seconds':cursor,'end_seconds':start})
        segments.append({'phase':'tool','step':i,'tool':call['name'],'primitive':call['arguments'].get('primitive'),
                         'request_id':call['request_id'],'start_seconds':start,'end_seconds':stop,
                         'actor':'supervisor' if call['request_id'] in {'batch-supervisor-finish','service-timeout'} else 'model'})
        bounds[call['request_id']]=(start,stop);cursor=stop;last_step=i
        end=max(end,base+stop)
    duration=end-base
    if duration>cursor:segments.append({'phase':'outside_tool_wait','step':last_step,'start_seconds':cursor,'end_seconds':duration})
    if not duration>0:raise ValueError('Nonpositive controller duration')
    boundary_indices=[i for i,f in enumerate(frames) if f['kind']=='observation_boundary']
    if len(boundary_indices)!=len(captures):raise ValueError('Capture/frame boundary count differs')
    times=[None]*len(frames)
    for index,capture in zip(boundary_indices,captures):
        times[index]=max(0.,epoch(capture['synchronized_capture']['captured_at'])-base)
    groups={}
    for i,f in enumerate(frames):
        if f['kind']=='env_step':groups.setdefault(f['request_id'],[]).append(i)
    for request,indices in groups.items():
        start,stop=bounds[request]
        following=indices[-1]+1
        if following<len(times) and times[following] is not None:stop=min(stop,times[following])
        if stop<start:raise ValueError('Capture predates its motor call')
        for n,index in enumerate(indices,1):times[index]=start+(stop-start)*n/(len(indices)+1)
    if any(t is None for t in times):raise ValueError('Unmapped raw frame')
    if any(b<a-.005 for a,b in zip(times,times[1:])):raise ValueError('Recorded capture order is not monotonic')
    times=[max(times[:i+1]) for i in range(len(times))] if any(b<a for a,b in zip(times,times[1:])) else times
    if times[-1]>duration+.5:raise ValueError('Video extends beyond recorded episode interval')
    tools=sum(s['end_seconds']-s['start_seconds'] for s in segments if s['phase']=='tool')
    return {'start_at':controller['started_at'],'end_at':datetime.fromtimestamp(end,datetime.fromisoformat(controller['started_at']).tzinfo).isoformat(),
            'duration_seconds':duration,'controller_duration_seconds':controller.get('duration_seconds'),
            'synchronous_tool_seconds':tools,'outside_tool_seconds':duration-tools,
            'outside_tool_scope':'model computation, client startup/finalization, transport and asynchronous work; NOT pure model thinking time',
            'segments':segments,'source_frame_wall_seconds':times,
            'frame_timing':'observation capture timestamps recorded; control frames uniformly spaced inside recorded tool intervals (estimated)',
            'model_message_timing':'Original public messages aligned to their following call; exact message emission times were not recorded'}


def build(run_dir: Path,controller_dir: Path,fps=15):
    from PIL import Image,ImageDraw,ImageOps
    from .explained_video import make_panel,get_font
    from .replay import render_replay
    output=run_dir/'walltime.mp4';record_path=run_dir/'walltime_video.json'
    if output.exists() or record_path.exists():raise FileExistsError('Viewing edition already exists; preserve prior renderer attempts')
    for name in ('replay.html','replay.json','replay_audit.json'):
        path=run_dir/name
        if path.exists():shutil.copyfile(path,path.with_name(path.stem+'_before_walltime'+path.suffix))
    data=render_replay(run_dir,controller_dir);raw=data['video']
    if not raw or raw['status']!='passed':raise ValueError('Requires a finalized original video')
    read=lambda p:[json.loads(s) for s in p.read_text().splitlines()]
    events=read(run_dir/'events.jsonl');frames=read(run_dir/'video_frames.jsonl');captures=read(run_dir/'captures.jsonl')
    controller=json.loads((controller_dir/'controller.json').read_text())
    timing=timeline(events,frames,captures,controller)
    write_json(run_dir/'walltime_timeline.json',timing)
    duration=timing['duration_seconds'];count=math.ceil(duration*fps)
    manifest={'status':'running','started_at':now(),'file':output.name,'poster':'walltime_poster.jpg',
              'host':platform.node(),'pid':os.getpid(),'interpreter':sys.executable,'unit':os.environ.get('MAS_UNIT'),
              'renderer_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'fps':fps,'width':1920,'height':1080,'target_duration_seconds':duration,
              'source_file':raw['file'],'source_sha256':raw['sha256'],'segments':timing['segments'],
              'input_hashes':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in
                              (run_dir/'events.jsonl',run_dir/'video_frames.jsonl',run_dir/'captures.jsonl',controller_dir/'controller.json')},
              'synchronous_tool_seconds':timing['synchronous_tool_seconds'],'outside_tool_seconds':timing['outside_tool_seconds'],
              'timing_scope':'controller start to recorded completion; initial simulator loading excluded',
              'model_wait':'retained at measured wall-clock duration; not a pure inference-latency measurement',
              'frame_timing':timing['frame_timing'],'model_message_timing':timing['model_message_timing'],
              'synthesized_motion':False,'repeated_frames':'holds reproduce measured waiting; no invented motion or reasoning'}
    write_json(record_path,manifest)
    size=raw['width']*raw['height']*3
    dec_log=(run_dir/'walltime_decode.log').open('wb');enc_log=(run_dir/'walltime_encode.log').open('wb')
    decoder=subprocess.Popen(['ffmpeg','-v','error','-threads','2','-i',str(run_dir/raw['file']),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE,stderr=dec_log)
    encoder=subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r',str(fps),'-i','pipe:0',
                             '-an','-c:v','libx264','-threads','2','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(output)],stdin=subprocess.PIPE,stderr=enc_log)
    source_index=-1;frame=None;phase_index=-1;last_key=None;panel=None;payload=None;used=set()
    starts=[s['start_seconds'] for s in timing['segments']];times=timing['source_frame_wall_seconds']
    run=json.loads((run_dir/'run.json').read_text());run['_final_model_messages']=data.get('model_final_messages',[])
    def read_frame():
        buf=bytearray()
        while len(buf)<size:
            chunk=decoder.stdout.read(size-len(buf))
            if not chunk:break
            buf.extend(chunk)
        if len(buf)!=size:raise RuntimeError('Original video ended before its ledger')
        return ImageOps.pad(Image.frombytes('RGB',(raw['width'],raw['height']),bytes(buf)),(1000,1078),method=Image.Resampling.BILINEAR,color='#101d2b')
    try:
        for n in range(count):
            t=min(n/fps,duration)
            target=max(0,bisect.bisect_right(times,t)-1)
            while source_index<target:frame=read_frame();source_index+=1
            used.add(source_index)
            active=min(len(starts)-1,max(0,bisect.bisect_right(starts,t)-1));segment=timing['segments'][active]
            step=data['steps'][segment['step']-1] if segment.get('step') else None
            if active!=phase_index:
                panel=make_panel(step,'execution' if segment['phase']=='tool' else ('result' if step else 'initial'),run,len(data['steps']))
                draw=ImageDraw.Draw(panel)
                draw.rectangle((1040,65,1919,178),fill='#14283a')
                label='TOOL: '+str(segment.get('primitive') or segment.get('tool')) if segment['phase']=='tool' else 'MODEL / NETWORK WAIT'
                draw.text((1052,76),label,font=get_font(30),fill='#99f4d2' if segment['phase']=='tool' else '#ffd591')
                draw.text((1052,125),'实测阶段时长 %.2f 秒'%(segment['end_seconds']-segment['start_seconds']),font=get_font(24),fill='white')
                draw.rectangle((1040,984,1919,1079),fill='#14283a')
                draw.text((1052,994),'1× 实际耗时 · 等待保留 · 不补写模型思考',font=get_font(22),fill='#aee4d9')
                draw.text((1052,1034),'工具内帧时间为估计；原始录像和时间戳可查。',font=get_font(20),fill='#bdc9d5')
                phase_index=active
            key=(source_index,phase_index,int(t))
            if key!=last_key:
                canvas=panel.copy();canvas.paste(frame,(12,1));draw=ImageDraw.Draw(canvas)
                draw.rectangle((1040,935,1919,980),fill='#0b1724')
                draw.text((1052,944),'Elapsed %06.1fs / %.2fs   |   Phase %05.1fs'%(t,duration,t-segment['start_seconds']),font=get_font(23),fill='white')
                payload=canvas.tobytes();last_key=key
                if n==0:canvas.save(run_dir/'walltime_poster.jpg',quality=92)
            encoder.stdin.write(payload)
        # Consume and check the complete original stream, even if CFR playback
        # cannot expose multiple boundary frames inside the same 1/fps interval.
        while source_index<len(frames)-1:read_frame();source_index+=1
        if decoder.stdout.read(1):raise RuntimeError('Original video has extra unledgered frames')
        encoder.stdin.close();enc=encoder.wait(timeout=120);dec=decoder.wait(timeout=30)
        if enc or dec:raise RuntimeError(f'Encoder/decoder failed: {enc}/{dec}')
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(output)],text=True))['streams'][0]
        actual=float(probe['duration'])
        validation={'duration_within_one_frame':abs(actual-duration)<=1/fps+.002,
                    'decoded_frame_count':int(probe['nb_read_frames'])==count,
                    'browser_h264':probe['codec_name']=='h264' and probe['pix_fmt']=='yuv420p',
                    'all_original_frames_decoded':source_index+1==len(frames),
                    'wait_and_tool_intervals_cover_duration':abs(sum(s['end_seconds']-s['start_seconds'] for s in timing['segments'])-duration)<.001}
        if not all(validation.values()):raise RuntimeError('Wall-time video validation failed: '+str(validation))
        with output.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        manifest.update(status='passed',finished_at=now(),frame_count=count,duration_seconds=actual,
                        source_frame_count=len(frames),source_frames_displayed=len(used),checks=validation,sha256=sha,bytes=output.stat().st_size)
        write_json(record_path,manifest);render_replay(run_dir,controller_dir)
        return manifest
    except BaseException as exc:
        manifest.update(status='failed',error=str(exc),finished_at=now());write_json(record_path,manifest)
        for process in (decoder,encoder):
            if process.poll() is None:process.kill();process.wait()
        raise
    finally:
        dec_log.close();enc_log.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--controller-dir',type=Path,required=True);p.add_argument('--fps',type=int,default=15);a=p.parse_args()
    r=build(a.run_dir,a.controller_dir,a.fps);print(json.dumps({k:r[k] for k in ('status','duration_seconds','frame_count','bytes')}))
