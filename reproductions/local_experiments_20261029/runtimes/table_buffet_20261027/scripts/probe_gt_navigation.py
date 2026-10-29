"""Real-simulator scripted navigation regression; never a model benchmark."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder, now, write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--scenario',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--record-video',action='store_true');a=p.parse_args()
    scenario=json.loads(a.scenario.read_text())
    config={'backend':'omnigibson','policy':'scripted_private_geometry_regression',
            'validation_level':'real_simulator_navigation_only','observation_mode':'rgb_only',
            'task':scenario['task'],'instance':scenario['instance'],'seed':0,
            'not_a_benchmark_attempt':True,'full100_remains_paused':True}
    recorder=Recorder(a.output,config);backend=None;checks=[]
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        backend=RGBBackend(scenario['task'],scenario['instance'],a.output,seed=0,record_video=a.record_video)
        recorder.run['backend']=backend.provenance();write_json(a.output/'run.json',recorder.run)
        spawn,orientation=backend.robot.get_position_orientation()
        position=backend.torch.tensor(scenario['start_position'],dtype=spawn.dtype,device=spawn.device)
        backend.robot.set_position_orientation(position,orientation);backend.robot.keep_still()
        recorder.event('diagnostic_pose_setup',{'at':now(),'fixture':scenario,'purpose':'Reproduce an archived invalid-start navigation failure; not model-selected state'})
        recorder.event('diagnostic_rgb',{'observation':backend.observe()})
        for index,target in enumerate((scenario['target_world'],spawn.cpu().tolist())):
            if index:
                current,quat=backend.robot.get_position_orientation()
                current[0]-=1e-5;current[1]+=1e-5
                backend.robot.set_position_orientation(current,quat);backend.robot.keep_still()
                recorder.event('diagnostic_pose_perturbation',{'delta_xy_m':[-1e-5,1e-5]})
            point=backend.torch.tensor(target,dtype=spawn.dtype)
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None))
            before=backend.robot.get_position_orientation()[0].cpu().tolist()
            backend.mark_video_tool('diagnostic_navigation',{'primitive':'navigate_to'},'scripted-'+str(index))
            recorder.event('diagnostic_navigation_started',{'index':index,'target_world':target,'start_position':before})
            result=backend._navigate(anchor,700)
            obs=backend.observe()
            recorder.event('diagnostic_navigation_result',{'index':index,'result':result,'observation':obs})
            checks.append({'index':index,'status':'passed' if result['nav_status']=='reached' else 'failed','result':result})
        video=backend.finalize_video()
        validation={'status':'passed' if all(c['status']=='passed' for c in checks) else 'failed',
                    'level':'real_simulator_scripted_navigation_only','cases':checks,
                    'model_used':False,'task_success_assessed':False,'full100_remains_paused':True}
        write_json(a.output/'validation.json',validation)
        recorder.finish({'status':validation['status'],'task_success':None,'validation':validation,
                         'video':video,'actions':0,'tool_calls':0,'sim_steps':backend.steps})
        print(json.dumps(validation),flush=True)
    except Exception as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        recorder.event('diagnostic_failed',{'type':type(exc).__name__,'message':str(exc)})
        recorder.finish({'status':'failed','task_success':None,'failure':str(exc),'completed_cases':checks})
        raise
    finally:
        if backend is not None:backend.close()


if __name__=='__main__':main()
