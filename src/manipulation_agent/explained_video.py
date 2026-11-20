"""Create an annotated viewing edition from original video frames and public calls.

All raw frames are retained once and in order. Decision/result holds repeat a real
frame; no optical-flow interpolation, invented reasoning or resimulation is used.
The sidecar maps every viewing interval to its source frame/call/phase.
"""
from __future__ import annotations
import argparse
from functools import lru_cache
import hashlib
import json
import os
import sys
import platform
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .records import now,write_json
from .replay import render_replay

PHASES={'initial':'Initial observation','decision':'Model output / tool call','execution':'Tool execution','result':'Tool result','evaluation':'Independent evaluation','final':'Final model output'}
LABELS={}
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')


@lru_cache(maxsize=16)
def get_font(size):
    return ImageFont.truetype(str(FONT),size)


@lru_cache(maxsize=4096)
def char_width(char,size):
    return get_font(size).getlength(char)


def outcome(step):
    result=step.get('result')
    if not result:return 'No tool result recorded.'
    keys=('ok','error','effect','closed','job')
    return json.dumps({k:result[k] for k in keys if k in result},ensure_ascii=False,separators=(',',':'))


def make_panel(step,phase,run,total):
    canvas=Image.new('RGB',(1920,1080),'#101d2b');draw=ImageDraw.Draw(canvas)
    font=get_font
    def wrap(text,x,y,width=845,size=26,color='#dfeaf4',max_lines=7):
        lines=[];line='';line_width=0.
        for c in str(text):
            cw=char_width(c,size) if c!='\n' else 0
            if c=='\n' or line_width+cw>width-8:
                lines.append(line);line='' if c=='\n' else c;line_width=0 if c=='\n' else cw
            else:line+=c;line_width+=cw
        if line:lines.append(line)
        if len(lines)>max_lines:lines=lines[:max_lines];lines[-1]+='…'
        for line in lines:
            draw.text((x,y),line,font=font(size),fill=color);y+=size+12
        return y
    draw.rectangle((1024,0,1919,1079),fill='#14283a')
    wrap('BEHAVIOR · RGB AGENT',1052,28,size=22,color='#64d9cb')
    number=step['index'] if step else 0
    heading=f'Step {number:02d} / {total:02d} | {PHASES[phase]}' if step else PHASES[phase]
    wrap(heading,1052,72,size=34,max_lines=2)
    tool=step['tool'] if step else 'observe'
    args=step['arguments'] if step else {}
    action=args.get('primitive',tool)
    wrap(LABELS.get(action,action) if step else 'Recorded replay',1052,135,size=30,color='#ffffff')
    # Exact executable parameters, separate from the public decision text.
    call={k:v for k,v in args.items() if k!='decision'}
    if tool=='finish':call={'outcome':args.get('outcome')}
    if step:
        wrap(tool+'('+json.dumps(call,ensure_ascii=False,separators=(',',':'))+')',1052,186,size=21,color='#80c9ee',max_lines=4)
    y=315
    summaries=step.get('model_reasoning_summaries',[]) if step else []
    messages=step.get('model_messages',[]) if step else (run.get('_final_model_messages',[]) if phase=='final' else [])
    if summaries:
        y=wrap('Provider reasoning summary (verbatim)',1052,y,size=22,color='#e4be79')+8
        for message in summaries:
            y=wrap(message['text'],1052,y,size=25,max_lines=5)+8
    y=wrap('Assistant output (verbatim)',1052,y,size=22,color='#74c9bf')+12
    if messages:
        for message in messages:
            y=wrap(message['text'],1052,y,size=28,max_lines=7)+10
    else:
        y=wrap('No new assistant text before this call.',1052,y,size=27,max_lines=2)+10
        y=wrap('Exact tool arguments shown above.',1052,y,size=24,max_lines=3)+14
    if tool=='read_skill':
        y=wrap('Skill: '+args.get('name',''),1052,min(y,660),size=26,color='#87dccc',max_lines=2)
    if tool=='finish':
        wrap('finish.reason: '+args.get('reason',''),1052,min(y,590),size=23,max_lines=5)
    if phase=='result' and step:
        draw.rectangle((1040,810,1903,979),fill='#1b3e49')
        wrap('Tool response',1052,824,size=20,color='#83e3c9')
        wrap(outcome(step),1052,859,size=23,max_lines=3)
    elif phase=='evaluation':
        success=run.get('task_success')
        draw.rectangle((1040,315,1903,850),fill='#17463f')
        wrap('Independent evaluation',1070,360,size=34,color='#99f4d2')
        wrap('Task passed. Q = '+str(run.get('evaluation',{}).get('goal_satisfaction_fraction','—')) if success else 'Task not passed. See recorded evaluation.',1070,440,width=780,size=36,max_lines=4)
        wrap('Offline score; not visible to the agent.',1070,655,width=780,size=27,max_lines=3)
    wrap(run.get('_video_note','Recorded frames | original text | timeline in replay'),1052,996,size=21,color='#99aec0',max_lines=1)
    wrap('Full transcript and original timing in the replay.',1052,1031,size=21,color='#99aec0',max_lines=1)
    return canvas


