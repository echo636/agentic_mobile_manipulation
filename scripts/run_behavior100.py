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
import signal
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
from manipulation_agent.batch_lifecycle import WorkerLease, classify_episode_outcome, validate_manifest_coverage, require_completed_scope, preferred_gpu_worker, gpu_lease_available
from manipulation_agent.startup_progress import STARTUP_POLICY, startup_progress_update

FINAL = {'passed','failed','blocked'}


def inline_systemd_launcher(script):
    """Deliver launcher bytes without waiting for cross-host NAS visibility."""
    # systemd expands ExecStart dollars before bash runs. Preserve them for the
    # sourced simulator environment, including ${PYTHONPATH:-} and shell quotes.
    return ['/bin/bash','-c',script.replace('$','$$')]


def evaluation_summary(evaluation):
    """Keep combinatorial goal arrays in the immutable episode, not live progress."""
    if not isinstance(evaluation, dict):
        return evaluation
    return {key: value for key, value in evaluation.items()
            if key not in {'goal_options', 'initial_goal_options'}}


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


def infrastructure_failure(row):
    """Keep normal scored policy/action failures separate from broken runs."""
    if not isinstance(row.get('task_success'),bool):
        return 'missing_final_score'
    if row.get('archive_failure'):
        return 'archive_failure'
    for field in ('video_validation','observation_validation','evidence_alignment'):
        if row.get(field)!='passed':
            return field
    return None


def worker_configs(config):
    """Resolve explicit host/GPU lanes; legacy single-host configs still work."""
    lanes = config.get('workers')
    if lanes is None:
        lanes = [{'id':f'gpu{gpu}', 'gpu':gpu} for gpu in config['gpus']]
    if not lanes:
        raise ValueError('At least one worker is required')
    allowed = {'id', 'gpu', 'ssh', 'data_root', 'sim_python', 'sim_env',
               'base_port', 'minimum_free_gpu_mib', 'simulator_memory_max',
               'simulator_env', 'expected_gpu_uuid', 'memory_budget_gib', 'admission_delay_seconds',
               'simulator_appdata_path'}
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
        if merged.get('simulator_appdata_path') and not Path(merged['simulator_appdata_path']).is_absolute():
            raise ValueError('Simulator appdata path must be absolute')
        ids.add(worker_id);devices.add((host,gpu));ports.add((host,port));result.append(merged)
    # Workers are candidates, not simultaneous reservations. Shared host/GPU
    # leases below constrain actual admission across both comparison arms.
    slot_size=config.get('host_slot_memory_gib',28)
    for worker in result:
        limit=config.get('host_worker_memory_budget_gib',{}).get(worker['ssh'][-1],slot_size)
        if worker.get('memory_budget_gib',28)>slot_size or limit<slot_size:
            raise ValueError('Worker memory exceeds shared host slot capacity')
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
    if record.get('status')!='failed': return False
    stream=controller/'model_events.jsonl'
    if 'actions' not in record:
        # A pre-recorder startup failure has no action counter. Admit only the
        # explicitly pre-policy stages, without inventing fields in its record.
        if record.get('failure_stage') not in {'asset_preflight','simulator_starting'}:
            return False
        started=('controller_pid','controller_wrapper_pid','controller_status',
                 'execution_started_at_unix','episode_deadline_unix','bridge_ready_at')
        if any(record.get(key) is not None for key in started): return False
        if (controller/'controller.json').exists(): return False
        return not stream.exists() or stream.stat().st_size==0
    if record.get('actions')!=0: return False
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
    outcomes=Counter(classify_episode_outcome(r) for r in rows)
    return {'total':len(rows), 'completed':completed, 'counts':counts, 'task_successes':outcomes['success'],
            'official_goal_successes':successes,
            'outcome_counts':{name:outcomes[name] for name in ('success','failure','timeout')},
            'active_executions':sum(r.get('status')=='running' and r.get('stage') in
                {'simulator_starting','controller_starting','policy_running','finishing'} for r in rows),
            'first_attempt_task_successes':sum(r.get('task_success') is True for r in first),
            'extra_infrastructure_attempts':infrastructure,
            'extra_policy_attempts':extra-infrastructure,
            'success_fraction_all_tasks': outcomes['success']/len(rows) if rows else 0,
            'final_evaluations':sum(r.get('task_success') is not None for r in rows),
            'complete_videos':sum(r.get('video_validation')=='passed' for r in rows),
            'execution_status':'completed' if completed == len(rows) else 'running'}


