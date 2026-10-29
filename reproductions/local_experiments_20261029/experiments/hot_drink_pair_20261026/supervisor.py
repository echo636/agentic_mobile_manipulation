"""One frozen matched pair. Outcomes never trigger replacement episodes."""
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT/'plan.json').read_text())
SOURCE = Path(PLAN['source'])
PYTHON = sys.executable


def write(path, value):
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)


def status(state, **fields):
    write(ROOT/'status.json', dict(status=state, at_unix=time.time(), pid=os.getpid(), **fields))


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def lines(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []


def verify():
    manifest = read(ROOT/'source_manifest.json')
    for name, digest in manifest.items():
        assert hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() == digest, name
    assert hashlib.sha256(Path(PLAN['bddl']).read_bytes()).hexdigest() == PLAN['bddl_sha256']
    for name, digest in read(ROOT/'upstream_manifest.json')['files'].items():
        assert hashlib.sha256((ROOT/'upstream'/name).read_bytes()).hexdigest() == digest, name
    for name, digest in read(ROOT/'asset_input_hashes.json').items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name


def available_gpu():
    while True:
        rows = subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.free',
                '--format=csv,noheader,nounits'], text=True)
        evidence = []
        for row in rows.splitlines():
            index, uuid, free = [v.strip() for v in row.split(',')]
            evidence.append(dict(gpu=int(index), uuid=uuid, free_mib=int(free)))
        for item in [x for x in evidence if x['gpu']==PLAN['gpu']]:
            assert item['uuid']==PLAN['gpu_uuid']
            if item['free_mib'] < PLAN['minimum_free_gpu_mib']:
                continue
            lock = (ROOT.parent/f"sunyujie_simulator_gpu{item['gpu']}.lock").open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return item, lock
            except BlockingIOError:
                lock.close()
        status('waiting_for_gpu', gpu_memory=evidence, minimum_free_mib=PLAN['minimum_free_gpu_mib'])
        time.sleep(30)


def script(name, args, env, log):
    with (ROOT/log).open('a') as stream:
        return subprocess.run([PYTHON,str(SOURCE/'scripts'/name),*args],
                              cwd=SOURCE,env=env,stdout=stream,stderr=subprocess.STDOUT).returncode


def audit(arm, trial, env):
    result = {'arm':arm, 'trial':str(trial)}
    summary = read(trial/'summary.json')
    run = read(trial/'run/run.json')
    catalog = read(trial/'controller/mcp_preflight.json')
    events = lines(trial/'run/events.jsonl')
    calls = [e for e in events if e.get('kind') == 'tool_call'
             and e.get('request_id') != 'demo-supervisor-finish']
    trace = lines(trial/'transport.jsonl')
    requests = [e for e in trace if e.get('kind') == 'request']
    source_manifest = read(ROOT/'source_manifest.json')
    skill = read(trial/'run/skill_manifest.json')
    names = {s['name'] for s in skill.get('skills', [])}
    decision_fields = set(PLAN['decision_fields'][arm])
    checks = {
        'actual_model_requested':summary.get('model') == PLAN['model'],
        'actual_effort_requested':summary.get('reasoning_effort') == PLAN['reasoning_effort'],
        'catalog_matches_group':set(catalog.get('tool_names', [])) == set(PLAN['group_tools'][arm]),
        'decision_required_all_tools':bool(catalog.get('tool_schemas')) and all(
            'decision' in s.get('required', []) for s in catalog.get('tool_schemas', {}).values()),
        'decision_recorded_all_model_calls':bool(calls) and all(
            isinstance((e.get('arguments') or {}).get('decision'),dict) and
            set(e['arguments']['decision']) == decision_fields for e in calls),
        'skill_access_matches_group':('long-horizon-planning' in names) == (arm == 'with_long_horizon'),
        'other_four_skills_present':names - {'long-horizon-planning'} ==
            {'visual-exploration','visual-manipulation','pick-and-place','failure-recovery'},
        'scene_shell_runtime_passed':read(trial/'run/scene_shell_validation.json').get('passed') is True,
        'scene_version':read(trial/'run/scene_geometry_repair.json').get('policy') == PLAN['scene_version'],
        'same_physical_gpu':(run.get('backend') or {}).get('gpu_uuid') == read(ROOT/'selected_gpu.json')['uuid'],
        'strict_wire_trace_present':bool(requests) and all(e.get('transport_mode') == 'strict_mcp_tools_v1' for e in requests),
        'blocked_policy_matches':run.get('termination_contract',{}).get('blocked_policy') == 'accept',
        'environment_route_version':(run.get('backend') or {}).get('high_level_environment_open_close_protocol') == 'non_task_open_close_v1',
        'source_still_frozen':all(hashlib.sha256((SOURCE/p).read_bytes()).hexdigest() == h for p,h in source_manifest.items()),
    }
    write(trial/'configuration_audit.json', {'checks':checks,'passed':all(checks.values()),
          'wire_protocol':PLAN['wire_protocol'],'model_identity_limit':'Requested model and reported transport evidence; provider internals are not independently attested.'})
    result['configuration_checks'] = checks
    result['audit_returncode'] = script('audit_demo_trial.py',[str(trial)],env,arm+'_audit.log')
    import imageio_ffmpeg
    with (ROOT/(arm+'_decode.log')).open('w') as stream:
        decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-xerror','-i',
            str(trial/'run/episode.mp4'),'-f','null','-'],stdout=stream,stderr=subprocess.STDOUT).returncode
    write(trial/'video_decode.json',{'passed':decoded == 0,'returncode':decoded})
    result.update(video_decode_passed=decoded == 0, evaluation=run.get('evaluation'),
                  formal_finish_observed=summary.get('formal_finish_observed'),
                  configuration_verified=all(checks.values()))
    return result


