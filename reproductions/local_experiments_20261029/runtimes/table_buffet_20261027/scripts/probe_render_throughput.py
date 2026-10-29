"""Real-simulator render latency and paired timing diagnostic, not a policy run.

Large camera-pose changes with frozen physics detect stale render products.
The production observation barrier remains four renders regardless of results.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder, write_json

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
p.add_argument('--task',default='turning_on_radio');a=p.parse_args()
r=Recorder(a.output,{'task':a.task,'instance':301,'seed':0,'policy':'render_latency_diagnostic',
                     'model_used':False,'not_a_benchmark_attempt':True})
b=None
try:
    import numpy as np
    from PIL import Image
    from manipulation_agent.executors.omnigibson_rgb import RGBBackend
    b=RGBBackend(a.task,301,a.output,seed=0,record_video=True,inside_placement='official_volume')
    r.run['backend']=b.provenance();write_json(a.output/'run.json',r.run)
    snapshot=b.og.sim.dump_state(serialized=False)
    initial_pos,initial_quat=b.robot.get_position_orientation()
    latency=[]
    for case,(shift,yaw) in enumerate([(0,.8),(.2,-1.0),(-.2,1.8)]):
        pos=initial_pos.clone();pos[0]+=shift
        quat=b.torch.tensor([0.,0.,math.sin(yaw/2),math.cos(yaw/2)],device=initial_quat.device)
        b.robot.set_position_orientation(pos,quat);b.robot.keep_still();b._position_rig()
        packets=[]
        for tick in range(8):
            b.og.sim.render()
            views={}
            for name,sensor in b.rig.items():
                data,_=sensor.get_obs()
                views[name]={key:data[key].detach().cpu().numpy().copy() for key in ['rgb','depth_linear']}
            packets.append(views)
        reference=packets[-1]
        for tick in (2,3,4):
            metrics={}
            for name in b.rig:
                depth=packets[tick-1][name]['depth_linear'];truth=reference[name]['depth_linear']
                valid=np.isfinite(truth)&(truth>0)&(truth<30)
                stable=np.isfinite(depth)&(np.abs(depth-truth)<=.01)
                disagreement=float(np.mean(~stable[valid])) if valid.any() else 1.
                rgb=packets[tick-1][name]['rgb'][...,:3].astype(float)
                ref=reference[name]['rgb'][...,:3].astype(float)
                metrics[name]={'depth_disagreement_fraction':disagreement,'rgb_mean_absolute_error':float(np.mean(np.abs(rgb-ref)))}
                if case==0:
                    Image.fromarray(rgb.astype('uint8')).save(a.output/f'latency_{name}_{tick}.png')
            latency.append({'case':case,'renders':tick,'metrics':metrics,
                'passed':all(m['depth_disagreement_fraction']<.005 and m['rgb_mean_absolute_error']<3 for m in metrics.values())})
    accepted=[ticks for ticks in (2,3,4) if all(x['passed'] for x in latency if x['renders']==ticks)]
    recommended=min(accepted) if accepted else 4
    measurements=[]
    for trial,flushes in enumerate([4,recommended,4,recommended]):
        b.og.sim.load_state(snapshot,serialized=False)
        b._spectator_anchor=None;b._recorded_pixels=None;b.video_render_flushes=flushes
        b.observe();before=b.steps;start=time.perf_counter()
        b.mark_video_tool('look',{'yaw_degrees':60},f'timing-{trial}')
        result=b.execute_visual('look',None,700,yaw_degrees=60)
        elapsed=time.perf_counter()-start;pos,quat=b.robot.get_position_orientation()
        measurements.append({'trial':trial,'flushes':flushes,'seconds':elapsed,'steps':b.steps-before,
            'position':pos.tolist(),'orientation':quat.tolist(),'result':result})
    b.observe()
    observations=[json.loads(line) for line in (a.output/'observation_capture_audit.jsonl').read_text().splitlines()]
    comparable=all(np.allclose(x['position'],measurements[0]['position'],atol=1e-4)
                   and np.allclose(x['orientation'],measurements[0]['orientation'],atol=1e-4)
                   and x['steps']==measurements[0]['steps'] for x in measurements)
    baseline=statistics.mean(x['seconds'] for x in measurements[::2])
    optimized=statistics.mean(x['seconds'] for x in measurements[1::2])
    validation={'status':'passed' if accepted and comparable and all(x['no_robot_motion'] for x in observations) else 'failed',
        'level':'scripted_frozen_state_render_latency_and_paired_motor_timing','task_success_assessed':False,
        'latency':latency,'recommended_video_render_flushes':recommended,'observation_render_flushes':4,
        'paired_final_pose_and_steps_match':comparable,'timings':measurements,
        'baseline_4_flush_seconds':baseline,'candidate_seconds':optimized,'local_speedup':baseline/optimized,
        'limitations':['One scene and diagnostic motion; not end-to-end task speedup','Continuous video only; policy observation barrier unchanged']}
    write_json(a.output/'validation.json',validation)
    r.finish({'status':validation['status'],'task_success':None,'sim_steps':b.steps,'video':b.finalize_video(),'validation':validation})
    print(json.dumps(validation),flush=True)
except Exception as exc:
    write_json(a.output/'validation.json',{'status':'failed','error':str(exc),'level':'real_render_diagnostic'})
    (a.output/'traceback.txt').write_text(traceback.format_exc());raise
finally:
    if b is not None:b.close()
