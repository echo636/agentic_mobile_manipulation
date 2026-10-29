"""Run an isolated, reproducible private task-mechanics probe."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from build_scene_overlay import build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--behavior-root', type=Path, required=True)
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--appdata', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--bag-height-scale', type=float, default=1.2)
    parser.add_argument('--cargo-offset-xy', type=float, nargs=2, default=(0.0, 0.0))
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--probe-script', type=Path,
                        default=Path(__file__).with_name('probe_benchmark_readiness.py'))
    parser.add_argument('--before-backend', type=Path)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    root, behavior = args.output.resolve(), args.behavior_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(source / 'scripts'))
    from run_demo_trial import prepare_bddl_overlay

    frozen = root / 'source_snapshot'
    frozen.mkdir()
    sources = [Path(__file__), args.probe_script.resolve(),
               Path(__file__).with_name('build_scene_overlay.py'),
               Path(__file__).with_name('problem0.bddl'),
               source / 'src/manipulation_agent/omnigibson_backend.py',
               source / 'src/manipulation_agent/executors/carry.py',
               source / 'src/manipulation_agent/executors/placement.py',
               source / 'src/manipulation_agent/executors/omnigibson_rgb.py',
               source / 'src/manipulation_agent/executors/gt_navigation.py',
               source / 'src/manipulation_agent/executors/visual_mesh_grounding.py',
               source / 'src/manipulation_agent/observations/rig.py']
    source_hashes = {}
    for path in sources:
        data = path.read_bytes()
        (frozen / path.name).write_bytes(data)
        source_hashes[str(path.relative_to(source))] = hashlib.sha256(data).hexdigest()
    problem = Path(__file__).with_name('problem0.bddl').read_text()
    bddl, _ = prepare_bddl_overlay(behavior, 'carrying_in_groceries', problem, root)
    dataset = root / 'dataset_overlay'
    build(behavior / 'datasets', dataset, bag_height_scale=args.bag_height_scale,
          cargo_offset_xy=tuple(args.cargo_offset_xy))
    scratch = root / 'scratch'; scratch.mkdir()
    env = os.environ.copy()
    env.update(CUDA_VISIBLE_DEVICES=str(args.gpu), GAP_BEHAVIOR_GPU_ID=str(args.gpu),
               OMNIGIBSON_DATA_PATH=str(dataset), OMNIGIBSON_APPDATA_PATH=str(args.appdata.resolve()),
               TMPDIR=str(scratch), MAS_CPU_DYNAMICS='1', MAS_CUSTOM_NATIVE_BDDL='1',
               MAS_DEMO_MOTION='0', MAS_HIGH_LEVEL_HARNESS='0', MAS_SEMANTIC_MAGIC='0',
               OG_SKIP_PHYSICS_STEPS='0', OG_KINEMATIC_NAV_TELEPORT='0', MAS_NAV_RELAXED='0',
               MAS_VIDEO_RENDER_STRIDE='3')
    env.pop('OMNIGIBSON_GPU_ID', None)
    env.pop('OG_DISABLE_CONTACT_CACHE', None)
    env.pop('OG_SWITCH_TO_CPU_AFTER_WATER', None)
    env['PYTHONPATH'] = os.pathsep.join(str(p) for p in (
        bddl, source / 'src', behavior / 'OmniGibson', behavior / 'bddl3', behavior / 'joylo'))
    command = [sys.executable, str(frozen / args.probe_script.name),
               '--output', str(root / 'run'), '--seed', str(args.seed)]
    if args.before_backend:
        data = args.before_backend.read_bytes()
        before = frozen / 'before_omnigibson_rgb.py'
        before.write_bytes(data)
        source_hashes[str(args.before_backend.resolve())] = hashlib.sha256(data).hexdigest()
        command += ['--before-backend', str(before)]
    geometry_hashes = {str(p.relative_to(dataset)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in dataset.glob('2026-challenge-task-instances/scene_test/public/house_double_floor_lower/json/**/house_double_floor_lower_task_carrying_in_groceries*json')
                       if not p.is_symlink()}
    try:
        gpu_uuid = subprocess.check_output(['nvidia-smi', '-i', str(args.gpu),
            '--query-gpu=uuid', '--format=csv,noheader'], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        gpu_uuid = None
    manifest = {'started_at': datetime.now(timezone.utc).isoformat(),
                'launcher_pid': os.getpid(), 'gpu_uuid': gpu_uuid,
                'scene_json_sha256': geometry_hashes,
                'physics_flags': {key: env[key] for key in ('MAS_CPU_DYNAMICS',
                    'MAS_DEMO_MOTION', 'MAS_HIGH_LEVEL_HARNESS', 'MAS_SEMANTIC_MAGIC',
                    'OG_SKIP_PHYSICS_STEPS', 'OG_KINEMATIC_NAV_TELEPORT', 'MAS_NAV_RELAXED')},
                'kind': 'scripted_private_geometry_probe', 'model_used': False,
                'task': 'carrying_in_groceries', 'scene': 'house_double_floor_lower', 'instance': 301,
                'seed': args.seed, 'gpu': args.gpu, 'host': socket.gethostname(),
                'interpreter': sys.executable, 'command': command,
                'source_sha256': source_hashes,
                'bddl_sha256': hashlib.sha256(problem.encode()).hexdigest(),
                'bag_height_scale': args.bag_height_scale,
                'cargo_offset_xy_m': args.cargo_offset_xy, 'timeout_seconds': args.timeout,
                'dataset_overlay': str(dataset), 'protocol': 'private_helpers_not_autonomous_score'}
    (root / 'probe_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (root / 'simulator.log').open('w') as log:
        try:
            process = subprocess.run(command, env=env, cwd=source, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=args.timeout)
            manifest['simulator_exit_code'] = process.returncode
        except subprocess.TimeoutExpired:
            manifest['timed_out'] = True
    result_path = root / 'run/result.json'
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    # Simulator shutdown may exit with zero even after a failed diagnostic.
    passed = (result.get('status') == 'passed' and not manifest.get('timed_out')
              and manifest.get('simulator_exit_code') == 0)
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    manifest['status'] = 'passed' if passed else 'failed'
    (root / 'probe_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'status': manifest['status'], 'result': str(result_path)}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