def publish(completed, env):
    cohort = ROOT/'hot_drink_lh_repaired_pair_20261026'
    cohort.mkdir(exist_ok=True)
    for cell in completed:
        link = cohort/cell['arm']
        if not link.exists(): link.symlink_to(cell['trial'],target_is_directory=True)
    site = ROOT.parent.parent/'review_sites/atomic_actions_20261008'
    result = script('publish_demo_trials.py',['--trials',str(cohort),'--site-root',str(site),
        '--index-name','hot_drink_lh_repaired_pair_20261026_index.html',
        '--cohort-title','热饮执行器修复后：有无长程 skill＋计划记忆工具，astra low'],env,'publication.log')
    write(ROOT/'publication.json',{'returncode':result,'site':str(site),
           'index':'hot_drink_lh_repaired_pair_20261026_index.html'})


def main():
    write(ROOT/'supervisor.pid.json',{'pid':os.getpid()})
    verify()
    gpu, lock = available_gpu()
    write(ROOT/'selected_gpu.json', gpu)
    clean = {k:v for k,v in os.environ.items() if not k.startswith(('MAS_','OG_','OMNIGIBSON_'))
             and k not in {'CUDA_VISIBLE_DEVICES','GAP_BEHAVIOR_GPU_ID'}}
    clean.update(PLAN['shared_environment'])
    clean['PYTHONPATH'] = str(SOURCE/'src')
    completed = []
    for arm in PLAN['order']:
        verify()
        while True:
            free = int(subprocess.check_output(['nvidia-smi',f"--id={gpu['gpu']}",
                '--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).strip())
            if free >= PLAN['minimum_free_gpu_mib']: break
            status('waiting_for_same_gpu_memory',arm=arm,free_mib=free,completed=completed)
            time.sleep(30)
        trial = ROOT/arm
        assert not trial.exists(), 'Never overwrite an attempt'
        proxy = None
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        trace = ROOT/(arm+'_transport.jsonl')
        try:
            with (ROOT/(arm+'_proxy.log')).open('x') as log:
                proxy = subprocess.Popen([PYTHON,str(SOURCE/'scripts/trace_responses_transport.py'),
                    '--port',str(port),'--output',str(trace),'--strict-mcp-tools'],stdout=log,stderr=subprocess.STDOUT)
            for _ in range(60):
                if proxy.poll() is not None: raise RuntimeError('Proxy startup failed')
                try:
                    with urlopen(f'http://127.0.0.1:{port}/healthz',timeout=2) as response:
                        if json.load(response).get('mode') == 'strict_mcp_tools_v1': break
                except OSError: pass
                time.sleep(.5)
            else: raise RuntimeError('Proxy readiness timeout')
            env = dict(clean,MAS_RESPONSES_PROXY_URL=f'http://127.0.0.1:{port}/v1')
            command = [PYTHON,str(SOURCE/'scripts/run_no_demo_trial.py'),'--task','prepare_and_serve_hot_drink',
                '--instruction',PLAN['instruction'],'--bddl-source',PLAN['bddl'],'--output',str(trial),
                '--omnigibson-source',PLAN['omnigibson_source'],'--provider-profile',PLAN['provider_profile'],
                '--codex-home',PLAN['codex_home'],'--expected-provider-host',PLAN['provider_host'],
                '--gpu',str(gpu['gpu']),'--appdata',str(ROOT/f"appdata_gpu{gpu['gpu']}"),
                '--instance','301','--seed','0','--timeout','14400','--max-actions','120',
                '--max-sim-steps','20000','--unfinished-restarts','3','--unfinished-recovery','same_thread',
                '--long-horizon-planning-skill','on' if arm == 'with_long_horizon' else 'off',
                '--model',PLAN['model'],'--reasoning-effort',PLAN['reasoning_effort'],
                '--high-level-harness','--unbounded-episode']
            write(ROOT/(arm+'_launch.json'),{'command':command,'common_environment':PLAN['shared_environment'],
                'transport_proxy_url':env['MAS_RESPONSES_PROXY_URL'],'gpu':gpu})
            with (ROOT/(arm+'_launcher.log')).open('x') as log:
                process = subprocess.Popen(command,cwd=SOURCE,env=env,stdout=log,stderr=subprocess.STDOUT)
                while process.poll() is None:
                    status('episode_running',arm=arm,launcher_pid=process.pid,gpu=gpu,completed=completed)
                    time.sleep(20)
            summary = read(trial/'summary.json')
            if not summary or summary.get('supervisor_failure_type'):
                raise RuntimeError('Episode infrastructure failure: preserve, diagnose, do not resample')
        finally:
            if proxy is not None and proxy.poll() is None:
                proxy.terminate()
                try: proxy.wait(timeout=10)
                except subprocess.TimeoutExpired: proxy.kill();proxy.wait()
        (trial/'transport.jsonl').symlink_to(trace)
        status('auditing',arm=arm,completed=completed)
        cell = audit(arm,trial,clean)
        completed.append(cell)
        write(ROOT/'completed_cells.json',completed)
        analysis = script('analyze_workflow_efficiency.py',[*[c['trial'] for c in completed],
            '--output',str(ROOT/('comparison_'+arm+'.json'))],clean,'cost_analysis.log')
        write(ROOT/'cost_analysis_status.json',{'returncode':analysis})
        publish(completed,clean)
    verify()
    status('pair_complete_review_required',completed=completed)


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        status('failed_preserve_and_diagnose',error_type=type(exc).__name__,error=str(exc))
        traceback.print_exc()
        raise
