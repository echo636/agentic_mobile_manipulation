"""Alternating legacy/current recording on identical real simulator motion.

The supplied reference runtime is read-only. Only RGB capture and CPU recorder
methods are exchanged; motor, simulator, scene, GPU and frame cadence are fixed.
Two warmup trials are excluded. This privileged probe is not a model benchmark.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys
import time
from types import MethodType

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder,write_json

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
p.add_argument('--reference-runtime',type=Path,required=True);a=p.parse_args()
r=Recorder(a.output,{'task':'turning_on_radio','instance':301,'seed':0,'policy':'paired_recording_diagnostic',
    'model_used':False,'not_a_benchmark_attempt':True,'reference_runtime':str(a.reference_runtime)})
b=None
try:
    import numpy as np
    from manipulation_agent.executors.omnigibson_rgb import RGBBackend
    from manipulation_agent.video import EpisodeVideo
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    old_rgb_path=a.reference_runtime/'src/manipulation_agent/executors/omnigibson_rgb.py'
    old_video_path=a.reference_runtime/'src/manipulation_agent/video.py'
    old_rgb=load('manipulation_agent.executors.reference_rgb',old_rgb_path).RGBBackend
    old_video=load('manipulation_agent.reference_video',old_video_path).EpisodeVideo
    b=RGBBackend('turning_on_radio',301,a.output,seed=0,record_video=True,inside_placement='official_volume')
    b.video_render_flushes=4
    r.run['backend']=b.provenance();write_json(a.output/'run.json',r.run)
    state=b.og.sim.dump_state(serialized=False);pos,quat=b.robot.get_position_orientation()
    # Short controlled translation at the initial pose, identical in all trials.
    # This is a component probe, not navigation target selection by an agent.
    path=[pos[:2].tolist(),[float(pos[0])+.4,float(pos[1])]]
    from omnigibson.utils.transform_utils import quat2mat
    matrix=quat2mat(quat);yaw=math.atan2(float(matrix[1,0]),float(matrix[0,0]))
    methods=['_position_spectator','_video_frame','_render_rgb_views','observe']
    trials=[]
    for index,mode in enumerate(['reference','optimized','reference','optimized','optimized','reference','reference','optimized','optimized','reference']):
        cls=old_rgb if mode=='reference' else RGBBackend
        for name in methods:setattr(b,name,MethodType(getattr(cls,name),b))
        b.video.append=MethodType((old_video if mode=='reference' else EpisodeVideo).append,b.video)
        b.og.sim.load_state(state,serialized=False)
        b._spectator_anchor=None;b._recorded_pixels=None
        b.observe()
        start=time.perf_counter();before=b.steps
        b.mark_video_tool('navigate_to',{'primitive':'diagnostic_'+mode},'pair-'+str(index))
        b.observe()
        b._execute_base_path(path,yaw,700)
        b.observe()
        # Include queued encoder work in both measurements.
        while b.video.encoder_pending:b.video.encoder_pending.popleft().result(timeout=90)
        elapsed=time.perf_counter()-start
        end_pos,end_quat=b.robot.get_position_orientation()
        row={'index':index,'mode':mode,'warmup':index<2,'seconds':elapsed,'steps':b.steps-before,
             'position':end_pos.tolist(),'orientation':end_quat.tolist()}
        trials.append(row);print(json.dumps(row),flush=True)
        write_json(a.output/'validation_progress.json',{'status':'running','trials':trials})
    valid=all(x['steps']==trials[0]['steps'] and np.allclose(x['position'],trials[0]['position'],atol=1e-4)
        and np.allclose(x['orientation'],trials[0]['orientation'],atol=1e-4) for x in trials)
    observations=[json.loads(s) for s in (a.output/'observation_capture_audit.jsonl').read_text().splitlines()]
    medians={mode:statistics.median(x['seconds'] for x in trials if x['mode']==mode and not x['warmup']) for mode in ['reference','optimized']}
    validation={'status':'passed' if valid and all(x['no_robot_motion'] for x in observations) else 'failed',
        'level':'paired_real_recording_overhead_diagnostic','task_success_assessed':False,'trials':trials,
        'final_pose_and_control_steps_match':valid,'median_seconds':medians,'local_speedup':medians['reference']/medians['optimized'],
        'video_render_flushes':4,'observation_render_flushes':4,'video_stride':2,
        'reference_files':{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [old_rgb_path,old_video_path]},
        'limitations':['One scene and short diagnostic motion; not whole-task or whole-cohort speedup','GPU is shared; timing spread is retained; first two warmups excluded']}
    write_json(a.output/'validation.json',validation)
    r.finish({'status':validation['status'],'task_success':None,'sim_steps':b.steps,'validation':validation,'video':b.finalize_video()})
finally:
    if b is not None:b.close()