def build(run_dir,controller_dir=None):
    data=render_replay(run_dir,controller_dir)
    raw=data['video'];assert raw and raw['status']=='passed'
    run=json.loads((run_dir/'run.json').read_text());run['_final_model_messages']=data.get('model_final_messages',[]);fps=raw['fps'];steps=data['steps']
    size=raw['width']*raw['height']*3
    output=run_dir/'explained.mp4';ledger=[];out_count=0;source_count=0;last=None
    manifest={'host':platform.node(),'pid':os.getpid(),'interpreter':sys.executable,
        'renderer_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'ffmpeg_version':subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0],
        'status':'running','started_at':now(),'file':output.name,'poster':'explained_poster.jpg','fps':fps,
        'source_sha256':raw['sha256'],'source_file':raw['file'],'source_frames':raw['frame_count'],
        'scope':'all raw video frames once in order, plus labeled public-decision/result holds',
        'model_wait':'compressed; holds are viewing time, not model latency','synthesized_motion':False,
        'text_source':'verbatim public assistant messages in original event order; not private reasoning; no rewritten Chinese summary'}
    write_json(run_dir/'explained_video.json',manifest)
    decoder=subprocess.Popen(['ffmpeg','-v','error','-threads','2','-i',str(run_dir/raw['file']),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE,stderr=(run_dir/'explained_decode.log').open('wb'))
    encoder=subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r',str(fps),'-i','pipe:0','-an','-c:v','libx264','-threads','3','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-movflags','+faststart',str(output)],stdin=subprocess.PIPE,stderr=(run_dir/'explained_encoder.log').open('wb'))
    manifest.update(encoder_pid=encoder.pid,decoder_pid=decoder.pid)
    write_json(run_dir/'explained_video.json',manifest)
    def read_frame():
        nonlocal source_count
        buf=bytearray()
        while len(buf)<size:
            block=decoder.stdout.read(size-len(buf))
            if not block:break
            buf.extend(block)
        if len(buf)!=size:raise RuntimeError('Source ended before manifest frame count')
        source_count+=1
        return ImageOps.pad(Image.frombytes('RGB',(raw['width'],raw['height']),bytes(buf)),
                            (1000,1078),method=Image.Resampling.BILINEAR,color='#101d2b')
    def write_frame(panel,frame,repeat=1):
        nonlocal out_count
        canvas=panel.copy();canvas.paste(frame,(12,1));payload=canvas.tobytes()
        if out_count==0:canvas.save(run_dir/'explained_poster.jpg',quality=92)
        for _ in range(repeat):encoder.stdin.write(payload)
        out_count+=repeat
    def interval(step,phase,count,hold=False):
        nonlocal last
        if count<=0:return
        begin=out_count;source_begin=source_count
        panel=make_panel(step,phase,run,len(steps))
        if hold:
            if last is None:raise RuntimeError('Hold requires an actual preceding frame')
            write_frame(panel,last,count)
        else:
            for _ in range(count):last=read_frame();write_frame(panel,last)
        ledger.append({'step':step['index'] if step else None,'request_id':step.get('request_id') if step else None,
            'phase':phase,'start_frame':begin,'end_frame_exclusive':out_count,'start_seconds':begin/fps,'end_seconds':out_count/fps,
            'source_start_frame':source_begin if not hold else source_count-1,
            'source_end_frame_exclusive':source_count,'repeated_frame_hold':hold})
    markers={m['request_id']:m['frame_index'] for m in raw['markers']}
    try:
        first=markers.get(steps[0]['request_id'],1) if steps else raw['frame_count']
        interval(None,'initial',first)
        for i,step in enumerate(steps):
            start=markers[step['request_id']]
            end=markers[steps[i+1]['request_id']] if i+1<len(steps) else raw['frame_count']
            if source_count != start:raise RuntimeError('Non-monotonic video/call markers')
            duration=4 if step.get('model_messages') else (2 if step['tool']=='read_skill' else 1)
            interval(step,'decision',round(duration*fps),True)
            interval(step,'execution',end-start)
            interval(step,'result',round((4 if step['tool'] in {'read_skill','finish'} else 2)*fps),True)
        if data.get('model_final_messages'):
            interval(None,'final',round(min(12,max(4,sum(len(m['text']) for m in data['model_final_messages'])/25))*fps),True)
        interval(None,'evaluation',round(5*fps),True)
        if decoder.stdout.read(1):raise RuntimeError('More source frames than recorded')
        encoder.stdin.close();code=encoder.wait(timeout=120);dec=decoder.wait(timeout=30)
        if code or dec:raise RuntimeError(f'Encoder/decoder returned {code}/{dec}')
        if source_count!=raw['frame_count']:raise RuntimeError('Raw frame coverage mismatch')
        manifest.update(status='passed',finished_at=now(),frame_count=out_count,duration_seconds=out_count/fps,
            width=1920,height=1080,source_frames_used=source_count,all_source_frames_once=True,segments=ledger,
            bytes=output.stat().st_size,sha256=hashlib.sha256(output.read_bytes()).hexdigest())
        write_json(run_dir/'explained_video.json',manifest)
        render_replay(run_dir,controller_dir)
        return manifest
    except BaseException as exc:
        manifest.update(status='failed',error=str(exc));write_json(run_dir/'explained_video.json',manifest)
        for proc in [decoder,encoder]:
            if proc.poll() is None:proc.kill();proc.wait()
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--controller-dir',type=Path);a=p.parse_args()
    r=build(a.run_dir,a.controller_dir);print(json.dumps({k:r[k] for k in ['status','frame_count','duration_seconds','bytes']}))
