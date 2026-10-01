"""Scripted upstream component probe; uses private target identity, not a policy."""
import argparse
import json
from pathlib import Path
import traceback

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.official_symbolic import OfficialSymbolicBackend, OFFICIAL_PRIMITIVES
from manipulation_agent.records import Recorder, write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    rec=Recorder(a.output,{'backend':'omnigibson','task':'turning_on_radio','instance':301,'seed':0,
        'agent_profile':'official','observation_mode':'rgb_only','model_used':False,
        'validation_level':'scripted_official_component_probe_private_target_identity'})
    b=None;checks={}
    try:
        b=OfficialSymbolicBackend('turning_on_radio',301,a.output,seed=0,max_steps=1000,record_video=True)
        rec.run['backend']=b.provenance();write_json(a.output/'run.json',rec.run)
        checks['native_inventory_14']=len(OFFICIAL_PRIMITIVES)==len(b.primitives.controller_functions)==14
        checks['no_custom_carry']=b.ideal_carry is False
        observation=b.observe();rec.event('diagnostic_observation',{'observation':observation})
        checks['four_rgb']=len(observation['images'])==4
        # The scripted probe explicitly bypasses RGB grounding to isolate the
        # native dispatcher. A separate fresh episode tests actual Astra + RGB.
        targets=[o for o in b.env.scene.objects if o.category=='radio']
        assert len(targets)==1, 'Probe requires exactly one radio'
        obj=targets[0]
        b._ground=lambda target:(obj,obj.get_position_orientation()[0],{'scripted_private_target':True})
        target={'image_ref':observation['images'][0]['image_ref'],'point':[.5,.5]}
        original_physics=b.og.sim.step_physics
        for primitive in ['navigate_to','toggle_on','toggle_off']:
            marker=rec.event('diagnostic_call',{'primitive':primitive,'target_source':'private_scripted_fixture'})
            b.mark_video_tool('act',{'primitive':primitive,'target':target},marker)
            error=None
            try: result=b.execute_visual(primitive,target,300)
            except SkillError as exc: error={'code':exc.code,'detail':str(exc)};result=None
            observation=b.observe()
            rec.event('diagnostic_result',{'primitive':primitive,'result':result,'error':error,'observation':observation})
            checks[primitive+'_wrapper_restored']=b.og.sim.step_physics==original_physics
            if primitive=='navigate_to':
                ledger=json.loads((a.output/'official_primitives.jsonl').read_text().splitlines()[-1])
                checks['upstream_navigation_defect_reproduced']=ledger.get('error_type')=='AttributeError' and 'NoneType' in ledger.get('error','')
            else:
                from omnigibson.object_states import ToggledOn
                checks[primitive+'_official_state_verified']=error is None and bool(obj.states[ToggledOn].get_value())==(primitive=='toggle_on')
        checks['raw_control_steps_recorded']=len((a.output/'official_control_steps.jsonl').read_text().splitlines())==b.steps
        validation={'status':'passed' if all(checks.values()) else 'failed','checks':checks,'task_success_assessed':False,'model_used':False,
                    'known_limitation':'Official NAVIGATE_TO failed; component audit passing does not mean navigation works'}
        write_json(a.output/'validation.json',validation)
        rec.finish({'status':validation['status'],'task_success':None,'video':b.finalize_video(),'sim_steps':b.steps,'validation':validation})
        assert all(checks.values()),checks
    except Exception as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        write_json(a.output/'validation.json',{'status':'failed','checks':checks,'error_type':type(exc).__name__,'model_used':False,'task_success_assessed':False})
        rec.finish({'status':'failed','task_success':None,'failure':str(exc),'sim_steps':b.steps if b else 0,'video':b.finalize_video() if b else None})
        raise
    finally:
        if b is not None:b.close()


if __name__=='__main__':main()
