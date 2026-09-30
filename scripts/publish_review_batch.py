"""Publish additive compact videos; archive replaced derivative replay documents."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import now,write_json,source_version
from manipulation_agent.replay import render_replay

p=argparse.ArgumentParser();p.add_argument('--batch',type=Path,required=True)
p.add_argument('--reports',type=Path,required=True);p.add_argument('--operations',type=Path,required=True)
p.add_argument('--only');a=p.parse_args()
records=a.operations/'video_records';records.mkdir(exist_ok=True,parents=True)
index_path=a.reports/'behavior100/review_index.json';index={}
if index_path.exists():index=json.loads(index_path.read_text())['runs']
def publish(status):write_json(index_path,{'status':status,'updated_at':now(),'runs':index})
publish('running')
for row in json.loads((a.batch/'progress.json').read_text())['tasks']:
    runid=row['run_id']
    if a.only and a.only!=runid:continue
    run=a.batch/'runs'/runid;controller=a.batch/'controllers'/runid
    dest=a.reports/'manipulation_runs'/runid
    if not (run/'run.json').exists():continue
    cg=Path('/sys/fs/cgroup/user.slice')/f'user-{os.getuid()}.slice'
    maximum=(cg/'memory.max').read_text().strip();current=int((cg/'memory.current').read_text())
    record={'at':now(),'host':os.uname().nodename,'interpreter':sys.executable,'pid':os.getpid(),
        'unit':os.environ.get('MAS_UNIT'),'gpu_uuid':None,'output':str(run),
        'source':source_version(),'cgroup_current':current,'cgroup_max':maximum,
        'disk_free':shutil.disk_usage(run).free,'quota':subprocess.run(['quota','-s'],capture_output=True,text=True).stdout}
    write_json(records/(runid+'_preflight.json'),record)
    if (maximum!='max' and int(maximum)-current<2*1024**3) or record['disk_free']<5*1024**3:
        index[runid]={'status':'blocked','reason':'CPU renderer resource headroom'};publish('running');continue
    for folder in (run,dest):
        for name in ('replay.html','replay.json','replay_audit.json','model_public_events.jsonl'):
            src=folder/name;backup=folder/(src.stem+'_before_review'+src.suffix)
            if src.exists() and not backup.exists():shutil.copyfile(src,backup)
    controller=controller if (controller/'controller.json').exists() else None
    video=json.loads((run/'video.json').read_text()) if (run/'video.json').exists() else None
    if video and video.get('status')=='passed':
        manifest=run/'review_video.json'
        if not manifest.exists():
            with (records/(runid+'.log')).open('w') as log:
                command=[sys.executable,'-m','manipulation_agent.review_video','--run-dir',str(run)]
                if controller:command+=['--controller-dir',str(controller)]
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
        result=json.loads(manifest.read_text()) if manifest.exists() else {'status':'failed','error':'No render manifest'}
        index[runid]={k:result.get(k) for k in ('status','file','duration_seconds','motion_speed_vs_source','error','all_tool_calls_represented')}
    else:index[runid]={'status':'unavailable','reason':'No verified complete source video'}
    render_replay(run,controller)
    names=['replay.html','replay.json','replay_audit.json','model_public_events.jsonl',
        'review.mp4','review_poster.jpg','review_video.json']
    hashes={};dest.mkdir(exist_ok=True,parents=True)
    for name in names:
        src=run/name
        if not src.exists():continue
        with src.open('rb') as stream:hashes[name]=hashlib.file_digest(stream,'sha256').hexdigest()
        tmp=dest/(name+'.tmp');shutil.copyfile(src,tmp);tmp.replace(dest/name)
    evidence={**record,'finished_at':now(),'result':index[runid],'artifact_hashes':hashes,
        'raw_run_events_captures_and_scores_unchanged':True}
    write_json(records/(runid+'.json'),evidence);write_json(dest/'review_artifact_hashes.json',evidence)
    with (a.operations/'journal.md').open('a') as stream:
        stream.write('\n'+now()+' — compact replay '+runid+': '+json.dumps(index[runid])+'; raw evidence preserved.\n')
    publish('running')
publish('completed')
