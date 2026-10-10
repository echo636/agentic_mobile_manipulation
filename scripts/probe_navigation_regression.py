"""Private archived navigation geometry regression; not an autonomous benchmark."""
import argparse
import json
import math
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--fixture',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();fixture=json.loads(a.fixture.read_text())
    recorder=Recorder(a.output,dict(fixture,policy='private_archived_geometry_navigation_probe',model_used=False,
                                  not_a_benchmark_attempt=True,observation_mode='rgb_only'))
    validation={'status':'running','checks':{},'cases':[], 'validation_level':'real_simulator_private_navigation_component'}
    backend=harness=None
    def save():write_json(a.output/'validation.json',validation)
    save()
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.executors.live_gt_map import build_navigation_grid
        from manipulation_agent.executors.gt_navigation import plan_navigation,NavigationError
        from manipulation_agent.vision_harness import VisionHarness
        from manipulation_agent.contracts import Budget
        from omnigibson.object_states import Open
        backend=RGBBackend(fixture['task'],301,a.output,seed=0,max_steps=40000,inside_placement='official_volume',record_video=True)
        backend.deadline.arm_local(1800)
        harness=VisionHarness(backend,recorder,Budget(wall_seconds=1800),profile='skills')
        doors={obj.name:obj for obj in backend.env.scene.objects if obj.name in fixture['doors']}
        if set(doors)!=set(fixture['doors']):raise ValueError('Expected diagnostic doors absent')
        validation['doors']={name:{'category':obj.category,'initial_open':obj.states[Open].get_value()} for name,obj in doors.items()}
        floor=min(range(len(backend.env.scene.trav_map.floor_heights)),key=lambda i:abs(float(backend.robot.get_position_orientation()[0][2])-backend.env.scene.trav_map.floor_heights[i]))
        grid,meta=build_navigation_grid(backend,floor)
        import numpy as np
        np.savez_compressed(a.output/'initial_navigation_map.npz',free=np.frombuffer(grid.free,dtype='uint8').reshape(grid.height,grid.width),origin=grid.origin,resolution=grid.resolution)
        validation['initial_map']=meta;save();print(json.dumps({'stage':'initialized','map':meta}),flush=True)
        for case in fixture['cases']:
            case_record={'input':case};validation['cases'].append(case_record)
            for door in doors.values():door.states[Open].set_value(False,fully=True)
            for _ in range(10):backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
            pos,quat=backend.robot.get_position_orientation();pos[:2]=backend.torch.tensor(case['start'],device=pos.device)
            if case.get('start_orientation') is not None:
                quat=backend.torch.tensor(case['start_orientation'],device=quat.device)
            backend.robot.set_position_orientation(pos,quat)
            closed_grid,closed_meta=build_navigation_grid(backend,floor)
            np.savez_compressed(a.output/(case['name']+'_closed_map.npz'),free=np.frombuffer(closed_grid.free,dtype='uint8').reshape(closed_grid.height,closed_grid.width),origin=closed_grid.origin,resolution=closed_grid.resolution)
            try:
                plan=plan_navigation(closed_grid,case['start'],case['world_target'][:2],goal_mode=case['goal_mode'])
                case_record['closed_door_plan']={'status':'planned','goal':plan.goal}
            except NavigationError as exc:case_record['closed_door_plan']={'status':'blocked','error':str(exc)}
            for door in doors.values():
                if not door.states[Open].set_value(True,fully=True):raise ValueError('Diagnostic door failed to open')
            for _ in range(10):backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
            backend.robot.set_position_orientation(pos,quat)
            harness.refresh()
            point=backend.torch.tensor(case['world_target'],dtype=backend.torch.float32)
            target=SimpleNamespace(get_position_orientation=lambda:(point,None),selected_object=None)
            recorder.event('diagnostic_navigation_begin',case)
            result=backend._navigate(target,20000,for_manipulation=case.get('for_manipulation',False))
            obs=harness.refresh()
            actual=backend.robot.get_position_orientation()[0].cpu().tolist()
            case_record.update(status='passed',result=result,actual_position=actual,views=[i['view'] for i in obs['images']],
                               clicked_xy_error_m=math.dist(actual[:2],case['world_target'][:2]))
            validation['checks'][case['name']+'_reached']=result['nav_status']=='reached'
            if case['goal_mode']=='point':validation['checks'][case['name']+'_near_click']=case_record['clicked_xy_error_m']<=.15
            else:validation['checks'][case['name']+'_within_reach']=case_record['clicked_xy_error_m']<=1.4
            if doors:validation['checks'][case['name']+'_closed_door_blocks']=case_record['closed_door_plan']['status']=='blocked'
            recorder.event('diagnostic_navigation_result',case_record);save();print(json.dumps(case_record),flush=True)
    except Exception as exc:
        validation['error']={'type':type(exc).__name__,'message':str(exc)}
        (a.output/'traceback.txt').write_text(traceback.format_exc());print(traceback.format_exc(),flush=True)
    finally:
        try:
            if harness is not None:
                try:
                    result=harness.call('finish',{'outcome':'aborted','reason':'Private navigation component probe, not an autonomous challenge result.'},'diagnostic-finish')
                    harness.finalize_recording()
                    validation['checks']['finish_closed']=result.get('closed') is True
                    validation['checks']['scoring_completed']=recorder.run.get('scoring',{}).get('status')=='passed'
                    validation['checks']['video_finalized']=recorder.run.get('video',{}).get('status')=='passed'
                except Exception as exc:validation['finish_error']=str(exc)
            validation['status']='passed' if not validation.get('error') and not validation.get('finish_error') and validation['checks'] and all(validation['checks'].values()) else 'failed'
            save()
        finally:
            if backend is not None:backend.close()
    print(json.dumps(validation),flush=True)
    return 0 if validation['status']=='passed' else 2

if __name__=='__main__':raise SystemExit(main())
