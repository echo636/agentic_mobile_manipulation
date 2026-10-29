"""Private GROC-012 timeout recovery with manually chosen current-RGB pixel; never a policy run."""
import argparse
import json
import math
from pathlib import Path
from probe_navigation_prefix import load_plan,execute


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--item',required=True)
    parser.add_argument('--target',required=True)
    args=parser.parse_args()
    plan=load_plan(args.trial,27)
    if plan['actions'][25]['original_event']!='event-00099' or plan['actions'][26]['original_event']!='event-00106':
        raise ValueError('This case requires the frozen GROC-012 prefix')
    last=plan['actions'][-1]
    if last['expected_ok'] or last['expected_error']!='navigation_unreachable':
        raise ValueError('Original trace must contain the exit failure')
    plan['repair_check']={'scope':'scripted private placement/exit only','item':args.item,'target':args.target,
                          'changed_expected_event':last['original_event'],'original_expected_ok':False,
                          'requested_expected_ok':False,'exit_expected_error':'action_timeout','recovery_pixel_selection':'manual RGB','prefix_actions_unchanged':26}
    last['expected_ok']=False;last['expected_error']='action_timeout'
    def verify(backend,row,record):
        if row['original_event'] not in ('event-00099','event-00106'):return True
        if row['original_event']=='event-00106':
            from manipulation_agent.contracts import SkillError
            obs=backend.observe()
            image=next(im for im in obs['images'] if im['view']=='right')
            target_pixel={'image_ref':image['image_ref'],'point':[.35,.60]}
            _,_,ground=backend._ground(target_pixel)
            record['recovery_target']={'selection':'manual RGB floor pixel from prior terminal image; validated on fresh capture',
                                      'target':target_pixel,'grounding':ground}
            if 'floor' not in str(ground.get('selected_object_prim','')).lower():
                record['recovery_target']['rejected']='selected pixel is not floor';return False
            ok=True;error=None
            try:backend.execute_visual('navigate_to',target_pixel,700)
            except SkillError as exc:ok=False;error=exc.code
            record['actions'].append({'original_event':'private-recovery-001','primitive':'navigate_to',
                                      'ok':ok,'error':error,'sim_steps':backend.steps})
            if not ok:return False
        from omnigibson.object_states import NextTo,OnTop
        item=backend.env.scene.object_registry('name',args.item)
        target=backend.env.scene.object_registry('name',args.target)
        floors=[obj for obj in backend.env.scene.objects if 'floor' in str(getattr(obj,'category','')).lower()]
        supported=any(item.states[OnTop].get_value(floor) for floor in floors)
        position=backend.robot.get_position_orientation()[0].cpu().tolist()
        evidence={'next_to':bool(item.states[NextTo].get_value(target)),
                  'floor_supported':bool(supported),'hand_empty':backend._get_held() is None,
                  'robot_position':position}
        record.setdefault('private_acceptance',{})[row['original_event']]=evidence
        if row['original_event']=='event-00106':
            before=record['private_acceptance']['event-00099']['robot_position']
            evidence['base_moved_m']=math.dist(before[:2],position[:2])
            if evidence['base_moved_m']<=.5:return False
        return evidence['next_to'] and evidence['floor_supported'] and evidence['hand_empty']
    result=execute(plan,args.output,after_action=verify)
    passed=result['status']=='recorded_feedback_prefix_reproduced' and len(result['actions'])==28
    acceptance={'passed':passed,'model_used':False,'task_success_assessed':False,
                'scope':'28-action scripted placement, expected timeout, manually selected recovery; not autonomous or whole-task success',
                'prefix_status':result['status'],'private_acceptance':result.get('private_acceptance',{})}
    (args.output/'repair_acceptance.json').write_text(json.dumps(acceptance,indent=2))
    print(json.dumps(acceptance),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
