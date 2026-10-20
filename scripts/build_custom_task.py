#!/usr/bin/env python3
"""Construct and freeze one real custom scene. This is not task acceptance."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE/'src'))
from manipulation_agent.custom_tasks import load_spec, sha256
from manipulation_agent.records import write_json, release_source_digest
from manipulation_agent.reference_evidence import close_reference, complete_reference_exit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--order',default='build',help=argparse.SUPPRESS)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if not a.worker:
        if a.output.exists():p.error('Output exists; preserve evidence and choose a new attempt')
        child=subprocess.run([sys.executable,'-B','-u',str(Path(__file__).resolve()),*sys.argv[1:],'--worker'])
        return 0 if complete_reference_exit(a.output,child.returncode) else 1
    spec,contract=load_spec(a.spec)
    if spec.get('frozen_instance'):p.error('Build requires a construction recipe, not an existing frozen instance')
    a.output.mkdir(parents=True,exist_ok=False)
    freeze=SOURCE/'source_freeze.json'
    commit=(json.loads(freeze.read_text())['source_commit'] if freeze.exists() else
            subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip())
    record=dict(status='running',started_at_unix=time.time(),task=spec['id'],host=platform.node(),
                interpreter=sys.executable,pid=os.getpid(),unit=os.environ.get('MAS_UNIT'),gpu_uuid=os.environ.get('MAS_GPU_UUID'),
                source_commit=commit,source_sha256=release_source_digest(SOURCE),spec_sha256=sha256(a.spec),
                output_path=str(a.output.resolve()),model_used=False,benchmark_ready=False,
                validation_level='real_simulator_scene_construction_and_policy_ready_initial_assertions',
                physical_reference_status='planned',model_status='planned',contract_validation=contract,evaluation=None)
    write_json(a.output/'run_record.json',record)
    backend=None
    try:
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        backend=CustomTaskBackend.__new__(CustomTaskBackend)
        backend.__init__(a.spec,a.output,build_instance=a.output/'initial_scene.json',record_video=False,
                         inside_placement='official_volume',max_steps=1000000)
        record['backend']=backend.provenance()
        record['evaluation']=backend.evaluate()
        if record['evaluation']['task_success']:
            raise RuntimeError('New task is already solved at initialization')
        write_json(a.output/'initial_observation.json',backend.observe())
        spec['frozen_instance']={'path':'initial_scene.json','sha256':sha256(a.output/'initial_scene.json')}
        write_json(a.output/'task.json',spec)
        load_spec(a.output/'task.json',require_frozen=True)
        record.update(status='passed',frozen_spec=str(a.output/'task.json'),
                      frozen_spec_sha256=sha256(a.output/'task.json'),scene_sha256=spec['frozen_instance']['sha256'])
    except BaseException as exc:
        record.update(status='failed',error=str(exc),error_code=getattr(exc,'code',None))
        (a.output/'traceback.txt').write_text(traceback.format_exc())
    finally:
        if backend is not None and hasattr(backend,'env'):
            try:
                live={o.name:o for o in backend.env.scene.objects}
                write_json(a.output/'geometry.json',{role:({'aabb':[v.tolist() for v in live[name].aabb],
                    'pose':[v.tolist() for v in live[name].get_position_orientation()],
                    'states':[s.__name__ for s in live[name].states],'fixed':live[name].fixed_base} if name in live else None)
                    for role,name in spec['roles'].items()})
            except Exception:
                (a.output/'geometry_error.txt').write_text(traceback.format_exc())
        close_reference(backend,record,a.output)
    return 0 if record['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
