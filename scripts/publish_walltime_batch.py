"""CPU-only postprocessor alongside a frozen simulator batch; never edits policy evidence."""
import argparse,fcntl,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import now,write_json,source_version

PAGE='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>100 项测试 · 真实耗时回放</title><style>body{font:16px system-ui;max-width:1450px;margin:28px auto;padding:0 18px;background:#eef3f8;color:#183049}article{background:white;padding:20px;border:1px solid #d3dfe9;border-radius:12px;margin:18px 0}p{line-height:1.7}a{color:#0762a2}video{width:100%;max-height:75vh;background:#0a1420}small{display:block;color:#62768a;margin-top:6px}table{width:100%;border-collapse:collapse;min-width:950px}td,th{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #ddd}td:nth-child(2){max-width:340px;overflow-wrap:anywhere}.table{overflow:auto}.stats{display:flex;flex-wrap:wrap;gap:20px}.stats b{font-size:28px}.passed{color:#137244}.failed{color:#aa3333}input{font:inherit;padding:8px}#error{color:#b32a24}</style><h1>BEHAVIOR 100 · 真实耗时回放</h1><p>100 个任务类型 × public instance 301 × seed 0。四个固定 RGB 相机、当前 skills/tools、理想动作执行器。<a href="behavior100.html">完整实验账本</a></p><article><div id="stats" class="stats"></div><p id="status"></p><p>视频在 <b>1×</b> 下保留控制器从启动到结束的实际时间，包含模型交互、网络及工具耗时；不包含仿真场景初始化。调用外等待不能当作纯模型思考时间。工具的起止时间及 RGB 观测时间有原始记录；工具内部帧间时间按调用区间估计，未插造运动。公开文本按工具调用顺序对齐，未记录其精确输出时刻。</p><p>每项完成后自动生成真实耗时版和 Replay，原始仿真时间录像仍保留。没有完整视频的故障不会补造视频。前期两次模型执行前的服务配置故障保留在账本；首次尝试成绩与修复后结果分别报告。</p></article><article id="featured" hidden><h2 id="video-title"></h2><video id="video" controls playsinline preload="metadata"></video><p id="video-info"></p><a id="replay">打开完整 Replay、四路 RGB、LLM 公开原文及精确工具调用 →</a></article><p id="error"></p><input id="search" placeholder="搜索任务"><div class="table"><table><thead><tr><th>#</th><th>任务</th><th>测试状态</th><th>结果</th><th>真实用时</th><th>回放</th></tr></thead><tbody id="rows"></tbody></table></div><script>
const $=id=>document.getElementById(id);let rows=[],index={},selected=null;const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));function show(id){let r=rows.find(r=>r.run_id===id),v=index[id];if(!r||v?.status!=='passed')return;selected=id;$('featured').hidden=false;$('video-title').textContent=r.name+' · 真实耗时 '+v.duration_seconds.toFixed(2)+' 秒';$('video').src=v.base+v.file;$('video').poster=v.base+'walltime_poster.jpg';$('video').playbackRate=1;$('video-info').textContent='同步工具 '+v.synchronous_tool_seconds.toFixed(2)+' 秒；调用外等待 '+v.outside_tool_seconds.toFixed(2)+' 秒。完整片长按实测时间呈现。';$('replay').href=v.base+'replay.html'}function draw(){let q=$('search').value.toLowerCase();$('rows').innerHTML=rows.filter(r=>(r.task+' '+r.name).toLowerCase().includes(q)).map(r=>{let v=index[r.run_id],links=[];if(r.replay_url)links.push('<a href="'+esc(r.replay_url)+'">Replay</a>');if(v?.status==='passed')links.push('<a href="'+esc(v.base+v.file)+'">真实耗时 MP4</a>','<button data-run="'+esc(r.run_id)+'">本页播放</button>');if(r.video_url)links.push('<a href="'+esc(r.video_url)+'">原始仿真时间版</a>');return '<tr><td>'+(r.index+1)+'</td><td>'+esc(r.name)+'<small>'+esc(r.task)+'</small></td><td class="'+esc(r.status)+'">'+esc(r.status)+'<small>'+esc(r.stage||'queued')+'</small></td><td>'+(r.task_success===true?'成功':r.task_success===false?'未成功':'待评估')+'</td><td>'+(v?.status==='passed'?v.duration_seconds.toFixed(2)+' 秒':v?.status==='failed'?'渲染失败，保留日志':r.video_validation==='passed'?'正在生成':'—')+'</td><td>'+links.join(' · ')+'</td></tr>'}).join('');document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>show(b.dataset.run))}async function refresh(){try{let [p,w]=await Promise.all(['behavior100/progress.json','behavior100/walltime_index.json'].map(url=>fetch(url,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error(url+' '+r.status);return r.json()})));rows=p.tasks;index=w.runs;let s=p.summary,n=Object.values(index).filter(v=>v.status==='passed').length;$('stats').innerHTML='<div><b>'+s.completed+' / '+s.total+'</b><small>已结束</small></div><div><b>'+s.task_successes+' / '+s.total+'</b><small>最新尝试任务成功</small></div><div><b>'+n+'</b><small>真实耗时视频已生成</small></div>';$('status').textContent='首次尝试成功 '+s.first_attempt_task_successes+'/'+s.total+'；额外基础设施尝试 '+s.extra_infrastructure_attempts+'。任务批次 '+s.execution_status+'，视频处理 '+w.status+'；更新 '+p.updated_at;draw();if(!selected){let r=rows.find(r=>index[r.run_id]?.status==='passed');if(r)show(r.run_id)}$('error').textContent=''}catch(e){$('error').textContent='读取进度失败：'+e.message}}$('search').oninput=draw;refresh();setInterval(refresh,30000);
</script></html>'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--batch',type=Path,required=True);p.add_argument('--reports',type=Path,required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
    source=Path(__file__).resolve().parents[1];records=a.batch/'walltime_records';records.mkdir(exist_ok=True)
    public=a.reports/'behavior100';public.mkdir(exist_ok=True)
    (a.reports/'behavior100_realtime.html').write_text(PAGE)
    with (a.batch/'walltime.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        index=json.loads((public/'walltime_index.json').read_text())['runs'] if (public/'walltime_index.json').exists() else {}
        def publish(status):write_json(public/'walltime_index.json',{'updated_at':now(),'status':status,'renderer_source':source_version(),'runs':index})
        publish('running')
        while True:
            progress=json.loads((a.batch/'progress.json').read_text())
            for row in progress['tasks']:
                if row['status'] not in {'passed','failed','blocked'} or row.get('video_validation')!='passed':continue
                runid=row['run_id']
                if index.get(runid,{}).get('status') in {'passed','failed'}:continue
                run=a.batch/'runs'/runid;controller=a.batch/'controllers'/runid
                output=run/'walltime_video.json'
                cg=Path('/sys/fs/cgroup/user.slice')/f'user-{os.getuid()}.slice'
                current=int((cg/'memory.current').read_text());maximum=(cg/'memory.max').read_text().strip()
                free=None if maximum=='max' else int(maximum)-current
                record={'at':now(),'host':os.uname().nodename,'pid':os.getpid(),'unit':os.environ.get('MAS_UNIT'),'interpreter':sys.executable,'gpu_uuid':None,
                        'cgroup_memory_current':current,'cgroup_memory_max':maximum,'disk_free':shutil.disk_usage(a.batch).free,'output':str(run),'renderer_source':source_version()}
                write_json(records/(runid+'_preflight.json'),record)
                if (free is not None and free<2*1024**3) or record['disk_free']<5*1024**3:
                    index[runid]={'status':'blocked','reason':'Renderer resource headroom','base':'manipulation_runs/'+runid+'/'};publish('running');continue
                if not output.exists():
                    with (records/(runid+'.log')).open('w') as log:
                        subprocess.run([sys.executable,'-m','manipulation_agent.walltime_video','--run-dir',str(run),'--controller-dir',str(controller)],
                                       stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONPATH':str(source/'src')},timeout=10800)
                result=json.loads(output.read_text()) if output.exists() else {'status':'failed','error':'Renderer did not create a record'}
                destination=a.reports/'manipulation_runs'/runid;destination.mkdir(exist_ok=True)
                names=[f.name for f in run.glob('walltime*') if f.is_file()]+[f.name for f in run.glob('*_before_walltime.*')]
                names+=['replay.json','replay_audit.json','replay.html']
                for name in names:
                    if not (run/name).exists():continue
                    tmp=destination/(name+'.tmp');shutil.copyfile(run/name,tmp);tmp.replace(destination/name)
                hashes={}
                for name in names:
                    if (run/name).is_file():
                        with (run/name).open('rb') as f:hashes[name]=hashlib.file_digest(f,'sha256').hexdigest()
                write_json(run/'walltime_artifact_hashes.json',{'generated_at':now(),'hashes':hashes,'note':'Original artifact_hashes.json is preserved; pre-walltime replay bytes are retained under *_before_walltime.*.'})
                shutil.copyfile(run/'walltime_artifact_hashes.json',destination/'walltime_artifact_hashes.json')
                index[runid]={k:result.get(k) for k in ('status','file','duration_seconds','synchronous_tool_seconds','outside_tool_seconds','error')}
                index[runid]['base']='manipulation_runs/'+runid+'/'
                write_json(records/(runid+'.json'),{**record,'result':result})
                with (a.batch/'walltime_journal.md').open('a') as f:f.write(f"\n- {now()} · {runid}: {result['status']}; duration={result.get('duration_seconds')}; renderer={source_version()['commit']}; original policy/evaluator evidence unchanged.\n")
                publish('running')
            done=progress['summary']['execution_status']=='completed'
            publish('completed' if done else 'running')
            if done or not a.watch:return
            time.sleep(20)


if __name__=='__main__':main()
