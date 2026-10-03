"""Scripted upstream component probe; uses private target identity, not a policy."""
import argparse
import json
from pathlib import Path
import traceback

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import write_execution_clock
from manipulation_agent.executors.official_symbolic import OfficialSymbolicBackend, OFFICIAL_PRIMITIVES
from manipulation_agent.records import Recorder, write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--task',default='turning_on_radio')
    p.add_argument('--instance',type=int,default=301)
    p.add_argument('--seed',type=int,default=0)
    p.add_argument('--object-name',help='Private exact scene object; diagnostic only')
    p.add_argument('--primitives',nargs='+',choices=OFFICIAL_PRIMITIVES,
                   default=['navigate_to','toggle_on','toggle_off'])
    p.add_argument('--max-sim-steps',type=int,default=20000)
    p.add_argument('--execution-seconds',type=float,default=1800)
    a=p.parse_args()
    if a.task!='turning_on_radio' and not a.object_name:
        p.error('--object-name is required for a non-radio diagnostic')
    if a.max_sim_steps<=0 or a.execution_seconds<=0:
        p.error('Execution budgets must be positive')
    rec=Recorder(a.output,{'backend':'omnigibson','task':a.task,'instance':a.instance,'seed':a.seed,
        'agent_profile':'official','observation_mode':'rgb_only','model_used':False,
        'diagnostic_target_object':a.object_name,'diagnostic_primitives':a.primitives,
        'validation_level':'scripted_official_component_probe_private_target_identity'})
    b=None;checks={}
    try:
        b=OfficialSymbolicBackend(a.task,a.instance,a.output,seed=a.seed,max_steps=a.max_sim_steps,record_video=True)
        rec.run['backend']=b.provenance();write_json(a.output/'run.json',rec.run)
        checks['native_inventory_14']=len(OFFICIAL_PRIMITIVES)==len(b.primitives.controller_functions)==14
        checks['no_custom_carry']=b.ideal_carry is False
        observation=b.observe();rec.event('diagnostic_observation',{'observation':observation})
        checks['four_rgb']=len(observation['images'])==4
        # The scripted probe explicitly bypasses RGB grounding to isolate the
        # native dispatcher. A separate fresh episode tests actual Astra + RGB.
        targets=[o for o in b.env.scene.objects if
                 (o.name==a.object_name if a.object_name else o.category=='radio')]
        assert len(targets)==1, 'Probe requires exactly one selected object'
        obj=targets[0]
        b._ground=lambda target:(obj,obj.get_position_orientation()[0],{'scripted_private_target':True})
        target={'image_ref':observation['images'][0]['image_ref'],'point':[.5,.5]}
        original_physics=b.og.sim.step_physics
        # This standalone probe has no model launcher to arm its private clock.
        # Start only after simulator initialization and the first RGB capture.
        if b.deadline.clock_path is not None:
            write_execution_clock(b.deadline.clock_path,a.execution_seconds)
        else:
            b.deadline.arm_local(a.execution_seconds)
        b.deadline.check()
        rec.run.update(execution_clock=b.deadline.clock, max_sim_steps=a.max_sim_steps)
        write_json(a.output/'run.json',rec.run)
        for index,primitive in enumerate(a.primitives):
            action_target=None if primitive=='release' else target
            marker=rec.event('diagnostic_call',{'primitive':primitive,'target_source':'private_scripted_fixture'})
            b.mark_video_tool('act',{'primitive':primitive,'target':action_target},marker)
            error=None
            try: result=b.execute_visual(primitive,action_target,a.max_sim_steps-b.steps)
            except SkillError as exc: error={'code':exc.code,'detail':str(exc)};result=None
            if not b.deadline.expired:
                observation=b.observe()
            rec.event('diagnostic_result',{'primitive':primitive,'result':result,'error':error,'observation':observation})
            key=f'{index:02d}_{primitive}'
            checks[key+'_wrapper_restored']=b.og.sim.step_physics==original_physics
            checks[key+'_upstream_completed']=error is None
            if primitive in {'toggle_on','toggle_off'}:
                from omnigibson.object_states import ToggledOn
                checks[key+'_official_state_verified']=error is None and bool(obj.states[ToggledOn].get_value())==(primitive=='toggle_on')
            elif primitive=='grasp':
                checks[key+'_native_in_hand_verified']=error is None and b.primitives._get_obj_in_hand()==obj
            elif primitive=='release':
                checks[key+'_native_empty_hand_verified']=error is None and b.primitives._get_obj_in_hand() is None
            if b.deadline.expired:
                break
        control_file=a.output/'official_control_steps.jsonl'
        control_count=len(control_file.read_text().splitlines()) if control_file.exists() else 0
        checks['raw_control_steps_recorded']=control_count==b.steps
        validation={'status':'passed' if all(checks.values()) else 'failed','checks':checks,'task_success_assessed':False,'model_used':False,
                    'known_limitation':'Private scripted component validation; does not validate Astra RGB task completion'}
        write_json(a.output/'validation.json',validation)
        rec.finish({'status':validation['status'],'task_success':None,'video':b.finalize_video(),'sim_steps':b.steps,'validation':validation})
        return 0 if all(checks.values()) else 2
    except Exception as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        write_json(a.output/'validation.json',{'status':'failed','checks':checks,'error_type':type(exc).__name__,'model_used':False,'task_success_assessed':False})
        rec.finish({'status':'failed','task_success':None,'failure':str(exc),'sim_steps':b.steps if b else 0,'video':b.finalize_video() if b else None})
        raise
    finally:
        if b is not None:b.close()


if __name__=='__main__':raise SystemExit(main())
