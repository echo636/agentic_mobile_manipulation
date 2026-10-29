"""Bounded CPU publication of both real-time and readable editions for new runs."""
import argparse,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--batch',type=Path,required=True);p.add_argument('--reports',type=Path,required=True);p.add_argument('--newest-first',action='store_true',help='Render higher task indices first, then fill older missing editions');a=p.parse_args()
b=a.batch;reports=a.reports
sys.path.insert(0,str(a.runtime/'src'))
from manipulation_agent.records import now,write_json,source_version
from manipulation_agent.replay import render_replay
records=b/'render_records';records.mkdir(exist_ok=True);public=reports/'behavior100';public.mkdir(exist_ok=True,parents=True)
index_path=public/'inspection_index.json'
def hashes(paths):
 out={}
 for path in paths:
  if path.is_file():
   with path.open('rb') as f:out[str(path)]=hashlib.file_digest(f,'sha256').hexdigest()
 return out

def preflight(rid):
 cg=Path('/sys/fs/cgroup/user.slice')/f'user-{os.getuid()}.slice'
 current=int((cg/'memory.current').read_text());maximum=(cg/'memory.max').read_text().strip()
 result={'at':now(),'host':socket.gethostname(),'pid':os.getpid(),'unit':os.environ.get('MAS_UNIT'),'interpreter':sys.executable,'gpu_uuid':None,'cpu_only':True,'source':source_version(),'output':str(records/rid),'cgroup_current':current,'cgroup_max':maximum,'disk_free':shutil.disk_usage(b).free,'quota':subprocess.run(['quota','-s'],capture_output=True,text=True,timeout=20).stdout,'gpu_inventory':subprocess.run(['nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader'],capture_output=True,text=True,timeout=20).stdout}
 result['status']='passed' if result['disk_free']>20*1024**3 and (maximum=='max' or int(maximum)-current>3*1024**3) else 'blocked'
 write_json(records/(rid+'_preflight.json'),result);return result

with (b/'render_watch.lock').open('w') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 index=json.loads(index_path.read_text()).get('runs',{}) if index_path.exists() else {}
 while True:
  try:
   progress=json.loads((b/'progress.json').read_text())
  except (json.JSONDecodeError,FileNotFoundError) as exc:
   with (b/'journal.md').open('a') as f:f.write('\n'+now()+' — Replay publisher waiting for readable progress.json: '+type(exc).__name__+'; retry next poll.\n')
   time.sleep(20);continue
  rows=progress['tasks']
  if a.newest_first:rows=sorted(rows,key=lambda row:row.get('index',-1),reverse=True)
  for row in rows:
   rid=row['run_id']
   if row['status'] not in {'passed','failed','blocked'} or index.get(rid,{}).get('status') in {'passed','failed','unavailable'}:continue
   run=b/'runs'/rid;ctrl=b/'controllers'/rid;dest=reports/'manipulation_runs'/rid
   if row.get('video_validation')!='passed':
    index[rid]={'status':'unavailable','reason':'No verified complete source video; raw partial artifacts preserved'};continue
   resource=preflight(rid)
   if resource['status']!='passed':index[rid]=resource;continue
   source=[run/n for n in ['run.json','events.jsonl','captures.jsonl','video.json','video_frames.jsonl','episode.mp4']]+[ctrl/n for n in ['controller.json','model_events.jsonl','model_reasoning_summaries.jsonl']]
   before=hashes(source);result={'status':'running','started_at':now(),'editions':{}}
   try:
    backup=records/rid/'original_derived';backup.mkdir(parents=True,exist_ok=True)
    for name in ['replay.html','replay.json','replay_audit.json']:
     if (run/name).exists() and not (backup/name).exists():shutil.copyfile(run/name,backup/name)
    for module,manifest in [('walltime_video','walltime_video.json'),('inspection_video','inspection_video.json')]:
     if not (run/manifest).exists():
      with (records/(rid+'_'+module+'.log')).open('w') as log:
       process=subprocess.run([sys.executable,'-m','manipulation_agent.'+module,'--run-dir',str(run),'--controller-dir',str(ctrl)],stdout=log,stderr=subprocess.STDOUT,timeout=10800,env={**os.environ,'PYTHONPATH':str(a.runtime/'src')})
     edition=json.loads((run/manifest).read_text()) if (run/manifest).exists() else {'status':'failed','error':'Renderer produced no manifest'}
     result['editions'][module]={k:edition.get(k) for k in ['status','file','duration_seconds','error']}
    render_replay(run,ctrl)
    if hashes(source)!=before:raise RuntimeError('Immutable policy/evaluator/video input changed')
    files=[path for path in run.iterdir() if path.is_file() and (path.name.startswith(('walltime','inspection','replay','model_public','browser_episode','browser_video.json')) or '_before_walltime.' in path.name)]
    dest.mkdir(exist_ok=True,parents=True)
    for path in files:
     tmp=dest/(path.name+'.tmp');shutil.copyfile(path,tmp);tmp.replace(dest/path.name)
    if (run/'inspection_frames').exists():shutil.copytree(run/'inspection_frames',dest/'inspection_frames',dirs_exist_ok=True)
    result.update(status='passed' if all(x['status']=='passed' for x in result['editions'].values()) else 'failed',raw_evidence_unchanged=True,raw_input_hashes=before,generated_hashes=hashes(files))
   except Exception as exc:result.update(status='failed',error=str(exc))
   result['finished_at']=now();write_json(records/(rid+'.json'),{**resource,**result});index[rid]=result
   with (b/'journal.md').open('a') as f:f.write('\n'+now()+' — Replay publication '+rid+': '+json.dumps({k:v for k,v in result.items() if k not in ['raw_input_hashes','generated_hashes']})+'\n')
   write_json(index_path,{'updated_at':now(),'status':'running','runs':index})
  complete=progress['summary']['execution_status']=='completed'
  write_json(index_path,{'updated_at':now(),'status':'completed' if complete else 'running','runs':index})
  if complete:
   break
  time.sleep(20)