def render_dashboard(progress):
    if progress.get('protocol',{}).get('comparison_arm'):
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from batch_comparison import render_arm_dashboard
        return render_arm_dashboard(progress)
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
        self.worker_states={};self._archive_pool=None
        validate_manifest_coverage(self.rows,config.get('expected_task_count'))
        expected_tasks={r['index']:r['task'] for r in self.rows}
        for directory in ('records','preflight','controllers','runs','launchers','logs'):
            (self.root/directory).mkdir(exist_ok=True)
        (self.root/'journal.md').touch(exist_ok=True)
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
            if (r['status']=='running' or (r.get('simulator_cleanup') or {}).get('status')=='blocked') and r['run_id'] not in config.get('adopt_inflight',{}):
                if r.get('execution_finished_at') and (r.get('simulator_cleanup') or {}).get('status')=='passed':
                    continue  # CPU archival is recovered independently below.
                match=next((w for w in self.workers if w['gpu']==r.get('gpu_index') and
                    w['ssh'][-1]==r.get('worker_host') and w['sim_python']==r.get('simulator_interpreter')),None)
                if not match:raise RuntimeError('Unfinished attempt has no matching original execution lane: '+r['run_id'])
                config.setdefault('adopt_inflight',{})[r['run_id']]=match['id']
        validate_manifest_coverage(self.rows,config.get('expected_task_count'),expected_tasks)
        adoptions=config.get('adopt_inflight',{})
        for runid,worker_id in adoptions.items():
            row=next((r for r in self.rows if r['run_id']==runid),None)
            worker=next((w for w in self.workers if w['id']==worker_id),None)
            if row is None or worker is None: raise RuntimeError('Unknown adoption target')
            if row['status']=='running' and (row.get('gpu_index')!=worker['gpu'] or row.get('simulator_interpreter')!=worker['sim_python']):
                raise RuntimeError('Inflight task must retain its original execution lane')
        self.source_info=source_version()
        if self.source_info['dirty']: raise RuntimeError('Batch source must be a clean frozen checkout')
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
            progress={'updated_at':now(),'source':self.source_info,'supervisor':{'host':os.uname().nodename,'pid':os.getpid(),'unit':os.environ.get('MAS_UNIT'),'interpreter':sys.executable},
                      'protocol':{k:v for k,v in self.manifest.items() if k!='tasks'},'summary':summarize(self.rows),'tasks':self.rows,'comparison':self.manifest.get('comparison'),
                      'workers':[{'id':w['id'],'host':w['ssh'][-1], 'gpu':w['gpu'],
                          'port':w['base_port']+w['gpu'], 'interpreter':w['sim_python'],
                          'recording_options':w.get('simulator_env',{})} for w in self.workers],
                      'draining':(self.root/'drain_requested.json').exists(),
                      'worker_states':self.worker_states,
                      'run_source_commits':dict(Counter((r.get('run_source') or r.get('source') or {}).get('commit','pending') for r in self.rows))}
            progress['summary']['waiting_resource_workers']=sum(v.get('stage') in
                {'waiting_for_lease','waiting_for_resources'} for v in self.worker_states.values())
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

    def cleanup_owned_unit(self, row):
        """Verify this lane is reusable; episode evidence never decides that."""
        unit=row['simulator_unit'];runid=row['run_id'];state={}
        try:
            state=self.unit_state(unit)
            if state.get('ActiveState') in {'active','activating','deactivating'}:
                if state.get('Description')!=f'BEHAVIOR100 owned {runid}':
                    raise RuntimeError('Simulator ownership changed; refusing stop')
                stopped=self.ssh(['systemctl','--user','stop',unit],timeout=60)
                if stopped.returncode:
                    raise RuntimeError('Owned simulator stop command failed: '+stopped.stderr[-1200:])
                state=self.unit_state(unit)
            # A collected transient unit may no longer retain its Description.
            # Only the live unit being signaled needs the ownership match.
            if state.get('ActiveState') not in {'inactive','failed'} or str(state.get('MainPID'))!='0':
                raise RuntimeError('Simulator termination could not be verified')
            cleanup={'status':'passed','at':now(),'unit':unit,'state':state}
        except Exception as exc:
            cleanup={'status':'blocked','at':now(),'unit':unit,'state':state,
                     'reason':f'{type(exc).__name__}: {exc}'}
            self.journal('WORKER CLEANUP BLOCKED '+runid+': '+cleanup['reason'])
        self.update(row,unit_final_state=state,simulator_cleanup=cleanup)
        return cleanup

    def hold_worker_for_cleanup(self, row):
        """Keep an unresolved simulator on its own lane, without draining peers."""
        cleanup=row.get('simulator_cleanup') or {}
        if cleanup.get('status')!='blocked':
            return False
        hold={'status':'blocked','at':now(),'worker_id':self.c['id'],
              'host':self.c['ssh'][-1],'gpu':self.c['gpu'],
              'run_id':row['run_id'],'unit':row.get('simulator_unit'),
              'reason':cleanup['reason']}
        self.update(row,worker_hold=hold)
        self.journal('WORKER HELD '+self.c['id']+' after '+row['run_id']+
                     '; other lanes continue: '+cleanup['reason'])
        return True

    def worker_state(self, stage, **details):
        with self.lock:
            self.worker_states[self.c['id']]={'stage':stage,'at':now(),
                'host':self.c['ssh'][-1],'gpu':self.c['gpu'],**details}
            self.publish()

    def acquire_worker_lease(self, *, adopting=None):
        directory=self._config.get('shared_lease_dir',str(self.root.parent/'resource_leases'))
        slot_size=self._config.get('host_slot_memory_gib',28)
        host_budget=self._config.get('host_worker_memory_budget_gib',{}).get(self.c['ssh'][-1],slot_size)
        lease=WorkerLease(directory,self.c,host_budget_gib=host_budget,slot_memory_gib=slot_size,
                          owner={'batch':str(self.root),'coordinator_pid':os.getpid()})
        if not lease.acquire():
            self.worker_state('waiting_for_lease')
            return None
        # A supervisor crash releases flock, but its remote simulator may live.
        # Do not reuse that slot until its unit is terminal, or explicitly adopt
        # exactly that existing attempt without starting another model.
        try:
            for previous in lease.previous_owners:
                if previous.get('released') or not previous.get('unit'):
                    continue
                if adopting and previous.get('run_id')==adopting['run_id']:
                    continue
                state=self.unit_state(previous['unit'])
                if state.get('ActiveState') not in {'inactive','failed'} or state.get('MainPID')!='0':
                    lease.release()
                    self.worker_state('waiting_for_lease',previous_unit=previous['unit'])
                    return None
            lease.record(released=False,run_id=adopting['run_id'] if adopting else None,
                         unit=adopting.get('simulator_unit') if adopting else None)
        except BaseException:
            lease.release()
            raise
        return lease

    def resource_admission(self):
        """No task is claimed here; immutable host checks run once per worker."""
        self.worker_state('admitting')
        args=[self.c['sim_python'],str(self.source/'scripts/behavior100_remote.py'),'preflight',
              '--manifest',str(self.root/'manifest.json'),'--gpu',str(self.c['gpu']),
              '--port',str(self.c['base_port']+self.c['gpu']),'--data-root',self.c['data_root'],
              '--memory-budget-gib',str(self.c.get('memory_budget_gib',28)),
              '--min-free-gpu-mib',str(self.c.get('minimum_free_gpu_mib',0))]
        lease=getattr(self._worker_local,'lease',None)
        if lease:args+=['--reserved-host-memory-gib',str(lease.other_reserved_gib(self._config.get('host_slot_memory_gib',28)))]
        if getattr(self._worker_local,'static_ready',False):args.append('--light')
        response=self.ssh(args)
        if response.returncode:
            self.worker_state('waiting_for_resources',reason='Resource query unavailable')
            return False
        check=json.loads(response.stdout)
        write_json(self.root/'preflight'/('worker_'+self.c['id']+'.json'),check)
        if self.c.get('expected_gpu_uuid') and check.get('gpu_uuid')!=self.c['expected_gpu_uuid']:
            raise RuntimeError('Permanent admission error: physical GPU UUID changed')
        static=('video_encoder','pinned_source_imports','validated_driver_floor')
        if any(check.get('checks',{}).get(name) is False for name in static):
            raise RuntimeError('Permanent admission error: static host environment check failed')
        self._worker_local.static_ready=True
        if check.get('status')!='passed':
            self.worker_state('waiting_for_resources',checks=check.get('checks',{}))
            return False
        if self._config.get('prefer_gpu_headroom', True):
            directory=self._config.get('shared_lease_dir',str(self.root.parent/'resource_leases'))
            with self.lock:
                preferred = preferred_gpu_worker(self.workers, self.worker_states,
                    self.c['ssh'][-1], check.get('queries', {}).get('gpus', {}).get('stdout', ''),
                    gpu_available=lambda worker: worker['id']==self.c['id'] or gpu_lease_available(directory,worker))
            if preferred is not None and preferred != self.c['id']:
                self.worker_state('waiting_for_resources', reason='Prefer available GPU with more free memory',
                                  preferred_worker=preferred)
                return False
        self._worker_local.admission=check
        return True

    def recover_worker_cleanup(self, row):
        """Retain both leases while only this lane retries owned cleanup."""
        while (row.get('simulator_cleanup') or {}).get('status')=='blocked':
            self.hold_worker_for_cleanup(row)
            self.worker_state('cleanup_pending',run_id=row['run_id'],
                              reason=row['simulator_cleanup'].get('reason'))
            time.sleep(self._config.get('cleanup_retry_seconds',10))
            self.cleanup_owned_unit(row)
        row.pop('worker_hold',None)

    def stop_controller(self, process, controller, wrapper_pid=None):
        """Bounded cleanup of only the model/wrapper owned by this episode."""
        if process is None:
            class AdoptedProcess:
                def poll(self):return None if controller_process_alive(wrapper_pid,controller) else 0
                def wait(self,timeout):
                    end=time.monotonic()+timeout
                    while self.poll() is None:
                        if time.monotonic()>=end:raise subprocess.TimeoutExpired('adopted controller',timeout)
                        time.sleep(.1)
                    return 0
                def terminate(self):
                    if self.poll() is None:os.kill(wrapper_pid,signal.SIGTERM)
                def kill(self):
                    if self.poll() is None:os.kill(wrapper_pid,signal.SIGKILL)
            process=AdoptedProcess()
        metadata_path=controller/'controller.json'
        metadata=json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        pid=metadata.get('pid')
        identity=None
        def owned_model():
            nonlocal identity
            if not pid:return False
            proc=Path('/proc')/str(pid)
            try:
                args=(proc/'cmdline').read_bytes().decode().split('\0')
                started=(proc/'stat').read_text().rsplit(') ',1)[1].split()[19]
                if proc.stat().st_uid!=os.getuid() or not any(str(controller) in arg for arg in args) or os.getpgid(pid)!=pid:
                    return False
                if identity is None:identity=started
                return identity==started
            except (ProcessLookupError,FileNotFoundError):return False
        if owned_model():
            try:os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if owned_model():
                try:
                    os.killpg(pid,signal.SIGKILL)
                except (ProcessLookupError,FileNotFoundError):pass
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)

    def finalize_episode(self, row, controller, remote_run, config):
        """CPU archival never owns the GPU/host lease or changes task outcome."""
        self._worker_local.config=config
        self.update(row,stage='archiving',archive_status='running')
        try:self.capture_result(row,remote_run)
        except Exception as exc:self.update(row,result_capture_error=f'{type(exc).__name__}: {exc}')
        try:
            self.archive(row,controller,remote_run)
            self.update(row,archive_status='passed')
        except Exception as exc:
            self.update(row,archive_failure=f'{type(exc).__name__}: {exc}',archive_status='failed')
        self.complete_row(row)

    def capture_result(self, row, remote_run):
        """Fetch the compact immutable outcome before potentially large media IO."""
        keys=('task_success','actions','tool_calls','sim_steps','agent_outcome','finish_reason','failure',
              'source','backend','execution_finished_at_unix','deadline_expired_at_finish',
              'evaluation_finished_at_unix','evaluation_finished_after_deadline','episode_deadline_unix','execution_started_at_unix','episode_outcome')
        code=("import json; from pathlib import Path; p=Path("+repr(str(remote_run/'run.json'))+
              "); r=json.loads(p.read_text()); d={k:r[k] for k in "+repr(keys)+" if k in r}; "
              "e=r.get('evaluation') or {}; d['evaluation']={k:v for k,v in e.items() if k not in ('goal_options','initial_goal_options')}; print(json.dumps(d))")
        result=self.ssh([self.c['sim_python'],'-c',code],timeout=30)
        if result.returncode:raise RuntimeError('Final outcome unavailable remotely')
        raw=json.loads(result.stdout)
        values={key:raw[key] for key in keys if key in raw and key not in {'failure','source','episode_outcome'}}
        values.update(evaluation=raw.get('evaluation'),q_score=(raw.get('evaluation') or {}).get('official_metrics',{}).get('q_score',{}).get('final'),
                      result_capture_status='passed')
        if raw.get('source'):values['run_source']=raw['source']
        if raw.get('failure') and raw.get('task_success') is None:values['simulator_failure_type']=raw['failure']
        if row.get('outcome_provisional') and isinstance(raw.get('task_success'),bool):
            values.update(episode_outcome=None,outcome_provisional=False,termination_reason=None)
        self.update(row,**values)

    def complete_row(self, row):
        # Media/evidence errors remain visible, but cannot turn a valid scored
        # task into a policy failure or turn a deadline into a late success.
        row.setdefault('execution_finished_at',now())
        outcome=classify_episode_outcome(row)
        reason=row.get('termination_reason')
        if not reason:
            reason=('episode_deadline_exceeded' if outcome=='timeout' else
                    'task_completed' if outcome=='success' else
                    'execution_error' if row.get('failure') else
                    'controller_failed' if row.get('controller_status')!='passed' else
                    'task_goal_not_satisfied')
        self.update(row,status='passed' if outcome=='success' else 'failed',episode_outcome=outcome,
                    termination_reason=reason,stage='complete',finished_at=now())
        self.journal(f"END {row['run_id']}: outcome={outcome}; reason={reason}; task_success={row.get('task_success')}; Q={row.get('q_score')}.")

    def execution_clock_command(self, path, seconds, runtime=None):
        runtime=Path(runtime or self.source)
        code=('import json; from manipulation_agent.deadline import write_execution_clock; '
              'print(json.dumps(write_execution_clock('+repr(str(path))+','+repr(seconds)+')))')
        remote=shlex.join(['env','PYTHONPATH='+str(runtime/'src'),self.c['sim_python'],'-c',code])
        return self.c['ssh']+[remote]

    def controller_metadata(self, row, controller):
        path=controller/'controller.json'
        try:metadata=json.loads(path.read_text())
        except (FileNotFoundError,json.JSONDecodeError):return {}
        deadline=metadata.get('episode_deadline_unix')
        started=metadata.get('execution_started_at_unix')
        if isinstance(deadline,(int,float)) and isinstance(started,(int,float)):
            if row.get('episode_deadline_unix')!=deadline:
                self.update(row,execution_started_at_unix=started,episode_deadline_unix=deadline,
                            execution_budget_seconds=metadata.get('execution_budget_seconds',row.get('episode_timeout_seconds')),
                            deadline_origin='model_start_after_mcp_handshake',
                            budget_basis='model_execution_excludes_initialization',stage='policy_running')
                self.worker_state('running',run_id=row['run_id'],deadline_unix=deadline)
        return metadata

    def wait_controller(self, row, process, controller):
        """Simulator/handshake startup and actual model execution have separate clocks."""
        while process.poll() is None:
            self.controller_metadata(row,controller)
            self.refresh_startup_progress(row)
            deadline=row.get('episode_deadline_unix')
            cutoff=deadline or row['startup_deadline_unix']
            if time.time()>=cutoff:
                if deadline:self.update(row,episode_timed_out=True)
                else:self.update(row,startup_timed_out=True,termination_reason='startup_timeout')
                if deadline:
                    try:process.wait(timeout=min(20,row.get('cleanup_grace_seconds',120)))
                    except subprocess.TimeoutExpired:self.stop_controller(process,controller)
                else:self.stop_controller(process,controller)
                break
            time.sleep(min(2,max(0,cutoff-time.time())))
            if time.monotonic()-getattr(process,'last_heartbeat',0)>30:
                process.last_heartbeat=time.monotonic();self.update(row,heartbeat_at=now())
        return self.controller_metadata(row,controller)

    def refresh_startup_progress(self, row):
        """Read actual remote milestones, never mistake polling for progress."""
        if row.get('startup_watchdog_policy') != STARTUP_POLICY or row.get('episode_deadline_unix'):
            return
        # Avoid launching a Python reader on every two-second health poll.
        last=getattr(self._worker_local,'startup_progress_poll',None)
        current=time.time()
        if (last and last[0]==row['run_id'] and current-last[1]<10
                and current<row['startup_deadline_unix']):
            return
        self._worker_local.startup_progress_poll=(row['run_id'],current)
        path=Path(row['simulator_output'])/'startup_stages.jsonl'
        code=("import json; from pathlib import Path; p=Path("+repr(str(path))+
              "); result=[]\n"
              "for line in (p.read_text().splitlines() if p.exists() else []):\n"
              " try: result.append(json.loads(line))\n"
              " except ValueError: pass\n"
              "print(json.dumps(result))")
        try:
            result=self.ssh(['/usr/bin/python3','-c',code],timeout=15)
            if result.returncode:return
            events=json.loads(result.stdout)
            if not isinstance(events,list):return
            updates=startup_progress_update(row,events,time.time())
        except (subprocess.TimeoutExpired,OSError,ValueError):
            return  # Unreadable progress cannot extend the watchdog.
        if updates:self.update(row,**updates)

    def run_one(self, row, gpu):
        runid=row['run_id'];port=self.c['base_port']+gpu
        unit=f"mas-b100-{row['index']:03d}-r{row.get('attempt',1)}-{self.c['batch_tag']}.service"
        remote_run=Path(self.c['data_root'])/'runs'/runid
        controller=self.root/'controllers'/runid
        own_unit=False;launch_requested=False;process=None;deadline=None;startup_deadline=None
        native_fault_capture=self.c.get('native_fault_capture',False) is True
        self.update(row,status='running',stage='asset_preflight',started_at=now(),gpu_index=gpu,
                    simulator_unit=unit,simulator_output=str(remote_run),controller_output=str(controller),
                    worker_id=self.c['id'],worker_host=self.c['ssh'][-1],runtime_source=str(self.source),
                    port=port,simulator_interpreter=self.c['sim_python'],source=self.source_info,
                    recording_options=self.c.get('simulator_env',{}),
                    native_fault_capture=native_fault_capture)
        self.journal(f'START {runid}; admitted worker {self.c["id"]}; unit {unit}.')
        try:
            asset_cmd=[self.c['sim_python'],str(self.source/'scripts/behavior100_remote.py'),'assets',
                       '--manifest',str(self.root/'manifest.json'),'--index',str(row['index']),
                       '--data-root',self.c['data_root']]
            checked=self.ssh(asset_cmd)
            if checked.returncode:raise RuntimeError('Task asset preflight command failed')
            asset_check=json.loads(checked.stdout)
            write_json(self.root/'preflight'/f'{runid}_assets.json',asset_check)
            if asset_check['status']!='passed':
                self.update(row,termination_reason='invalid_task_assets')
                raise RuntimeError('Task assets missing or incompatible; simulator was not started')
            state=self.unit_state(unit)
            if state.get('ActiveState') in {'active','activating','deactivating'}:
                raise RuntimeError('Unit collision; refusing to reuse')
            if self.ssh(['test','-e',str(remote_run)]).returncode==0:
                raise RuntimeError('Output exists; immutable attempt cannot be overwritten')
            # Initialization has its own watchdog. The 30-minute execution
            # clock is armed by the controller only after its MCP handshake.
            seconds=self._config.get('episode_timeout_seconds',self.manifest.get('model_timeout_seconds',1800))
            startup_seconds=self._config.get('startup_timeout_seconds',1800)
            started=time.time();startup_deadline=started+startup_seconds
            clock_path=remote_run/'execution_clock.json'
            grace=self._config.get('cleanup_grace_seconds',120)
            launcher=self.root/'launchers'/f'{runid}.sh';q=shlex.quote
            command=[self.c['sim_python'],str(self.source/'scripts/behavior100_remote.py'),'simulate',
                '--manifest',str(self.root/'manifest.json'),'--index',str(row['index']),'--run-id',runid,
                '--gpu',str(gpu),'--port',str(port),'--unit',unit,'--data-root',self.c['data_root'],
                '--memory-budget-gib',str(self.c.get('memory_budget_gib',28)),
                '--min-free-gpu-mib',str(self.c.get('minimum_free_gpu_mib',0)),
                '--light']
            # Optional external first-fault capture; no in-process tracing thread.
            # GDB follows the helper's execv into vision_cli and preserves normal
            # exit codes. The capture script creates files only on a native fault.
            fault_env=''
            if native_fault_capture:
                command=['/usr/bin/gdb','--batch','-q','-nx','-x',
                    str(self.source/'scripts/capture_native_fault.gdb'),'--args',*command]
                fault_env='\nexport MAS_NATIVE_FAULT_DIR='+q(str(remote_run))
            overrides=''.join('\nexport '+key+'='+q(str(value)) for key,value in self.c.get('simulator_env',{}).items())
            appdata=self.c.get('simulator_appdata_path',self.c['data_root']+'/cache/behavior100/gpu'+str(gpu))
            launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexport GAP_BEHAVIOR_GPU_ID='+str(gpu)+
                '\nsource '+q(self.c['sim_env'])+overrides+fault_env+'\nunset MAS_EPISODE_DEADLINE_UNIX'+
                '\nexport MAS_EXECUTION_CLOCK_PATH='+q(str(clock_path))+
                '\nexport PYTHONPATH='+q(str(self.source/'src'))+':${PYTHONPATH:-}\nexport OMNIGIBSON_APPDATA_PATH='+q(appdata)+
                '\nmkdir -p "$OMNIGIBSON_APPDATA_PATH"\nexec '+shlex.join(command)+'\n')
            launch=['systemd-run','--user',f'--unit={unit}',f'--description=BEHAVIOR100 owned {runid}',
                '-p','MemoryMax='+self.c.get('simulator_memory_max',str(self.c.get('memory_budget_gib',28))+'G'),
                '-p','CPUQuota=800%',
                '-p','TasksMax=2048','-p','LimitCORE=0','-p','TimeoutStopSec=30',
                '-p','KillMode=control-group','-p','SuccessExitStatus=2','-p','WorkingDirectory='+str(self.source)]
            launch+=inline_systemd_launcher(launcher.read_text())
            lease=getattr(self._worker_local,'lease',None)
            if lease:lease.record(run_id=runid,unit=unit,released=False)
            self.update(row,stage='simulator_starting',simulator_started_at_unix=started,
                        startup_deadline_unix=startup_deadline,startup_timeout_seconds=startup_seconds,
                        startup_watchdog_policy=STARTUP_POLICY,
                        execution_clock_path=str(clock_path),deadline_origin='model_start_after_mcp_handshake',
                        budget_basis='model_execution_excludes_initialization',
                        episode_timeout_seconds=seconds,
                        cleanup_grace_seconds=grace,port=port,simulator_interpreter=self.c['sim_python'],
                        source=getattr(self,'source_info',None) or source_version())
            launch_requested=True
            response=self.ssh(launch)
            (self.root/'logs'/f'{runid}_launch.log').write_text(response.stdout+response.stderr)
            if response.returncode:raise RuntimeError('Simulator unit launch failed')
            own_unit=True
            check=getattr(self._worker_local,'admission',{})
            state=self.unit_state(unit)
            self.update(row,stage='simulator_starting',simulator_owned=True,simulator_pid=int(state.get('MainPID',0)),
                gpu_uuid=check.get('gpu_uuid',self.c.get('expected_gpu_uuid')),simulator_host=check.get('host'),
                simulator_interpreter=self.c['sim_python'],source=getattr(self,'source_info',None) or source_version(),
                port=port,simulator_started_at_unix=started,startup_deadline_unix=startup_deadline,
                episode_timeout_seconds=seconds,cleanup_grace_seconds=grace)
            self.worker_state('starting',run_id=runid,startup_deadline_unix=startup_deadline)
            while True:
                self.refresh_startup_progress(row)
                if time.time()>=row['startup_deadline_unix']:
                    self.update(row,startup_timed_out=True,termination_reason='startup_timeout')
                    raise TimeoutError('Simulator initialization made no milestone progress before watchdog expiry')
                state=self.unit_state(unit)
                if state.get('ActiveState') not in {'active','activating'}:
                    raise RuntimeError('Simulator exited before bridge ready: '+str(state))
                health=self.health(port)
                if health and health.get('ready') and not health.get('closed'):
                    if {t['name'] for t in health['tools']}!={t['name'] for t in tool_specs(self.manifest['agent_profile'])}:
                        raise RuntimeError('Bridge tool profile mismatch')
                    write_json(self.root/'logs'/f'{runid}_health.json',health)
                    break
                time.sleep(min(2,max(0,row['startup_deadline_unix']-time.time())))
            self.update(row,stage='controller_starting',bridge_ready_at=now())
            self.worker_state('controller_starting',run_id=runid,startup_deadline_unix=row['startup_deadline_unix'])
            remote=shlex.join(['env','PYTHONPATH='+str(self.source/'src'),self.c['sim_python'],
                '-m','manipulation_agent.mcp_server','--bridge',f'http://127.0.0.1:{port}'])
            cmd=[sys.executable,str(self.source/'scripts/run_codex_controller.py'),'--model',self.manifest['model'],
                 '--instruction',row['instruction'],'--mcp-command',self.c['ssh'][0],
                 '--mcp-args-json',json.dumps(self.c['ssh'][1:]+[remote]),'--output',str(controller),
                 '--timeout',str(seconds),'--execution-clock-command-json',
                 json.dumps(self.execution_clock_command(clock_path,seconds)),
                 '--agent-profile',self.manifest['agent_profile']]
            if self.manifest.get('model_reasoning_effort'):
                cmd += ['--reasoning-effort',self.manifest['model_reasoning_effort']]
            if self.c.get('isolate_client_storage'):cmd.append('--isolate-client-storage')
            with (self.root/'logs'/f'{runid}_controller.log').open('w') as log:
                process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=self.environment)
                self.update(row,controller_wrapper_pid=process.pid,controller_host=os.uname().nodename,
                            controller_interpreter=sys.executable)
                self.wait_controller(row,process,controller)
                self.update(row,controller_wrapper_exit_code=process.returncode,stage='finishing')
            metadata=self.controller_metadata(row,controller)
            self.update(row,controller_pid=metadata.get('pid'),controller_status=metadata.get('status'),
                controller_timeout=bool(metadata.get('timeout')),model_duration_seconds=metadata.get('duration_seconds'))
            if not metadata:self.update(row,failure='Controller metadata unavailable',failure_stage='controller_launch')
            self.finish_and_wait(row,metadata)
        except Exception as exc:
            deadline=row.get('episode_deadline_unix')
            expired=deadline is not None and time.time()>=deadline
            if deadline is None and startup_deadline is not None and time.time()>=row['startup_deadline_unix']:
                self.update(row,startup_timed_out=True,termination_reason='startup_timeout')
            self.update(row,failure=f'{type(exc).__name__}: {exc}',failure_stage=row.get('stage'),
                        episode_timed_out=bool(row.get('episode_timed_out') or expired))
            (self.root/'logs'/f'{runid}_supervisor_error.log').write_text(traceback.format_exc())
            if process is not None and process.poll() is None:self.stop_controller(process,controller)
            if own_unit:
                try:self.finish_and_wait(row,{})
                except Exception as finish_error:
                    self.update(row,finish_failure=f'{type(finish_error).__name__}: {finish_error}')
        finally:
            # An SSH timeout may hide a successful remote systemd launch. The
            # declared unit remains reserved until cleanup is positively verified.
            if launch_requested:own_unit=True
            if own_unit:self.cleanup_owned_unit(row)
            self.update(row,execution_finished_at=now(),stage='cleanup_pending' if
                (row.get('simulator_cleanup') or {}).get('status')=='blocked' else 'execution_finished')
            if not own_unit:
                self.complete_row(row)
            elif not getattr(self._worker_local,'defer_archive',False):
                if (row.get('simulator_cleanup') or {}).get('status')=='passed':
                    self.finalize_episode(row,controller,remote_run,self.c)
                else:
                    self.update(row,archive_status='waiting_for_cleanup')
        return (controller,remote_run) if own_unit else None

    def finish_and_wait(self, row, metadata):
        """Execution deadline is fixed; score/cleanup receives a separate grace."""
        now_unix=time.time();deadline=row.get('episode_deadline_unix') or row.get('startup_deadline_unix') or now_unix
        end=min(deadline+row.get('cleanup_grace_seconds',120),now_unix+row.get('cleanup_grace_seconds',120))
        if not metadata.get('formal_finish_observed') and now_unix<end:
            self.update(row,supervisor_intervention=True,stage='finishing')
            timeout=max(1,end-time.time()-10)
            code=("from manipulation_agent.bridge import rpc; print(rpc("+repr(f'http://127.0.0.1:{row["port"]}')+
                ",'finish',{'outcome':'aborted','reason':'Controller ended or episode deadline reached'},'batch-supervisor-finish',timeout="+str(timeout)+"))")
            result=self.ssh(['env','PYTHONPATH='+str(self.source/'src'),self.c['sim_python'],'-c',code],timeout=timeout+5)
            (self.root/'logs'/f'{row["run_id"]}_forced_finish.log').write_text(result.stdout+result.stderr)
            self.update(row,supervisor_finish_exit_code=result.returncode)
        while time.time()<end:
            state=self.unit_state(row['simulator_unit'])
            if state.get('ActiveState') in {'inactive','failed'} and state.get('MainPID')=='0':return
            time.sleep(min(2,max(0,end-time.time())))

    def archive(self,row,controller,remote_run):
        dest=self.root/'runs'/row['run_id'];dest.mkdir(exist_ok=True)
        log=self.ssh(['journalctl','--user','-u',row['simulator_unit'],'--no-pager','-o','short-iso'],timeout=60)
        (self.root/'logs'/f"{row['run_id']}_simulator.log").write_text(log.stdout+log.stderr)
        # rsync needs the SSH command without the final host argument.
        cmd=['rsync','-a','-e',shlex.join(self.c['ssh'][:-1]),self.c['ssh'][-1]+':'+str(remote_run)+'/',str(dest)+'/']
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
        (self.root/'logs'/f"{row['run_id']}_archive.log").write_text(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('Episode rsync failed; remote raw output retained')
        simlog=self.root/'logs'/f"{row['run_id']}_simulator.log"
        if simlog.exists():shutil.copyfile(simlog,dest/'simulator.log')
        if not (dest/'run.json').exists():
            self.update(row,failure=row.get('failure') or 'No simulator recorder created',video_validation='unavailable_before_initialization')
            return
        raw=json.loads((dest/'run.json').read_text())
        if raw['status']=='running':
            write_json(dest/'termination.json',{'status':'failed','at':now(),'reason':row.get('failure') or 'Simulator ended without final recorder',
                       'unit':row['simulator_unit'],'unit_state':row.get('unit_final_state'),'final_evaluation_available':False})
        run=read_run(dest)
        if row.get('outcome_provisional') and isinstance(run.get('task_success'),bool):
            self.update(row,episode_outcome=None,outcome_provisional=False,termination_reason=None)
        self.update(row,**{key:run[key] for key in ('execution_finished_at_unix','deadline_expired_at_finish',
            'evaluation_finished_at_unix','evaluation_finished_after_deadline','episode_deadline_unix','execution_started_at_unix') if key in run})
        if run.get('deadline_expired_at_finish') is True or run.get('episode_outcome')=='timeout':
            self.update(row,episode_timed_out=True)
        if run.get('failure') and run.get('task_success') is None:
            self.update(row,failure=row.get('failure') or 'Simulator runtime failure: '+str(run['failure']),
                        failure_stage=row.get('failure_stage') or 'simulator_runtime',
                        simulator_failure_type=run['failure'])
        self.update(row,task_success=run.get('task_success'),actions=run.get('actions'),tool_calls=run.get('tool_calls'),
                    sim_steps=run.get('sim_steps'),evaluation=evaluation_summary(run.get('evaluation')),
                    evaluation_record='runs/'+row['run_id']+'/run.json',
                    q_score=(run.get('evaluation') or {}).get('official_metrics',{}).get('q_score',{}).get('final'),
                    run_source=run.get('source'),backend=run.get('backend'),agent_outcome=run.get('agent_outcome'),finish_reason=run.get('finish_reason'))
        errors=[]
        for script,filename in [('validate_async_observation.py','observation_validation.json'),('validate_episode_video.py','video_validation.json')]:
            cmd=[sys.executable,str(self.source/'scripts'/script)]
            if script=='validate_async_observation.py': cmd.extend(['--allow-sync-only','--run-dir'])
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
        """Resume supervision of this exact attempt; never restart its model."""
        controller=Path(row['controller_output']);remote_run=Path(row['simulator_output'])
        state=self.unit_state(row['simulator_unit']);process=None;metadata={}
        if state.get('ActiveState') in {'active','activating','deactivating'}:
            if state.get('Description')!=f"BEHAVIOR100 owned {row['run_id']}":
                raise RuntimeError('Inflight simulator ownership check failed')
        self.update(row,worker_id=self.c['id'],worker_host=self.c['ssh'][-1],coordinator_adopted_at=now())
        self.journal('ADOPT '+row['run_id']+'; original model, simulator, source and deadline retained.')
        if row.get('status') in FINAL and (row.get('simulator_cleanup') or {}).get('status')=='blocked':
            self.cleanup_owned_unit(row)
            return controller,remote_run
        try:
            path=controller/'controller.json'
            if path.exists():metadata=self.controller_metadata(row,controller)
            if not row.get('episode_deadline_unix') and not row.get('execution_clock_path'):
                started=datetime.fromisoformat(metadata.get('started_at') or row['started_at']).timestamp()
                row['episode_deadline_unix']=started+self.manifest['model_timeout_seconds']
            row.setdefault('cleanup_grace_seconds',self._config.get('cleanup_grace_seconds',120))
            pid=row.get('controller_wrapper_pid')
            if pid is None and not path.exists() and state.get('ActiveState') in {'active','activating'}:
                process=self.start_adopted_policy(row);pid=process.pid
            while pid and controller_process_alive(pid,controller):
                self.controller_metadata(row,controller)
                self.refresh_startup_progress(row)
                deadline=row.get('episode_deadline_unix')
                cutoff=deadline or row['startup_deadline_unix']
                if time.time()>=cutoff:
                    if deadline:self.update(row,episode_timed_out=True)
                    else:self.update(row,startup_timed_out=True,termination_reason='startup_timeout')
                    if process is not None:self.stop_controller(process,controller)
                    else:
                        cleanup_until=time.time()+(20 if deadline else 0)
                        while controller_process_alive(pid,controller) and time.time()<cleanup_until:time.sleep(.2)
                        if controller_process_alive(pid,controller):self.stop_controller(None,controller,wrapper_pid=pid)
                    break
                self.update(row,heartbeat_at=now());time.sleep(2)
            if process is not None:process.wait(timeout=10)
            if path.exists():metadata=self.controller_metadata(row,controller)
            self.update(row,controller_pid=metadata.get('pid'),controller_status=metadata.get('status'),
                        controller_timeout=bool(metadata.get('timeout')),model_duration_seconds=metadata.get('duration_seconds'))
            self.finish_and_wait(row,metadata)
        except Exception as exc:
            if not row.get('episode_deadline_unix') and row.get('startup_deadline_unix',float('inf'))<=time.time():
                self.update(row,startup_timed_out=True,termination_reason='startup_timeout')
            self.update(row,failure=f'{type(exc).__name__}: {exc}',failure_stage='adopted_controller')
        finally:
            self.cleanup_owned_unit(row)
            self.update(row,execution_finished_at=now())
        if not getattr(self._worker_local,'defer_archive',False) and row['simulator_cleanup']['status']=='passed':
            self.finalize_episode(row,controller,remote_run,self.c)
        return controller,remote_run

    def start_adopted_policy(self, row):
        """Wait for an original initializing simulator, then start its first policy."""
        spec=self._config.get('adopt_startup',{}).get(row['run_id']) or {
            'runtime':row['runtime_source'],'deadline_unix':row.get('startup_deadline_unix') or row['episode_deadline_unix'],
            'simulator_pid':row.get('simulator_pid')}
        runtime=Path(spec['runtime']);deadline=float(spec['deadline_unix'])
        original=subprocess.check_output(['git','-C',str(runtime),'rev-parse','HEAD'],text=True).strip()
        if original!=row['source']['commit']:raise RuntimeError('Original inflight runtime source mismatch')
        controller=Path(row['controller_output']);unit=row['simulator_unit'];port=row['port']
        if (controller/'controller.json').exists():raise RuntimeError('Refusing to launch a duplicate model controller')
        while True:
            self.refresh_startup_progress(row)
            cutoff=row['startup_deadline_unix'] if row.get('startup_watchdog_policy')==STARTUP_POLICY else deadline
            if time.time()>=cutoff:
                raise TimeoutError('Original simulator startup exceeded its progress watchdog' if
                    row.get('startup_watchdog_policy')==STARTUP_POLICY else
                    'Original simulator startup exceeded its unchanged deadline')
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
        self.update(row,stage='controller_starting' if row.get('execution_clock_path') else 'policy_running',
                    bridge_ready_at=now(),simulator_pid=spec['simulator_pid'])
        remote=shlex.join(['env','PYTHONPATH='+str(runtime/'src'),self.c['sim_python'],'-m','manipulation_agent.mcp_server','--bridge',f'http://127.0.0.1:{port}'])
        cmd=[sys.executable,str(runtime/'scripts/run_codex_controller.py'),'--model',self.manifest['model'],
             '--instruction',row['instruction'],'--mcp-command',self.c['ssh'][0],
             '--mcp-args-json',json.dumps(self.c['ssh'][1:]+[remote]),'--output',str(controller),
             '--timeout',str(self.manifest['model_timeout_seconds']),'--agent-profile',self.manifest['agent_profile']]
        if self.manifest.get('model_reasoning_effort'):
            cmd += ['--reasoning-effort',self.manifest['model_reasoning_effort']]
        if row.get('execution_clock_path'):
            cmd+=['--execution-clock-command-json',json.dumps(self.execution_clock_command(
                row['execution_clock_path'],row.get('episode_timeout_seconds',self.manifest['model_timeout_seconds']),runtime))]
        elif row.get('episode_deadline_unix'):cmd+=['--deadline-unix',str(row['episode_deadline_unix'])]
        with (self.root/'logs'/f"{row['run_id']}_controller.log").open('x') as log:
            process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,
                env={**self.environment,'PYTHONPATH':str(runtime/'src')})
        self.update(row,controller_wrapper_pid=process.pid,controller_host=os.uname().nodename,controller_interpreter=sys.executable)
        self.journal('READY adopted initialization '+row['run_id']+'; first model policy starts against the unchanged original simulator and source.')
        return process

    def run(self,limit=None):
        self.publish()
        self.queue=queue.Queue()
        planned=[r for r in self.rows if r['status']=='planned']
        selected=planned if limit is None else planned[:limit]
        required_rows=list(self.rows) if limit is None else [r for r in self.rows if r['status']!='planned' or r in selected]
        for row in selected:self.queue.put(row)
        archived=[];archive_lock=threading.Lock()
        archive_slots=threading.BoundedSemaphore(self._config.get('max_pending_archives',4))
        capacity=threading.BoundedSemaphore(self._config.get('max_active_workers',len(self.workers)))
        poll=self._config.get('resource_poll_seconds',30)
        def submit_archive(row,paths,config):
            if paths is None:return
            archive_slots.acquire()
            future=self._archive_pool.submit(self.finalize_episode,row,*paths,config)
            future.add_done_callback(lambda _:archive_slots.release())
            with archive_lock:archived.append(future)
        def worker(config):
            self._worker_local.config=config;self._worker_local.defer_archive=True
            time.sleep(config.get('admission_delay_seconds',0))
            inflight=[r for r in self.rows if (r['status']=='running' or (r.get('simulator_cleanup') or {}).get('status')=='blocked') and
                self._config.get('adopt_inflight',{}).get(r['run_id'])==config['id'] and
                not (r.get('execution_finished_at') and (r.get('simulator_cleanup') or {}).get('status')=='passed')]
            while inflight or not self.queue.empty():
                adopting=inflight[0] if inflight else None
                if not adopting and (self.root/'drain_requested.json').exists():
                    self.worker_state('drained');return
                if not capacity.acquire(blocking=False):
                    self.worker_state('waiting_for_capacity');time.sleep(poll);continue
                lease=None;row=None;paths=None;cleared=True
                try:
                    lease=self.acquire_worker_lease(adopting=adopting)
                    if lease is None:continue
                    self._worker_local.lease=lease
                    if adopting:
                        row=inflight.pop(0);cleared=False;paths=self.adopt_one(row)
                    else:
                        if not self.resource_admission():continue
                        if (self.root/'drain_requested.json').exists():continue
                        try:row=self.queue.get_nowait()
                        except queue.Empty:return
                        cleared=False;paths=self.run_one(row,config['gpu'])
                    if (row.get('simulator_cleanup') or {}).get('status')=='blocked':
                        try:self.capture_result(row,Path(row['simulator_output']))
                        except Exception:pass
                        if not isinstance(row.get('task_success'),bool):self.update(row,outcome_provisional=True)
                        self.complete_row(row)
                        self.update(row,stage='cleanup_pending')
                        self.recover_worker_cleanup(row)
                    cleared=(not (row.get('simulator_started_at_unix') or row.get('episode_started_at_unix')) or
                             (row.get('simulator_cleanup') or {}).get('status')=='passed')
                except Exception as exc:
                    self.worker_state('worker_error',reason=f'{type(exc).__name__}: {exc}',run_id=row.get('run_id') if row else None)
                    self.journal('WORKER ERROR '+config['id']+': '+str(exc))
                    if row is not None:
                        self.update(row,failure=f'{type(exc).__name__}: {exc}',failure_stage='worker_supervision')
                        if row.get('simulator_started_at_unix') or row.get('episode_started_at_unix') or adopting:
                            self.cleanup_owned_unit(row);self.recover_worker_cleanup(row)
                            paths=(Path(row['controller_output']),Path(row['simulator_output']))
                        else:self.complete_row(row)
                    elif 'Permanent admission error' in str(exc):
                        self.worker_state('disabled',reason=str(exc));return
                finally:
                    try:
                        if lease:lease.release(cleared=cleared)
                    except Exception as exc:
                        if row is not None:self.update(row,lease_metadata_cleanup_error=f'{type(exc).__name__}: {exc}')
                        self.journal('LEASE METADATA CLEANUP '+config['id']+': '+str(exc))
                    finally:
                        self._worker_local.lease=None;capacity.release()
                    if row is None:time.sleep(poll)
                # GPU and host capacity are free before any bulk copy or decode.
                if row is not None:
                    if cleared:self.worker_state('archive_pending')
                    submit_archive(row,paths,config)
                    if cleared:self.worker_state('available')
                    time.sleep(self._config.get('worker_yield_seconds',1))
            self.worker_state('idle')
        with ThreadPoolExecutor(max_workers=self._config.get('archive_workers',2)) as archive_pool:
            self._archive_pool=archive_pool
            for row in self.rows:
                if row['status']=='running' and row.get('execution_finished_at') and (row.get('simulator_cleanup') or {}).get('status')=='passed':
                    config=next(w for w in self.workers if w['id']==row['worker_id'])
                    submit_archive(row,(Path(row['controller_output']),Path(row['simulator_output'])),config)
            with ThreadPoolExecutor(max_workers=len(self.workers)) as pool:
                futures=[pool.submit(worker,config) for config in self.workers]
                for future in futures:
                    try:future.result()
                    except Exception as exc:self.journal('WORKER EXIT: '+str(exc))
            if not self.queue.empty() and not (self.root/'drain_requested.json').exists() and all(
                    self.worker_states.get(w['id'],{}).get('stage')=='disabled' for w in self.workers):
                raise RuntimeError('No usable worker: static admission failed; unstarted tasks remain planned')
            for future in archived:
                try:future.result()
                except Exception as exc:self.journal('ARCHIVE EXIT: '+str(exc))
        self._archive_pool=None
        self.publish()
        require_completed_scope(required_rows,draining=(self.root/'drain_requested.json').exists())


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
