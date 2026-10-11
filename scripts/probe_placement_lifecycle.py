"""Replay the archived canning-food placement failure. Private diagnostic, not a model trial."""
import argparse
import copy
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder, write_json
from probe_surface_placement import fixture_from_archive
from probe_recorded_cut import find_q


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive-run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    fixture=fixture_from_archive(a.archive_run)
    recorder=Recorder(a.output,{'task':fixture['task'],'instance':fixture['instance'],
        'seed':fixture['seed'],'backend':'omnigibson','policy':'private_recorded_placement_lifecycle',
        'instruction':'Private archived-pixel lifecycle regression; no model and no full-task success claim.',
        'record_video':True,'model_used':False,'not_a_benchmark_attempt':True})
    write_json(a.output/'fixture.json',fixture)
    validation={'status':'running','validation_level':'real_simulator_archived_placement_lifecycle',
                'model_used':False,'checks':{},'actions':[]}
    write_json(a.output/'validation.json',validation)
    backend=harness=None
    try:
        from manipulation_agent.contracts import Budget
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.vision_harness import VisionHarness
        backend=RGBBackend(fixture['task'],fixture['instance'],a.output,seed=fixture['seed'],
                           max_steps=20000,inside_placement='official_volume',record_video=True)
        backend.deadline.arm_local(1800)
        harness=VisionHarness(backend,recorder,Budget(wall_seconds=1800),profile='skills')
        harness.call('initialize',{},'probe-initialize')
        for call in fixture['prefix']:
            args=copy.deepcopy(call['arguments']);args['revision']=harness.revision
            primitive=args.get('primitive',call['name'])
            if args.get('target'):
                target=args['target'];view=target['image_ref'].rsplit('-',1)[-1]
                target['image_ref']=next(i['image_ref'] for i in harness.snapshot['images'] if i['view']==view)
                obj,point,grounding=backend._ground(target,require_object=primitive!='navigate_to')
                expected=(call['archived_grounding'].get('selected_object_prim') or '').rsplit('/',1)[-1]
                actual=obj.name if obj is not None else ''
                recorder.event('diagnostic_grounding',{'expected':expected,'actual':actual,'grounding':grounding})
                if expected and actual!=expected:
                    raise RuntimeError('Archived pixel grounded to different object; refusing substituted target')
            if primitive=='place_on_top':
                held=backend._get_held();before={obj.name for obj in backend.env.scene.objects}
                validation['held_before']=held.name
            result=harness.call(call['name'],args,'probe-'+call['id'])
            item={'primitive':primitive,'ok':result.get('ok'),'error':result.get('error'),
                  'views':[i['view'] for i in result.get('observation',{}).get('images',[])]}
            validation['actions'].append(item);print(json.dumps(item),flush=True)
            if primitive=='place_on_top':
                current={obj.name for obj in backend.env.scene.objects}
                validation['added_objects']=sorted(current-before)
                validation['checks'].update(original_held_removed=validation['held_before'] not in current,
                    replacement_present=bool(current-before),
                    recoverable_error=result.get('error',{}).get('code')=='target_changed',
                    fresh_four_views=set(item['views'])=={'front','back','left','right'},
                    stale_carry_cleared=backend._get_held() is None)
                followup=harness.call('act',{'primitive':'wait','revision':harness.revision,'wait_seconds':0.1,
                    'target':None,'placement_yaw_degrees':None},'probe-followup')
                validation['followup_result']={k:v for k,v in followup.items() if k!='observation'}
                validation['checks']['next_tool_still_works']=followup.get('ok') is True
            elif not result.get('ok'):
                raise RuntimeError('Archived prefix diverged: '+str(item))
            write_json(a.output/'validation.json',validation)
    except Exception as exc:
        validation['error']={'type':type(exc).__name__,'message':str(exc)}
        (a.output/'traceback.txt').write_text(traceback.format_exc())
    finally:
        try:
            if harness is not None:
                try:
                    result=harness.call('finish',{'outcome':'aborted','reason':'Private placement lifecycle diagnostic completed; no full-task success claim.'},'probe-finish')
                    validation['q_score']=find_q(recorder.run.get('evaluation',{}))
                    validation['checks'].update(finish_closed=result.get('closed') is True,
                        official_score_collected=validation['q_score'] is not None)
                    harness.finalize_recording()
                    validation['checks']['video_finalized']=recorder.run.get('video',{}).get('status')=='passed'
                except Exception as exc:
                    validation['finish_error']={'type':type(exc).__name__,'message':str(exc)}
            validation['status']='passed' if validation['checks'] and all(validation['checks'].values()) and not validation.get('error') and not validation.get('finish_error') else 'failed'
            write_json(a.output/'validation.json',validation)
        finally:
            if backend is not None:backend.close()
            else:
                from manipulation_agent.startup_cleanup import shutdown_partial_simulator
                shutdown_partial_simulator()
    print(json.dumps(validation),flush=True)
    return 0 if validation['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
