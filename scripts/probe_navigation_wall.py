"""Private real-simulator regression for task003's outside-wall sink approach.

Sets the archived base pose and carries the same bowl, then reuses the archived
RGB pixel through the real harness. This is component validation, not a model
episode or a full reproduction of all preceding plate placements.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder,write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();fixture=json.loads(args.fixture.read_text())
    recorder=Recorder(args.output,{'task':fixture['task'],'instance':301,'seed':0,
        'backend':'omnigibson','policy':'private_archived_wall_navigation_regression',
        'model_used':False,'not_a_benchmark_attempt':True,
        'instruction':'Private navigation regression: reuse archived sink pixel; not a full-task trial.'})
    write_json(args.output/'fixture.json',fixture)
    validation={'status':'running','validation_level':'real_simulator_navigation_component',
                'model_used':False,'checks':{}}
    backend=harness=None
    def save():write_json(args.output/'validation.json',validation)
    save()
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.runtime.vision_harness import VisionHarness
        from manipulation_agent.contracts import Budget
        backend=RGBBackend(fixture['task'],301,args.output,seed=0,max_steps=20000,
                           inside_placement='official_volume',record_video=True)
        backend.deadline.arm_local(1800)
        pos,quat=backend.robot.get_position_orientation()
        pos=backend.torch.tensor(fixture['start_position'],dtype=pos.dtype,device=pos.device)
        quat=backend.torch.tensor(fixture['start_orientation'],dtype=quat.dtype,device=quat.device)
        backend.robot.set_position_orientation(pos,quat);backend.robot.keep_still()
        bowl=next(o for o in backend.env.scene.objects if o.name==fixture['held_object'])
        recorder.event('diagnostic_setup',{'fixture':fixture,
            'scope':'Archived base pose and carried bowl; preceding plate placements are not replayed.'})
        backend._ideal_grasp(bowl,20)
        backend.frames_revision=-1
        harness=VisionHarness(backend,recorder,Budget(wall_seconds=1800,max_steps_per_action=20000),profile='skills')
        harness.call('initialize',{},'probe-initialize')
        target={'image_ref':next(im['image_ref'] for im in harness.snapshot['images'] if im['view']==fixture['view']),
                'point':fixture['pixel']}
        _,world,grounding=backend._ground(target,require_object=False)
        validation['grounded_point']=world.cpu().tolist()
        validation['archive_point_error_m']=math.dist(world.cpu().tolist(),fixture['world_target'])
        validation['checks']['same_visual_target']=validation['archive_point_error_m']<.05
        validation['old_goal_visible']=backend._approach_visible(fixture['old_goal'],world,None,
            margin=0.,max_distance=1.4,base_yaw=fixture['travel_yaw'])
        validation['checks']['old_outside_goal_rejected']=not validation['old_goal_visible']
        recorder.event('diagnostic_grounding',{'grounding':grounding,**validation})
        save()
        if not validation['checks']['same_visual_target']:
            raise RuntimeError('Archived pixel no longer grounds to the same sink surface')
        result=harness.call('act',{'primitive':'navigate_to','revision':harness.revision,'target':target,
            'placement_yaw_degrees':None,'wait_seconds':None},'probe-navigate')
        validation['tool_result']={k:v for k,v in result.items() if k!='observation'}
        actual=backend.robot.get_position_orientation()[0].cpu().tolist()
        validation['actual_position']=actual
        plans=[json.loads(line) for line in (args.output/'navigation_plans.jsonl').read_text().splitlines()]
        plan=plans[-1]['plan'];validation['plan']=plan
        arrival=json.loads((args.output/'navigation_arrivals.jsonl').read_text().splitlines()[-1])
        validation['arrival']=arrival
        validation['checks'].update(tool_succeeded=result.get('ok') is True,
            four_rgb_returned={im['view'] for im in result.get('observation',{}).get('images',[])}=={'front','back','left','right'},
            indoor_endpoint=actual[0]<fixture['world_target'][0],
            target_visible_at_arrival=arrival['target_visible'] is True,
            avoided_16m_detour=plan['geodesic_m']<8.,
            bowl_still_carried=backend._get_held() is bowl)
        save();print(json.dumps(validation),flush=True)
    except Exception as exc:
        validation['error']={'type':type(exc).__name__,'message':str(exc)}
        (args.output/'traceback.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(),flush=True)
    finally:
        try:
            if harness is not None:
                try:
                    result=harness.call('finish',{'outcome':'aborted',
                        'reason':'Private navigation regression completed; no full-task success claim.'},'probe-finish')
                    harness.finalize_recording()
                    validation['checks'].update(finish_closed=result.get('closed') is True,
                        official_score_collected=recorder.run.get('scoring',{}).get('status')=='passed',
                        video_finalized=recorder.run.get('video',{}).get('status')=='passed')
                except Exception as exc:validation['finish_error']=str(exc)
            validation['status']='passed' if validation['checks'] and all(validation['checks'].values()) and not validation.get('error') and not validation.get('finish_error') else 'failed'
            save()
        finally:
            if backend is not None:backend.close()
            else:
                from manipulation_agent.batch.startup_cleanup import shutdown_partial_simulator
                shutdown_partial_simulator()
    print(json.dumps(validation),flush=True)
    return 0 if validation['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
