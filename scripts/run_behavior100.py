"""Durable 100-task runner. One immutable attempt per row; never filter failures.

Run from a clean, frozen checkout on the controller host. JSON config supplies
site paths; authentication stays in the existing local SSH/model clients.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import now, write_json, read_run, source_version
from manipulation_agent.replay import render_replay
from manipulation_agent.tools import tool_specs

FINAL = {'passed','failed','blocked'}


def episode_artifact_hashes(runid, episode, controller):
    """Stable archive keys also cover an adopted controller in another batch."""
    hashes={}
    for label,folder in [('runs',episode),('controllers',controller)]:
        if not folder.exists():continue
        for path in sorted(folder.iterdir()):
            if path.is_file():
                with path.open('rb') as stream:
                    hashes[f'{label}/{runid}/{path.name}']=hashlib.file_digest(stream,'sha256').hexdigest()
    return hashes


def worker_configs(config):
    """Resolve explicit host/GPU lanes; legacy single-host configs still work."""
    lanes = config.get('workers')
    if lanes is None:
        lanes = [{'id':f'gpu{gpu}', 'gpu':gpu} for gpu in config['gpus']]
    if not lanes:
        raise ValueError('At least one worker is required')
    allowed = {'id', 'gpu', 'ssh', 'data_root', 'sim_python', 'sim_env',
               'base_port', 'minimum_free_gpu_mib', 'simulator_memory_max',
               'simulator_env', 'expected_gpu_uuid', 'memory_budget_gib'}
    result=[]; ids=set(); devices=set(); ports=set()
    for lane in lanes:
        if set(lane)-allowed: raise ValueError('Unsupported worker fields')
        merged={**config, **lane}
        worker_id=lane.get('id')
        if not worker_id or worker_id in ids: raise ValueError('Duplicate or missing worker id')
        host=tuple(merged['ssh'][-1:]);gpu=merged['gpu'];port=merged['base_port']+gpu
        if type(gpu) is not int or gpu<0 or not 1024<=port<=65535: raise ValueError('Invalid GPU or port')
        if (host,gpu) in devices or (host,port) in ports: raise ValueError('Worker GPU or port collision')
        env=merged.get('simulator_env',{})
        budget=merged.get('memory_budget_gib',28)
        if type(budget) is not int or not 12<=budget<=28: raise ValueError('Invalid memory budget')
        if merged.get('simulator_memory_max',str(budget)+'G')!=str(budget)+'G':
            raise ValueError('Resource admission and hard simulator memory limit must agree')
        if set(env)-{'MAS_VIDEO_RENDER_STRIDE','MAS_VIDEO_RENDER_FLUSHES'}:
            raise ValueError('Only reviewed recording options may be worker environment overrides')
        ids.add(worker_id);devices.add((host,gpu));ports.add((host,port));result.append(merged)
    budgets=Counter()
    for worker in result:budgets[worker['ssh'][-1]]+=worker.get('memory_budget_gib',28)
    for host,limit in config.get('host_worker_memory_budget_gib',{}).items():
        if budgets[host]>limit:raise ValueError('Combined worker hard limits exceed configured host allowance')
    return result


def controller_process_alive(pid, output):
    """Check an owned controller wrapper without signaling or restarting it."""
    proc=Path('/proc')/str(pid)
    try:
        if proc.stat().st_uid!=os.getuid(): raise RuntimeError('Controller UID mismatch')
        state=(proc/'stat').read_text().rsplit(') ',1)[1].split()[0]
        if state=='Z': return False
        args=(proc/'cmdline').read_bytes().decode().split('\0')
    except FileNotFoundError:
        return False
    if not any(x.endswith('/scripts/run_codex_controller.py') for x in args):
        raise RuntimeError('Controller PID reused by another process')
    if '--output' not in args or args[args.index('--output')+1]!=str(output):
        raise RuntimeError('Controller output ownership mismatch')
    return True


def prepolicy_failure(record, controller):
    if record.get('status')!='failed' or record.get('actions')!=0: return False
    stream=controller/'model_events.jsonl'
    if not stream.exists(): return record.get('controller_pid') is None
    events=[json.loads(s) for s in stream.read_text().splitlines()]
    # Client error items are not model decisions. Any generated policy item,
    # including reasoning or a started tool call, prevents this retry route.
    return all(e.get('item',{}).get('type') in (None,'error') for e in events)


def summarize(rows):
    counts = dict(Counter(r['status'] for r in rows))
    completed = sum(r['status'] in FINAL for r in rows)
    successes = sum(r.get('task_success') is True for r in rows)
    first=[r.get('previous_attempts',[r])[0] for r in rows]
    extra=sum(len(r.get('previous_attempts',[])) for r in rows)
    infrastructure=sum(len(r.get('previous_attempts',[])) for r in rows
                       if r.get('retry_kind','infrastructure')=='infrastructure')
    return {'total':len(rows), 'completed':completed, 'counts':counts, 'task_successes':successes,
            'first_attempt_task_successes':sum(r.get('task_success') is True for r in first),
            'extra_infrastructure_attempts':infrastructure,
            'extra_policy_attempts':extra-infrastructure,
            'success_fraction_all_tasks': successes/len(rows) if rows else 0,
            'final_evaluations':sum(r.get('task_success') is not None for r in rows),
            'complete_videos':sum(r.get('video_validation')=='passed' for r in rows),
            'execution_status':'completed' if completed == len(rows) else 'running'}


def render_dashboard(progress):
    esc=lambda x: html.escape(str(x))
    s=progress['summary']; rows=[]
    for r in progress['tasks']:
        links=[]
        for key,label in [('replay_url','Replay'),('video_url','视频'),('record_url','记录')]:
            if r.get(key): links.append(f'<a href="{esc(r[key])}">{label}</a>')
        for previous in r.get('previous_attempts',[]):
            if previous.get('replay_url'):
                links.append(f'<a href="{esc(previous["replay_url"])}">历史尝试</a>')
        outcome='成功' if r.get('task_success') is True else ('未成功' if r.get('task_success') is False else '未取得最终评分')
        rows.append(f'<tr data-status="{esc(r["status"])}"><td>{r["index"]+1}</td><td>{esc(r["name"])}<small>{esc(r["task"])}</small><details><summary>任务输入与来源</summary><p>{esc(r["instruction"])}</p><p>{esc(r["instruction_source"])}</p></details></td><td class="{r["status"]}">{esc(r["status"])}<small>{esc(r.get("stage","queued"))}</small></td><td>{outcome}<small>Q={esc(r.get("q_score","—"))}</small></td><td>{esc(r.get("actions","—"))}<small>{esc(r.get("tool_calls","—"))} tool calls</small></td><td>{esc(r.get("evidence_alignment","—"))}<small>video: {esc(r.get("video_validation","—"))}</small></td><td>{" · ".join(links)}<small>{esc(r.get("failure",""))}</small></td></tr>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BEHAVIOR 100 · 批量测试</title>
<style>body{font:16px system-ui;background:#eef3f8;color:#142c43;margin:25px auto;max-width:1500px;padding:0 18px}a{color:#07599b}article,.cards>div{background:white;border:1px solid #d3dfe9;border-radius:10px;padding:18px;margin:14px 0}.cards{display:flex;gap:14px;flex-wrap:wrap}.cards>div{flex:1;min-width:150px}.cards strong{font-size:30px;display:block}table{border-collapse:collapse;width:100%;min-width:1050px}td,th{text-align:left;border-bottom:1px solid #ddd;padding:12px;vertical-align:top}small{display:block;color:#586d81;margin-top:7px;overflow-wrap:anywhere}td:nth-child(2){max-width:360px}td:last-child{max-width:270px}summary{cursor:pointer}input,select{font:inherit;padding:8px;margin:8px}.table{overflow-x:auto}.passed{color:#087044}.failed{color:#ad3030}.blocked{color:#9c6700}.running{color:#065fa7}p{line-height:1.65}code{overflow-wrap:anywhere}</style>
<h1>BEHAVIOR 2026 · 100 项任务测试</h1><p>RGB agent + ideal motor executor · gpt-6-astra · public instance 301 · seed 0</p>''' + f'''
<div class="cards"><div><strong>{s['completed']} / {s['total']}</strong>已结束（包括失败和受阻）</div><div><strong>{s['task_successes']} / {s['total']}</strong>独立评估成功 / 固定总数</div><div><strong>{s['final_evaluations']}</strong>取得最终评分</div><div><strong>{s['complete_videos']}</strong>录像完整性验证通过</div></div>
<article><p><b>失败与重试：</b>首次尝试成功 {s['first_attempt_task_successes']} / {s['total']}；另有 {s['extra_infrastructure_attempts']} 次模型启动前的基础设施重试、{s['extra_policy_attempts']} 次执行器修复后重测。上方成功数含修复后的最新尝试，原始失败记录保留在该任务行，任务总数始终为 100。</p><p><b>协议：</b>每种任务运行一个公开测试实例，共 100 个 episode，不等于所有公开实例或官方排行榜提交。机器人输入是同时采集的前、后、左、右 RGB，无腕部相机；模型通过 9 个 MCP tools 和 4 个可读取 skills 操作当前理想执行器。每项上限 80 次动作、20,000 控制步、模型 30 分钟。切割、擦洗等能力未扩展，相关失败保留在总数中。</p><p><b>输入来源：</b>50 项使用官方原文，50 项按静态 BDDL 目标补写，逐项标注。独立评估、几何和 spectator 录像只供执行器或离线审阅，不作为模型观测。</p><p><b>回放：</b>连续录像记录实际控制步；模型等待时间不铺成静止画面。Replay 同步四路 RGB、模型公开 assistant 原文、工具参数和返回结果。不补写思考，不展示隐藏推理；原始私有运行记录保存在实验目录。</p><p><b>状态：</b>passed 要求任务成功、控制器正常结束、证据对齐、四相机观测和录像验证通过；failed/blocked 分别保留具体阶段。失败任务有最终评分时显示 Q，仿真在初始化前失败时无法生成观测或视频。部分录像不标成完整。</p><p>批次状态：{esc(s['execution_status'])} · 更新：{esc(progress['updated_at'])} · <a href="behavior100/progress.json">实时 JSON</a> · <a href="behavior100/manifest.json">冻结清单</a> · <a href="behavior100/validation.json">验证记录</a> · <a href="behavior100/journal.md">迭代日志</a></p></article>
<input id="search" placeholder="搜索任务 / 阶段"><select id="status"><option value="">所有状态</option><option>planned</option><option>running</option><option>passed</option><option>failed</option><option>blocked</option></select><label><input id="refresh" type="checkbox" checked>每 60 秒刷新</label><div class="table"><table><thead><tr><th>#</th><th>任务</th><th>运行状态</th><th>独立评估</th><th>动作数</th><th>证据与录像</th><th>回放 / 原始记录</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>''' + '''<script>const search=document.querySelector('#search'), status=document.querySelector('#status');function filter(){document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!(r.textContent.toLowerCase().includes(search.value.toLowerCase())&&(!status.value||r.dataset.status===status.value)))}search.oninput=status.onchange=filter;setInterval(()=>{if(document.querySelector('#refresh').checked&&!search.value&&!status.value)location.reload()},60000)</script></html>'''
    total = s['total']
    official=sum(r['instruction_source']=='official_gallery' for r in progress['tasks'])
    page=page.replace('50 项使用官方原文，50 项按静态 BDDL 目标补写',f'{official} 项使用官方原文，{total-official} 项按静态 BDDL 目标补写')
    page = page.replace('100 项任务测试',f'{total} 项任务测试').replace('任务总数始终为 100',f'任务总数始终为 {total}').replace('共 100 个 episode',f'共 {total} 个 episode')
    if progress.get('comparison'):
        c = progress['comparison']
        banner = '<article><b>'+esc(c.get('title','GT 导航修复后重测'))+'</b><p>'+esc(c['scope'])+'</p><p><a href="'+esc(c['baseline_url'])+'">上次测试账本</a> · <a href="'+esc(c['report_url'])+'">修复与验证报告</a> · <a href="behavior100_realtime.html">真实耗时视频</a></p><p>'+esc(c.get('display_note','本页仅统计新批次，不覆盖或合并旧成绩。其他任务未排队。'))+'</p></article>'
        page = page.replace('<div class="cards">',banner+'<div class="cards">',1)
    if progress.get('workers'):
        worker_rows=[]
        for w in progress['workers']:
            active=next((r for r in progress['tasks'] if r.get('worker_id')==w['id'] and r['status']=='running'),None)
            worker_rows.append('<li>'+esc(w['id'])+' · '+esc(w['host'])+' · GPU '+str(w['gpu'])+' · '+(esc(active['task'])+' / '+esc(active.get('stage')) if active else '等待下一项 / 已结束')+'</li>')
        panel='<article><b>独立仿真 workers</b><ul>'+''.join(worker_rows)+'</ul><p>源版本逐任务记录：'+esc(progress.get('run_source_commits',{}))+'。此前已完成和正在执行的尝试保留原始源码版本；不把不同渲染配置当成单变量实验。</p></article>'
        page=page.replace('<div class="cards">',panel+'<div class="cards">',1)
    return page


class Batch:
    @property
    def c(self):
        return getattr(self._worker_local, 'config', self._config)

    def __init__(self, config):
        self._config=config; self._worker_local=threading.local(); self.workers=worker_configs(config)
        self.root=Path(config['batch']); self.source=Path(__file__).resolve().parents[1]
        self.manifest=json.loads((self.root/'manifest.json').read_text()); self.reports=Path(config['reports'])
        self.lock=threading.RLock(); self.queue=queue.Queue(); self.rows=self.manifest['tasks']
        for directory in ('records','preflight','controllers','runs','launchers','logs'):
            (self.root/directory).mkdir(exist_ok=True)
        selection_path=self.root/'infrastructure_retries.json'
        selections=json.loads(selection_path.read_text()) if selection_path.exists() else {}
        for r in self.rows:
            saved=self.root/'records'/f"{r['run_id']}.json"
            selection=selections.get(str(r['index']))
            if selection:
                previous=[]
                for runid in selection.get('previous_run_ids',[r['run_id']]):
                    record=json.loads((self.root/'records'/f'{runid}.json').read_text())
                    if not prepolicy_failure(record,self.root/'controllers'/runid):
                        raise RuntimeError('Only failed attempts before any policy output may be retried here')
                    previous.append({k:v for k,v in record.items() if k!='previous_attempts'})
                r.update(run_id=selection['run_id'],attempt=len(previous)+1,previous_attempts=previous,retry_reason=selection['reason'])
                saved=self.root/'records'/f"{r['run_id']}.json"
            if saved.exists(): r.update(json.loads(saved.read_text()))
            if r['status'] == 'running' and r['run_id'] not in config.get('adopt_inflight',{}):
                raise RuntimeError('Unfinished attempt exists; reconcile its owned unit and archive before resume: '+r['run_id'])
        adoptions=config.get('adopt_inflight',{})
        for runid,worker_id in adoptions.items():
            row=next((r for r in self.rows if r['run_id']==runid),None)
            worker=next((w for w in self.workers if w['id']==worker_id),None)
            if row is None or worker is None: raise RuntimeError('Unknown adoption target')
            if row['status']=='running' and (row.get('gpu_index')!=worker['gpu'] or row.get('simulator_interpreter')!=worker['sim_python']):
                raise RuntimeError('Inflight task must retain its original execution lane')
        if source_version()['dirty']: raise RuntimeError('Batch source must be a clean frozen checkout')
        self.environment={**os.environ,'PYTHONPATH':str(self.source/'src')}
        missing=[k for k in config.get('required_controller_env',[]) if not self.environment.get(k)]
        if missing: raise RuntimeError('Required controller environment variables absent: '+','.join(missing))
        if config.get('controller_path'): self.environment['PATH']=config['controller_path']
        client=shutil.which('codex',path=self.environment.get('PATH'))
        if not client: raise RuntimeError('Model executable unavailable before any simulator launch')
        subprocess.run([client,'--version'],check=True,capture_output=True,text=True,timeout=20)

    def ssh(self, args, timeout=40):
        cmd=shlex.join(args) if isinstance(args,list) else args
        return subprocess.run(self.c['ssh']+[cmd],capture_output=True,text=True,timeout=timeout)

    def journal(self, message):
        with self.lock:
            with (self.root/'journal.md').open('a') as f: f.write(f'\n- {now()} · {message}\n')

    def publish(self):
        with self.lock:
            progress={'updated_at':now(),'source':source_version(),'supervisor':{'host':os.uname().nodename,'pid':os.getpid(),'unit':os.environ.get('MAS_UNIT'),'interpreter':sys.executable},
                      'summary':summarize(self.rows),'tasks':self.rows,'comparison':self.manifest.get('comparison'),
                      'workers':[{'id':w['id'],'host':w['ssh'][-1], 'gpu':w['gpu'],
                          'port':w['base_port']+w['gpu'], 'interpreter':w['sim_python'],
                          'recording_options':w.get('simulator_env',{})} for w in self.workers],
                      'draining':(self.root/'drain_requested.json').exists(),
                      'run_source_commits':dict(Counter((r.get('run_source') or r.get('source') or {}).get('commit','pending') for r in self.rows))}
            write_json(self.root/'progress.json',progress)
            write_json(self.root/'run_records.json',{'updated_at':now(),'runs':self.rows})
            validation={'status':'running' if progress['summary']['execution_status']=='running' else ('passed' if all(r['status']=='passed' for r in self.rows) else 'failed'),
                        'level':'real_rgb_simulator_batch','task_count':len(self.rows),'summary':progress['summary'],
                        'source':progress['source'],'official_submission_eligible':False,
                        'note':'Task success, controller termination, transport alignment and video completeness are separate fields.'}
            write_json(self.root/'validation.json',validation)
            public=self.reports/'behavior100';public.mkdir(parents=True,exist_ok=True)
            for name in ('progress.json','manifest.json','validation.json','journal.md'):
                temp=public/(name+'.tmp');shutil.copyfile(self.root/name,temp);temp.replace(public/name)
            target=self.reports/'behavior100.html'; tmp=target.with_suffix('.html.tmp')
            tmp.write_text(render_dashboard(progress));tmp.replace(target)

    def update(self, row, **values):
        with self.lock:
            row.update(values,updated_at=now())
            write_json(self.root/'records'/f"{row['run_id']}.json",row)
            self.publish()

    def unit_state(self, unit):
        p=self.ssh(['systemctl','--user','show',unit,'-p','ActiveState','-p','MainPID','-p','ExecMainStatus','-p','Result','-p','Description','-p','MemoryPeak'])
        return dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)

    def health(self, port):
        p=self.ssh(['curl','--noproxy','*','-fsS','--max-time','3',f'http://127.0.0.1:{port}/healthz'])
        return json.loads(p.stdout) if p.returncode==0 else None

    def run_one(self, row, gpu):
        runid=row['run_id']; port=self.c['base_port']+gpu
        unit=f"mas-b100-{row['index']:03d}-r{row.get('attempt',1)}-{self.c['batch_tag']}.service"
        remote_run=Path(self.c['data_root'])/'runs'/runid
        controller=self.root/'controllers'/runid
        own_unit=False
        self.update(row,status='running',stage='preflight',started_at=now(),gpu_index=gpu,
                    simulator_unit=unit,simulator_output=str(remote_run),controller_output=str(controller),
                    worker_id=self.c.get('id'), worker_host=self.c['ssh'][-1],
                    recording_options=self.c.get('simulator_env',{}))
        self.journal(f"START {runid}; GPU index {gpu}; unit {unit}; output {remote_run}.")
        try:
            check_cmd=[self.c['sim_python'],str(self.source/'scripts/behavior100_remote.py'),'preflight','--manifest',str(self.root/'manifest.json'),
                       '--gpu',str(gpu),'--port',str(port),'--data-root',self.c['data_root'],
                       '--memory-budget-gib',str(self.c.get('memory_budget_gib',28))]
            if self.c.get('minimum_free_gpu_mib'):
                check_cmd+=['--min-free-gpu-mib',str(self.c['minimum_free_gpu_mib'])]
            for attempt in range(61):
                p=self.ssh(check_cmd)
                if p.returncode: raise RuntimeError('Remote resource preflight command failed: '+p.stderr[-1200:])
                check=json.loads(p.stdout)
                if self.c.get('expected_gpu_uuid') and check.get('gpu_uuid')!=self.c['expected_gpu_uuid']:
                    raise RuntimeError('GPU UUID changed; refusing physical-index reassignment')
                write_json(self.root/'preflight'/f'{runid}_{attempt:02d}.json',check)
                if check['status']=='passed': break
                self.update(row,stage='waiting_for_resources',resource_checks=check['checks'])
                time.sleep(30)
            else: raise RuntimeError('Resource preflight remained blocked for 30 minutes')
            state=self.unit_state(unit)
            if state.get('ActiveState') in {'active','activating','deactivating'}: raise RuntimeError('Unit collision; refusing to reuse')
            if self.ssh(['test','-e',str(remote_run)]).returncode == 0: raise RuntimeError('Output exists; immutable attempt cannot be overwritten')
            # The launcher is data only, outside the frozen source tree.
            launcher=self.root/'launchers'/f'{runid}.sh'
            q=shlex.quote
            command=[self.c['sim_python'],str(self.source/'scripts/behavior100_remote.py'),'simulate','--manifest',str(self.root/'manifest.json'),
                     '--index',str(row['index']),'--run-id',runid,'--gpu',str(gpu),'--port',str(port),'--unit',unit,'--data-root',self.c['data_root'],
                     '--memory-budget-gib',str(self.c.get('memory_budget_gib',28))]
            if self.c.get('minimum_free_gpu_mib'):
                command+=['--min-free-gpu-mib',str(self.c['minimum_free_gpu_mib'])]
            overrides=''.join('\nexport '+key+'='+q(str(value)) for key,value in self.c.get('simulator_env',{}).items())
            launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexport GAP_BEHAVIOR_GPU_ID='+str(gpu)+'\nsource '+q(self.c['sim_env'])+overrides+
                                '\nexport PYTHONPATH='+q(str(self.source/'src'))+':${PYTHONPATH:-}\nexport OMNIGIBSON_APPDATA_PATH='+q(self.c['data_root']+'/cache/behavior100/gpu'+str(gpu))+
                                '\nmkdir -p "$OMNIGIBSON_APPDATA_PATH"\nexec '+shlex.join(command)+'\n')
            launch=['systemd-run','--user',f'--unit={unit}',f'--description=BEHAVIOR100 owned {runid}',
                    '-p','MemoryMax='+self.c.get('simulator_memory_max',str(self.c.get('memory_budget_gib',28))+'G'),'-p','CPUQuota=800%','-p',f"RuntimeMaxSec={self.manifest['simulator_runtime_max_seconds']}",
                    '-p','TimeoutStopSec=30','-p','KillMode=control-group','-p','SuccessExitStatus=2',
                    '-p','WorkingDirectory='+str(self.source),'/bin/bash',str(launcher)]
            p=self.ssh(launch);(self.root/'logs'/f'{runid}_launch.log').write_text(p.stdout+p.stderr)
            if p.returncode: raise RuntimeError('Simulator unit launch failed')
            own_unit=True
            self.update(row,stage='simulator_starting',gpu_uuid=check['gpu_uuid'],simulator_host=check['host'],
                        simulator_interpreter=self.c['sim_python'],source=source_version(),port=port)
            deadline=time.monotonic()+self.manifest['startup_timeout_seconds']
            while time.monotonic()<deadline:
                state=self.unit_state(unit)
                if state.get('ActiveState') not in {'active','activating'}: raise RuntimeError('Simulator exited before bridge ready: '+str(state))
                h=self.health(port)
                if h and h.get('ready') and not h.get('closed'):
                    if {t['name'] for t in h['tools']}!={t['name'] for t in tool_specs(self.manifest['agent_profile'])}: raise RuntimeError('Bridge tool profile mismatch')
                    write_json(self.root/'logs'/f'{runid}_health.json',h);break
                time.sleep(4)
            else: raise TimeoutError('Simulator startup exceeded fixed budget')
            self.update(row,stage='policy_running',simulator_pid=int(state['MainPID']),bridge_ready_at=now())
            self.journal(f"READY {runid}; host {check['host']}; PID {state['MainPID']}; GPU UUID {check['gpu_uuid']}; real RGB model policy starting.")
            remote=shlex.join(['env','PYTHONPATH='+str(self.source/'src'),self.c['sim_python'],'-m','manipulation_agent.mcp_server','--bridge',f'http://127.0.0.1:{port}'])
            cmd=[sys.executable,str(self.source/'scripts/run_codex_controller.py'),'--model',self.manifest['model'],
                 '--instruction',row['instruction'],'--mcp-command',self.c['ssh'][0],'--mcp-args-json',json.dumps(self.c['ssh'][1:]+[remote]),
                 '--output',str(controller),'--timeout',str(self.manifest['model_timeout_seconds']),'--agent-profile',self.manifest['agent_profile']]
            with (self.root/'logs'/f'{runid}_controller.log').open('w') as log:
                process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=self.environment)
                self.update(row,controller_wrapper_pid=process.pid,controller_host=os.uname().nodename,controller_interpreter=sys.executable)
                while process.poll() is None:
                    time.sleep(5)
                    if time.monotonic()-getattr(process,'last_heartbeat',0)>30:
                        process.last_heartbeat=time.monotonic()
                        self.update(row,heartbeat_at=now())
                self.update(row,controller_wrapper_exit_code=process.returncode,stage='finalizing')
            metadata=json.loads((controller/'controller.json').read_text()) if (controller/'controller.json').exists() else {}
            self.update(row,controller_pid=metadata.get('pid'),controller_status=metadata.get('status'),model_duration_seconds=metadata.get('duration_seconds'))
            if not metadata:
                self.update(row,failure='Controller did not create metadata; inspect original launcher traceback',failure_stage='controller_launch')
            if not metadata.get('formal_finish_observed'):
                self.journal(f"CONTROLLER FAILURE {runid}; supervisor requests finish aborted, recorded as supervisor intervention (not a model call).")
                self.update(row,supervisor_intervention=True)
                code="from manipulation_agent.bridge import rpc; print(rpc("+repr(f'http://127.0.0.1:{port}')+",'finish',{'outcome':'aborted','reason':'Batch supervisor: controller ended without formal finish'},'batch-supervisor-finish',timeout=30))"
                finish=self.ssh(['env','PYTHONPATH='+str(self.source/'src'),self.c['sim_python'],'-c',code],timeout=45)
                (self.root/'logs'/f'{runid}_forced_finish.log').write_text(finish.stdout+finish.stderr)
            for _ in range(24):
                state=self.unit_state(unit)
                if state.get('ActiveState') not in {'active','activating','deactivating'}: break
                time.sleep(5)
        except Exception as exc:
            self.update(row,failure=f'{type(exc).__name__}: {exc}',failure_stage=row.get('stage'))
            (self.root/'logs'/f'{runid}_supervisor_error.log').write_text(traceback.format_exc())
            self.journal(f"FAILURE {runid}; stage {row.get('stage')}; {type(exc).__name__}: {exc}")
        finally:
            if own_unit:
                try:
                    state=self.unit_state(unit)
                    if state.get('ActiveState') in {'active','activating','deactivating'}:
                        if state.get('Description') != f'BEHAVIOR100 owned {runid}': raise RuntimeError('Ownership check failed; refusing stop')
                        self.ssh(['systemctl','--user','stop',unit],timeout=60)
                    state=self.unit_state(unit)
                    self.update(row,unit_final_state=state)
                    p=self.ssh(['journalctl','--user','-u',unit,'--no-pager','-o','short-iso'],timeout=60)
                    (self.root/'logs'/f'{runid}_simulator.log').write_text(p.stdout+p.stderr)
                    self.archive(row,controller,remote_run)
                except Exception as exc:
                    self.update(row,archive_failure=str(exc))
                    self.journal(f"ARCHIVE FAILURE {runid}: {exc}")
            passed=row.get('task_success') is True and row.get('controller_status')=='passed' and row.get('evidence_alignment')=='passed' and row.get('video_validation')=='passed' and row.get('observation_validation')=='passed'
            self.update(row,status='passed' if passed else ('blocked' if not own_unit else 'failed'),stage='complete',finished_at=now())
            self.journal(f"END {runid}: status={row['status']}; task_success={row.get('task_success')}; evidence={row.get('evidence_alignment')}; video={row.get('video_validation')}; actions={row.get('actions')}. All failures remain in the denominator.")
            self.publish()

    def archive(self,row,controller,remote_run):
        dest=self.root/'runs'/row['run_id'];dest.mkdir(exist_ok=True)
        # rsync needs the SSH command without the final host argument.
        cmd=['rsync','-a','-e',shlex.join(self.c['ssh'][:-1]),self.c['ssh'][-1]+':'+str(remote_run)+'/',str(dest)+'/']
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
        (self.root/'logs'/f"{row['run_id']}_archive.log").write_text(p.stdout+p.stderr)
        simlog=self.root/'logs'/f"{row['run_id']}_simulator.log"
        if simlog.exists():shutil.copyfile(simlog,dest/'simulator.log')
        if not (dest/'run.json').exists():
            self.update(row,failure=row.get('failure') or 'No simulator recorder created',task_success=None,video_validation='unavailable_before_initialization')
            return
        raw=json.loads((dest/'run.json').read_text())
        if raw['status']=='running':
            write_json(dest/'termination.json',{'status':'failed','at':now(),'reason':row.get('failure') or 'Simulator ended without final recorder',
                       'unit':row['simulator_unit'],'unit_state':row.get('unit_final_state'),'final_evaluation_available':False})
        run=read_run(dest)
        self.update(row,task_success=run.get('task_success'),actions=run.get('actions'),tool_calls=run.get('tool_calls'),
                    sim_steps=run.get('sim_steps'),evaluation=run.get('evaluation'),q_score=(run.get('evaluation') or {}).get('official_metrics',{}).get('q_score',{}).get('final'),
                    run_source=run.get('source'),backend=run.get('backend'),agent_outcome=run.get('agent_outcome'),finish_reason=run.get('finish_reason'))
        errors=[]
        for script,filename in [('validate_async_observation.py','observation_validation.json'),('validate_episode_video.py','video_validation.json')]:
            cmd=[sys.executable,str(self.source/'scripts'/script)]
            if script=='validate_async_observation.py': cmd.append('--run-dir')
            cmd.append(str(dest))
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=300,env=self.environment)
            (dest/(script+'.log')).write_text(p.stdout+p.stderr)
            # Failed validation never gets converted to success merely because the file exists.
            if not (dest/filename).exists(): write_json(dest/filename,{'status':'failed','reason':'Validator could not complete','exit_code':p.returncode})
        controller_ready=(controller/'controller.json').is_file() and (controller/'model_events.jsonl').is_file()
        try:
            replay=render_replay(dest,controller if controller_ready else None)
        except Exception as exc:
            errors.append('Controller/replay join failed: '+str(exc))
            replay=render_replay(dest)
        video=json.loads((dest/'video_validation.json').read_text())
        audit=replay.get('audit') or {}
        public=self.reports/'manipulation_runs'/row['run_id']
        # This directory contains simulator artifacts and public-only model export.
        # Raw controller stream and authentication remain outside the web root.
        shutil.copytree(dest,public,dirs_exist_ok=True)
        prefix='manipulation_runs/'+row['run_id']+'/'
        observation=json.loads((dest/'observation_validation.json').read_text())
        self.update(row,video_validation=video['status'],observation_validation=observation['status'],evidence_alignment=audit.get('evidence_alignment','unavailable'),
                    model_usage=audit.get('usage'),replay_url=prefix+'replay.html',record_url=prefix+'run.json',
                    video_url=prefix+'episode.mp4' if (dest/'episode.mp4').exists() else None,
                    replay_errors=errors,recording_warnings=sum(1 for _ in (dest/'recording_warnings.jsonl').open()) if (dest/'recording_warnings.jsonl').exists() else 0,video_frames=video.get('frame_count'),video_seconds=video.get('duration_seconds'))
        # Checksums cover original evidence as well as exports; streamed for large videos.
        hashes=episode_artifact_hashes(row['run_id'],dest,controller)
        write_json(dest/'artifact_hashes.json',hashes)
        shutil.copyfile(dest/'artifact_hashes.json',public/'artifact_hashes.json')

    def adopt_one(self, row):
        """Finish an explicitly handed-over attempt with its original controller.

        The migration procedure stops only the old coordinator, preserving the
        live controller and remote simulator. An existing model is never
        restarted; if still initializing, its first policy starts only after
        readiness within the original startup deadline. Source/budgets remain.
        """
        controller=Path(row['controller_output']);remote_run=Path(row['simulator_output'])
        unit=row['simulator_unit'];port=row['port'];pid=row.get('controller_wrapper_pid')
        state=self.unit_state(unit)
        if state.get('ActiveState') in {'active','activating'}:
            if state.get('Description')!=f"BEHAVIOR100 owned {row['run_id']}" or (row.get('simulator_pid') and int(state['MainPID'])!=row['simulator_pid']):
                raise RuntimeError('Inflight simulator ownership check failed')
        self.update(row,worker_id=self.c['id'],worker_host=self.c['ssh'][-1],coordinator_adopted_at=now())
        self.journal('ADOPT '+row['run_id']+'; original model process, simulator, source and time budget retained.')
        metadata={};started_process=None
        try:
            if pid is None:
                started_process=self.start_adopted_policy(row)
                pid=started_process.pid
                deadline=time.time()+self.manifest['model_timeout_seconds']+300
            else:
                metadata=json.loads((controller/'controller.json').read_text())
                deadline=datetime.fromisoformat(metadata['started_at']).timestamp()+self.manifest['model_timeout_seconds']+300
            while controller_process_alive(pid,controller):
                if time.time()>deadline: raise RuntimeError('Original controller exceeded its existing timeout plus cleanup allowance')
                self.update(row,heartbeat_at=now());time.sleep(5)
            if started_process is not None:
                self.update(row,controller_wrapper_exit_code=started_process.wait(timeout=10))
            metadata=json.loads((controller/'controller.json').read_text())
        except Exception as exc:
            metadata={}
            self.update(row,failure=f'{type(exc).__name__}: {exc}',failure_stage='adopted_initialization_or_controller')
            self.journal('ADOPTION FAILURE '+row['run_id']+': '+str(exc))
        self.update(row,stage='finalizing',controller_pid=metadata.get('pid'),controller_status=metadata.get('status'),
                    model_duration_seconds=metadata.get('duration_seconds'))
        if not metadata.get('formal_finish_observed') and self.health(port):
            self.update(row,supervisor_intervention=True)
            code="from manipulation_agent.bridge import rpc; print(rpc("+repr(f'http://127.0.0.1:{port}')+",'finish',{'outcome':'aborted','reason':'Adopted original controller ended without formal finish'},'batch-supervisor-finish',timeout=30))"
            finish=self.ssh(['env','PYTHONPATH='+str(self.source/'src'),self.c['sim_python'],'-c',code],timeout=45)
            (self.root/'logs'/f"{row['run_id']}_forced_finish.log").write_text(finish.stdout+finish.stderr)
        for _ in range(24):
            state=self.unit_state(unit)
            if state.get('ActiveState') not in {'active','activating','deactivating'}:break
            time.sleep(5)
        if state.get('ActiveState') in {'active','activating','deactivating'}:
            if state.get('Description')!=f"BEHAVIOR100 owned {row['run_id']}":raise RuntimeError('Simulator ownership changed')
            self.ssh(['systemctl','--user','stop',unit],timeout=60)
        self.update(row,unit_final_state=self.unit_state(unit))
        log=self.ssh(['journalctl','--user','-u',unit,'--no-pager','-o','short-iso'],timeout=60)
        (self.root/'logs'/f"{row['run_id']}_simulator.log").write_text(log.stdout+log.stderr)
        self.archive(row,controller,remote_run)
        passed=row.get('task_success') is True and all(row.get(k)=='passed' for k in ['controller_status','evidence_alignment','video_validation','observation_validation'])
        self.update(row,status='passed' if passed else 'failed',stage='complete',finished_at=now())
        self.journal(f"END adopted {row['run_id']}: {row['status']}; task_success={row.get('task_success')}; original attempt preserved.")

    def start_adopted_policy(self, row):
        """Wait for an original initializing simulator, then start its first policy."""
        spec=self._config['adopt_startup'][row['run_id']]
        runtime=Path(spec['runtime']);deadline=float(spec['deadline_unix'])
        original=subprocess.check_output(['git','-C',str(runtime),'rev-parse','HEAD'],text=True).strip()
        if original!=row['source']['commit']:raise RuntimeError('Original inflight runtime source mismatch')
        controller=Path(row['controller_output']);unit=row['simulator_unit'];port=row['port']
        if (controller/'controller.json').exists():raise RuntimeError('Refusing to launch a duplicate model controller')
        while time.time()<deadline:
            state=self.unit_state(unit)
            if state.get('ActiveState') not in {'active','activating'}:raise RuntimeError('Original simulator exited during initialization')
            if state.get('Description')!=f"BEHAVIOR100 owned {row['run_id']}" or int(state['MainPID'])!=spec['simulator_pid']:
                raise RuntimeError('Original simulator identity changed during initialization')
            h=self.health(port)
            if h and h.get('ready') and not h.get('closed'):
                if {t['name'] for t in h['tools']}!={t['name'] for t in tool_specs(self.manifest['agent_profile'])}:
                    raise RuntimeError('Original bridge tool catalog mismatch')
                break
            self.update(row,heartbeat_at=now(),simulator_pid=spec['simulator_pid']);time.sleep(4)
        else:raise TimeoutError('Original simulator startup exceeded its unchanged deadline')
        self.update(row,stage='policy_running',bridge_ready_at=now(),simulator_pid=spec['simulator_pid'])
        remote=shlex.join(['env','PYTHONPATH='+str(runtime/'src'),self.c['sim_python'],'-m','manipulation_agent.mcp_server','--bridge',f'http://127.0.0.1:{port}'])
        cmd=[sys.executable,str(runtime/'scripts/run_codex_controller.py'),'--model',self.manifest['model'],
             '--instruction',row['instruction'],'--mcp-command',self.c['ssh'][0],
             '--mcp-args-json',json.dumps(self.c['ssh'][1:]+[remote]),'--output',str(controller),
             '--timeout',str(self.manifest['model_timeout_seconds']),'--agent-profile',self.manifest['agent_profile']]
        with (self.root/'logs'/f"{row['run_id']}_controller.log").open('x') as log:
            process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,
                env={**self.environment,'PYTHONPATH':str(runtime/'src')})
        self.update(row,controller_wrapper_pid=process.pid,controller_host=os.uname().nodename,controller_interpreter=sys.executable)
        self.journal('READY adopted initialization '+row['run_id']+'; first model policy starts against the unchanged original simulator and source.')
        return process

    def run(self,limit=None):
        self.publish()
        for row in self.rows:
            if row['status']=='planned': self.queue.put(row)
        if limit is not None:
            limited=queue.Queue()
            for _ in range(min(limit,self.queue.qsize())):limited.put(self.queue.get_nowait())
            self.queue=limited
        def worker(config):
            self._worker_local.config=config
            for row in self.rows:
                if row['status']=='running' and self._config.get('adopt_inflight',{}).get(row['run_id'])==config['id']:
                    self.adopt_one(row)
            while True:
                if (self.root/'drain_requested.json').exists():
                    self.journal('Drain requested; '+config['id']+' leaves queued tasks untouched.')
                    return
                try:row=self.queue.get_nowait()
                except queue.Empty:return
                self.run_one(row,config['gpu'])
        with ThreadPoolExecutor(max_workers=len(self.workers)) as pool:
            futures=[pool.submit(worker,config) for config in self.workers]
            for future in futures:future.result()
        self.publish()


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--limit',type=int);p.add_argument('--publish-only',action='store_true');p.add_argument('--bootstrap-first',action='store_true')
    a=p.parse_args();config=json.loads(a.config.read_text());root=Path(config['batch'])
    with (root/'supervisor.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        batch=Batch(config)
        if a.publish_only:batch.publish()
        elif a.bootstrap_first:
            first=next(r for r in batch.rows if r['status']=='planned')
            batch.run(1)
            if first.get('evidence_alignment')!='passed' or first.get('video_validation')!='passed':
                raise RuntimeError('First episode did not validate the real model/replay pipeline; remaining tasks stay planned')
            batch.journal('Real model/replay pipeline passed; continuing all remaining planned tasks on the configured GPU slots.')
            batch.run(a.limit)
        else:batch.run(a.limit)


if __name__=='__main__':main()
