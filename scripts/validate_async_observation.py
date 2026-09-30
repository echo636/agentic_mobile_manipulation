"""Audit four-camera captures, no-motion acquisition and async lifecycle evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def read_lines(p): return [json.loads(x) for x in p.read_text().splitlines()]


def forward(q):
    x,y,z,w=q
    return [-2*(x*z+y*w), -2*(y*z-x*w), -(1-2*(x*x+y*y))]


def validate(root, probe=None):
    run=json.loads((root/'run.json').read_text())
    captures=read_lines(root/'captures.jsonl')
    audits=read_lines(root/'observation_capture_audit.jsonl')
    events=read_lines(root/'events.jsonl')
    jobs=[json.loads(p.read_text()) for p in sorted((root/'observation_jobs').glob('*.json'))]
    checks={'real_omnigibson':run['config']['backend']=='omnigibson',
        'exactly_four_fixed_cameras':bool(audits) and all(len(a['rig_sensor_names'])==4 and not a['stock_sensor_names'] for a in audits),
        'no_robot_motion_during_capture':bool(audits) and all(a['no_robot_motion'] and a['before_position']==a['after_position'] and a['before_orientation']==a['after_orientation'] for a in audits),
        'no_physics_step_during_capture':bool(audits) and all(a['start_step']==a['end_step']==a['capture']['sim_step'] for a in audits),
        'capture_audit_complete':len(audits)==len(captures)>0,
        'no_wrist_images':not list((root/'frames').glob('*wrist*')),
        'common_capture_identity':all('synchronized_capture' in c and c['synchronized_capture']['sim_step']==c['env_steps'] for c in captures),
        'rgb_hashes_match':True,'four_distinct_direction_images':True,'cardinal_optical_axes':True,'ninety_degree_hfov':True}
    for c in captures:
        names={row['image_ref'].split('-')[-1] for row in c['images']}
        checks['exactly_four_fixed_cameras'] &= names=={'front','back','left','right'} and len(c['images'])==4
        axes={}; hashes=[]
        for row in c['images']:
            ref=row['image_ref'];meta=json.loads((root/'executor_frames'/f'{ref}.json').read_text())
            payload=(root/row['file']).read_bytes();sha=hashlib.sha256(payload).hexdigest();hashes.append(sha)
            checks['rgb_hashes_match'] &= sha==row['sha256']==meta['rgb_sha256']
            axis=forward(meta['orientation']);n=math.hypot(*axis[:2]);axes[ref.split('-')[-1]]=[axis[0]/n,axis[1]/n]
            k=meta['intrinsic'];fov=math.degrees(2*math.atan(256/k[0][0]))
            checks['ninety_degree_hfov'] &= abs(fov-90)<.1
        checks['four_distinct_direction_images'] &= len(set(hashes))==4
        dot=lambda a,b:sum(x*y for x,y in zip(axes[a],axes[b]))
        checks['cardinal_optical_axes'] &= abs(dot('front','back')+1)<1e-4 and abs(dot('left','right')+1)<1e-4 and abs(dot('front','left'))<1e-4
    terminal={'passed','failed','cancelled'}
    checks['all_jobs_terminal']=bool(jobs) and all(j['status'] in terminal for j in jobs)
    checks['completed_async_capture']=any(j['status']=='passed' for j in jobs)
    ordering=[]
    for j in jobs:
        ack=next((i for i,e in enumerate(events) if e['kind']=='tool_result' and e['name']=='start_observation' and e['result'].get('job',{}).get('job_id')==j['job_id']),None)
        started=next((i for i,e in enumerate(events) if e['kind']=='observation_started' and e['job']['job_id']==j['job_id']),None)
        if started is not None:ordering.append(ack is not None and ack<started)
    checks['submission_returned_before_capture_started']=bool(ordering) and all(ordering)
    if probe:
        result=json.loads((probe/'validation.json').read_text());checks['real_mcp_probe_passed']=result['status']=='passed'
        checks['read_only_probe_no_actions']=run.get('actions')==0 and run.get('sim_steps')==0
    result={'status':'passed' if all(checks.values()) else 'failed','run_id':run['run_id'],
            'validation_level':'fixed_four_camera_async_interface','task_success_separate':run.get('task_success'),
            'checks':checks,'captures':len(captures),'jobs':len(jobs),'source':run['source']}
    (root/'observation_validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));return 0 if result['status']=='passed' else 2


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--probe-dir',type=Path)
    a=p.parse_args();raise SystemExit(validate(a.run_dir,a.probe_dir))
