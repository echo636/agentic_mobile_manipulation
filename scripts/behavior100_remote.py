"""Host-side batch checks and launch, without importing the simulator for checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone


def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return {'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


def preflight(gpu, port, data_root, min_free_gpu_mib=0):
    queries = {
        'gpus': command(['nvidia-smi', '--query-gpu=index,uuid,memory.used,memory.total,driver_version', '--format=csv,noheader,nounits']),
        'gpu_processes': command(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory', '--format=csv,noheader']),
        'disk': command(['df', '-B1', str(data_root), os.getcwd()]),
        'quota': command(['quota', '-s']),
    }
    selected = next((s.split(',') for s in queries['gpus']['stdout'].splitlines() if s.split(',')[0].strip() == str(gpu)), None)
    if selected is None:
        raise RuntimeError('Selected physical GPU is unavailable')
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    cgroup = Path('/sys/fs/cgroup/user.slice') / f'user-{os.getuid()}.slice'
    cg = {k: (cgroup/k).read_text().strip() for k in ('memory.current', 'memory.max')}
    cg['memory_stat']={k:int(v) for k,v in (line.split() for line in (cgroup/'memory.stat').read_text().splitlines())}
    stats=cg['memory_stat']
    # Clean file cache and reclaimable slab are not live process working memory.
    # This is an estimate; the simulator still has its own hard cgroup limit.
    cg['reclaimable_estimate']=max(0,stats['file']-stats.get('shmem',0)-stats.get('file_dirty',0)-stats.get('file_writeback',0))+stats.get('slab_reclaimable',0)
    cg_free = None if cg['memory.max'] == 'max' else int(cg['memory.max'])-int(cg['memory.current'])+cg['reclaimable_estimate']
    with socket.socket() as sock:
        port_free = sock.connect_ex(('127.0.0.1', port)) != 0
    if min_free_gpu_mib and min_free_gpu_mib<24576:
        raise ValueError('Shared GPU mode requires at least 24 GiB free before each episode')
    checks = {'gpu_capacity': (int(selected[3])-int(selected[2])>=min_free_gpu_mib) if min_free_gpu_mib else int(selected[2])<1024,
              'validated_driver_floor':tuple(map(int,selected[4].strip().split('.'))) >= (580,65,6),
              'host_memory_available_40GiB': int(mem['MemAvailable'].split()[0])*1024 > 40*1024**3,
              'cgroup_reclaimable_headroom_28GiB': cg_free is None or cg_free > 28*1024**3,
              'data_disk_free_40GiB': shutil.disk_usage(data_root).free > 40*1024**3,
              'bridge_port_free': port_free}
    return {'at': datetime.now(timezone.utc).isoformat(), 'host': socket.gethostname(),
            'interpreter': sys.executable, 'pid': os.getpid(), 'gpu_index': gpu,
            'gpu_uuid': selected[1].strip(), 'cgroup': cg, 'checks': checks,
            'driver_version':selected[4].strip(),'gpu_policy':{'shared':bool(min_free_gpu_mib),'minimum_free_mib':min_free_gpu_mib},
            'status': 'passed' if all(checks.values()) else 'blocked', 'queries': queries}


def assets(manifest, data_root):
    import yaml
    root = data_root/'data/omnigibson/2026-challenge-task-instances'
    config = root/'metadata/available_tasks.yaml'
    catalog = yaml.safe_load(config.read_text())
    rows = []
    for task in manifest['tasks']:
        name, instance = task['task'], task['instance']
        scene = catalog[name][0]['scene_model']
        folder = root/'scene_test/public'/scene/'json'
        paths = {'scene_template': folder/f'{scene}_task_{name}_0_0_template-partial_rooms.json',
                 'instance_state': folder/f'{scene}_task_{name}_instances'/f'{scene}_task_{name}_0_{instance}_template-tro_state.json',
                 'bddl': data_root/'src/BEHAVIOR-1K/bddl3/bddl/activity_definitions'/name/'problem0.bddl'}
        row = {'task': name, 'scene': scene, 'scene_matches_catalog': scene == task['scene'], 'files': {}}
        for key, path in paths.items():
            row['files'][key] = {'path': str(path), 'exists': path.is_file(),
                                'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
        row['bddl_matches_manifest'] = row['files']['bddl']['sha256'] == task['bddl_sha256']
        row['status'] = 'passed' if all(v['exists'] for v in row['files'].values()) and row['scene_matches_catalog'] and row['bddl_matches_manifest'] else 'failed'
        rows.append(row)
    return {'status': 'passed' if all(r['status']=='passed' for r in rows) else 'failed',
            'available_tasks_sha256': hashlib.sha256(config.read_bytes()).hexdigest(), 'tasks': rows}


def main():
    p=argparse.ArgumentParser(); p.add_argument('mode', choices=['preflight', 'assets', 'simulate'])
    p.add_argument('--manifest',type=Path,required=True); p.add_argument('--index',type=int)
    p.add_argument('--gpu',type=int); p.add_argument('--port',type=int); p.add_argument('--unit'); p.add_argument('--run-id')
    p.add_argument('--data-root',type=Path,required=True)
    p.add_argument('--min-free-gpu-mib',type=int,default=0)
    a=p.parse_args(); manifest=json.loads(a.manifest.read_text())
    if a.mode == 'assets':
        print(json.dumps(assets(manifest,a.data_root))); return
    if a.mode == 'preflight':
        print(json.dumps(preflight(a.gpu,a.port,a.data_root,a.min_free_gpu_mib))); return
    row=manifest['tasks'][a.index]
    if a.run_id:
        import re
        if not re.fullmatch(re.escape(row['run_id'].rsplit('_r',1)[0])+r'_r[1-9][0-9]*',a.run_id):
            raise ValueError('Attempt ID must preserve the frozen task identity')
        row['run_id']=a.run_id
    os.environ['MAS_UNIT']=a.unit
    result=preflight(a.gpu,a.port,a.data_root,a.min_free_gpu_mib)
    # Persist a second check inside the allocated unit, immediately before startup.
    check_path=a.manifest.parent/'preflight'/f"{row['run_id']}_in_unit.json"
    check_path.write_text(json.dumps(result,indent=2)+'\n')
    if result['status'] != 'passed': raise RuntimeError('Resource preflight blocked simulator startup')
    os.environ['MAS_GPU_UUID']=result['gpu_uuid']
    args=[sys.executable,'-m','manipulation_agent.vision_cli','--backend','omnigibson','--policy','serve',
          '--task',row['task'],'--instance',str(row['instance']),'--seed',str(row['seed']),
          '--instruction',row['instruction'],'--agent-profile',manifest['agent_profile'],'--record-video',
          '--port',str(a.port),'--controller','codex:'+manifest['model'],
          '--max-actions',str(manifest['max_actions']),'--max-sim-steps',str(manifest['max_sim_steps']),
          '--output',str(a.data_root/'runs'/row['run_id'])]
    os.execv(sys.executable,args)


if __name__=='__main__': main()
