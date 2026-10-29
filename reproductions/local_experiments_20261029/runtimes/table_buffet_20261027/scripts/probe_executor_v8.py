"""Privileged real-simulator regression; never an autonomous benchmark episode."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.contracts import SkillError
from manipulation_agent.records import Recorder,write_json

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
p.add_argument('--task',default='cleaning_up_plates_and_food');args=p.parse_args()
rec=Recorder(args.output,{'task':args.task,'instance':301,'seed':0,
    'policy':'scripted_private_target_regression','model_used':False,
    'validation_level':'real_actuator_components_not_autonomous_task_success'})
b=None;checks=[]

def check(name,passed,**evidence):
    row={'name':name,'status':'passed' if passed else 'failed',**evidence};checks.append(row)
    rec.event('diagnostic_check',row);write_json(args.output/'validation_progress.json',{'status':'running','checks':checks})
    print(json.dumps(row),flush=True)
    if not passed:raise AssertionError(name)

def operation(name,fn):
    before=b.robot.get_position_orientation()[0].clone();start=time.monotonic()
    b.mark_video_tool('diagnostic',{'primitive':name},name)
    rec.event('diagnostic_call',{'name':name,'selection':'privileged known object; no LLM'})
    result=fn();obs=b.observe()
    rec.event('diagnostic_result',{'name':name,'result':result,'observation':obs,
        'wall_seconds':time.monotonic()-start})
    return result,float(b.torch.linalg.norm(b.robot.get_position_orientation()[0]-before))

try:
    from manipulation_agent.executors.omnigibson_rgb import RGBBackend
    from types import SimpleNamespace
    b=RGBBackend(args.task,301,args.output,seed=0,record_video=True,inside_placement='official_volume')
    from omnigibson.object_states import Inside,OnTop,Touching
    from omnigibson.utils import transform_utils as T
    from omnigibson.utils.sampling_utils import raytest
    rec.run['backend']=b.provenance();write_json(args.output/'run.json',rec.run)
    rec.event('diagnostic_initial_observation',{'observation':b.observe()})
    plate=b.env.scene.object_registry('name','plate_93');pizza=b.env.scene.object_registry('name','pizza_89')
    fridge=b.env.scene.object_registry('name','fridge_petcxr_0')
    check('known_regression_objects_loaded',all(o is not None for o in (plate,pizza,fridge)))
    check('initial_pizza_supported_by_plate',bool(pizza.states[OnTop].get_value(plate)))
    center=sum(plate.aabb)/2;end=center.clone();end[2]-=.3
    ignore=[link.prim_path for obj in (plate,pizza,b.robot) for link in obj.links.values()]
    hit=raytest(center,end,ignore_bodies=ignore)
    support=next((o for o in b.env.scene.objects if any(link.prim_path==hit.get('rigidBody') for link in o.links.values())),None)
    check('initial_support_ray_resolved',hit['hit'] and support is not None)
    surface=hit['position'].clone()
    def navigate(obj,point):
        return b._navigate(SimpleNamespace(get_position_orientation=lambda:(point,None),selected_object=obj),900)
    operation('navigate_to_plate',lambda:navigate(plate,center))
    operation('grasp_plate_with_food',lambda:b._ideal_grasp(plate,100))
    check('pizza_in_carry_payload',any(obj is pizza for obj,_ in b._carry_contents),
          payload=[{'child':c.name,'parent':p.name,'relation':r} for c,p,r in b._carry_dependencies])
    relative=T.relative_pose_transform(*pizza.get_position_orientation(),*plate.get_position_orientation())
    operation('turn_with_food',lambda:b._turn(35,200))
    _,drift=operation('wait_with_food',lambda:b.execute_visual('wait',None,100,seconds=2))
    current=T.relative_pose_transform(*pizza.get_position_orientation(),*plate.get_position_orientation())
    check('supported_payload_retains_relative_pose',all(b.torch.allclose(x,y,atol=1e-4) for x,y in zip(relative,current)))
    check('wait_holds_base',drift<1e-4,base_drift_m=drift)
    result,drift=operation('place_plate_back_on_selected_table',lambda:b._checked_place_on_top(support,300,surface))
    check('table_placement_keeps_food_on_plate',bool(pizza.states[OnTop].get_value(plate)))
    check('surface_placement_holds_base',drift<1e-4,base_drift_m=drift)
    operation('open_fridge',lambda:b._ideal_state_action('open',fridge,60))
    operation('regrasp_plated_food',lambda:b._ideal_grasp(plate,100))
    operation('approach_fridge',lambda:navigate(fridge,b.torch.tensor([5.04965448,-.39107591,1.31114352])))
    point=b.torch.tensor([4.84392357,-.30404085,1.31190062])
    base=b.robot.get_position_orientation()[0].clone();held_pose=tuple(v.clone() for v in plate.get_position_orientation())
    try:
        operation('selected_fridge_shelf',lambda:b._checked_place_on_top(fridge,300,point))
        rows=[json.loads(line) for line in (args.output/'placement_diagnostics.jsonl').read_text().splitlines()]
        last=next(row for row in reversed(rows) if row['status']=='postcondition_check')
        check('fridge_shelf_preserves_selected_height',last['selected_surface_support']['checks']['selected_shelf_height'],
              support=last['selected_surface_support'])
        operation('regrasp_after_valid_shelf_placement',lambda:b._ideal_grasp(plate,100))
    except SkillError as exc:
        check('infeasible_shelf_rejected_and_carry_restored',exc.code in {'sampling_error','postcondition_error'} and b._get_held() is plate,
              expected_rejection=exc.code,detail=str(exc))
        check('failed_shelf_restores_pose',all(b.torch.allclose(x,y,atol=1e-4) for x,y in zip(held_pose,plate.get_position_orientation())))
    check('shelf_attempt_holds_base',float(b.torch.linalg.norm(b.robot.get_position_orientation()[0]-base))<1e-4)
    result,drift=operation('place_plated_food_inside_fridge',lambda:b._checked_place_inside(fridge,700))
    check('inside_sampler_holds_base',drift<1e-4,base_drift_m=drift)
    check('plated_food_inside_fridge',bool(plate.states[Inside].get_value(fridge) and pizza.states[Inside].get_value(fridge)))
    check('food_still_supported_after_inside',bool(pizza.states[OnTop].get_value(plate)))
    operation('navigate_after_placement',lambda:navigate(support,surface))
    check('all_robot_and_payload_poses_finite',all(bool(b.torch.isfinite(v).all()) for o in (b.robot,plate,pizza) for v in o.get_position_orientation()))
    validation={'status':'passed','level':'real_actuator_components_not_autonomous_task_success','model_used':False,
                'task_success_assessed':False,'checks':checks}
    write_json(args.output/'validation.json',validation)
    rec.finish({'status':'passed','validation':validation,'task_success':None,'video':b.finalize_video(),'sim_steps':b.steps})
except Exception as exc:
    validation={'status':'failed','level':'real_actuator_components_not_autonomous_task_success','model_used':False,
                'task_success_assessed':False,'checks':checks,'error':str(exc)}
    write_json(args.output/'validation.json',validation);(args.output/'traceback.txt').write_text(traceback.format_exc())
    rec.finish({'status':'failed','failure':str(exc),'task_success':None,
                'video':b.finalize_video() if b else None,'sim_steps':b.steps if b else 0})
    raise
finally:
    if b is not None:b.close()
