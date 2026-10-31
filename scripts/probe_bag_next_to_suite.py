"""Private two-bag placement/door-retention regression; no policy or task score."""
import argparse
import json
import traceback
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=('doors','targets'),default='doors')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    report={'status':'incomplete','model_used':False,'task_success_assessed':False,
            'scope':'Private relocation and state helpers; two bags coexist; no navigation acceptance',
            'mode':args.mode,'states':[]}
    backend=None
    def save():
        (args.output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        backend=RGBBackend('carrying_in_groceries',301,args.output,seed=0,record_video=True)
        from omnigibson.object_states import NextTo,OnTop,Open
        report['provenance']=backend.provenance();save()
        objects={obj.name:obj for obj in backend.env.scene.objects}
        fridge=objects['fridge_petcxr_0'];placed=[]
        floors=[obj for obj in objects.values() if 'floor' in str(getattr(obj,'category','')).lower()]
        # Private setup follows the historical GROC-003 mechanism probe. Food
        # removal does not count as policy discovery, unloading, or task progress.
        for i,name in enumerate(('carton_of_milk_79','beefsteak_tomato_80','swiss_cheese_84',
                                 'carton_of_milk_83','butter_cookie_85','butter_cookie_86')):
            obj=objects[name];pos,quat=obj.get_position_orientation();pos=pos.clone();pos[0]-=2+i*.3
            obj.set_position_orientation(pos,quat);obj.keep_still()
        pos,quat=backend.robot.get_position_orientation();pos=pos.clone()
        pos[:2]=backend.torch.tensor([6.45,.85],device=pos.device)
        backend.robot.set_position_orientation(pos,quat);backend.robot.keep_still()
        point=backend.torch.tensor([4.7271428108,-.4277050197,.9704863429],device=pos.device)
        def check(label):
            row={'label':label,'env_step':backend.steps,'target_name':fridge.name,'target_open':bool(fridge.states[Open].get_value()) if Open in fridge.states else None,
                 'hand_empty':backend._get_held() is None,'bags':{}}
            for bag in placed:
                row['bags'][bag.name]={'next_to':bool(bag.states[NextTo].get_value(fridge)),
                    'floor_supported':any(bool(bag.states[OnTop].get_value(floor)) for floor in floors),
                    'aabb':[corner.tolist() for corner in bag.aabb]}
            report['states'].append(row);save();backend.observe()
            if not row['hand_empty'] or not all(v['next_to'] and v['floor_supported'] for v in row['bags'].values()):
                raise RuntimeError('Relation retention failed: '+label)
        if args.mode=='targets':
            # Same-scene target-shape regression, not a transfer task result.
            # Reuse one emptied bag; no simulator checkpoint equivalence claim.
            for target_name in ('breakfast_table_xftrki_0','straight_chair_uofiqj_0'):
                fridge=objects[target_name];placed=[]
                bag=objects['paper_bag_81'];grasp=backend._ideal_grasp(bag,100)
                if grasp.get('contained_rigid_objects')!=0:raise RuntimeError('Invalid setup: payload remains')
                lo,hi=fridge.aabb;selected=(lo+hi)/2
                backend._checked_place_next_to(fridge,300,selected);placed.append(bag)
                check(target_name+':placed')
                for _ in range(100):backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
                check(target_name+':after_100_steps')
        else:
            for name in ('paper_bag_81','paper_bag_82'):
                backend._ideal_state_action('open',fridge,100)
                bag=objects[name];grasp=backend._ideal_grasp(bag,100)
                if grasp.get('contained_rigid_objects')!=0:raise RuntimeError('Invalid setup: payload remains')
                backend._checked_place_next_to(fridge,300,point);placed.append(bag)
                check(name+':placed_open')
                for primitive in ('close','open','close'):
                    backend._ideal_state_action(primitive,fridge,100)
                    check(name+':'+primitive+':'+str(len(report['states'])))
        report['status']='passed'
    except Exception as exc:
        report.update(status='failed',error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
    finally:
        if backend is not None:
            try:report['video']=backend.finalize_video()
            except Exception as exc:report['video_error']=str(exc);report['status']='failed'
        save()
        if backend is not None:backend.close()
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
