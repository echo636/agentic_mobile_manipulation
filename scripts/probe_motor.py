"""Scripted real-simulator motor checks. Never a model or benchmark success claim."""
import argparse
from pathlib import Path
import traceback
from manipulation_agent.executors.motor import MotorBackend
from manipulation_agent.records import Recorder,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--task',default='turning_on_radio');a=p.parse_args()
    rec=Recorder(a.output,{'backend':'omnigibson','task':a.task,'instance':301,'seed':0,
        'observation_mode':'rgb_only','agent_profile':'motor','model_used':False,
        'validation_level':'scripted_real_physical_control_components'})
    b=None;checks=[]
    def check(name,value,**detail):
        row={'name':name,'status':'passed' if value else 'failed',**detail};checks.append(row)
        rec.event('motor_validation',row);write_json(a.output/'validation.json',{'status':'running','checks':checks})
        if not value:raise AssertionError(name)
    try:
        b=MotorBackend(a.task,301,a.output,seed=0,max_steps=400,record_video=True)
        rec.run['backend']=b.provenance();write_json(a.output/'run.json',rec.run)
        check('physical_controller_configuration',not hasattr(b,'primitives') and b.robot.grasping_mode=='physical')
        obs=b.observe();rec.event('diagnostic_observation',{'observation':obs})
        check('four_rgb_views',len(obs['images'])==4)
        before=b.steps
        for name,args in [('hold',dict(steps=3)),('base_velocity',dict(x=.1,y=0,yaw=0,steps=9)),
                          ('base_velocity',dict(x=0,y=0,yaw=.2,steps=9)),
                          ('joint_delta',dict(group='arm_right',delta=[0,0,0,.05,0,0,0],steps=9)),
                          ('gripper',dict(hand='right',opening=0,steps=12)),
                          ('gripper',dict(hand='right',opening=1,steps=12))]:
            marker=rec.event('diagnostic_call',{'primitive':name,'arguments':args})
            b.mark_video_tool(name,args,marker)
            old=b.robot.get_joint_positions().clone();pos=b.robot.get_position_orientation()[0].clone()
            result=b.execute_motor(name,args,100)
            rec.event('diagnostic_result',{'primitive':name,'result':result,'observation':b.observe()})
            check(name+'_command_steps',result['sim_steps']==args['steps']+(name=='base_velocity'))
            if name=='joint_delta':
                index=b.motor_joint_indices['arm_right'][3]
                measured=float(b.robot.get_joint_positions()[index]-old[index])
                check('arm_joint_physically_moved',measured>.005,requested_delta=.05,measured_delta=measured)
            if name=='base_velocity' and args['x']:
                distance=float(b.torch.linalg.norm(b.robot.get_position_orientation()[0]-pos))
                check('base_physically_moved',distance>.003,measured_distance_m=distance)
            if name=='gripper':
                index=b.motor_joint_indices['gripper_right']
                measured=float((b.robot.get_joint_positions()[index]-old[index]).mean())
                check('gripper_opened' if args['opening'] else 'gripper_closed',
                      measured>.001 if args['opening'] else measured<-.001,measured_joint_change=measured)
        check('all_steps_accounted',b.steps-before==56,steps=b.steps-before)
        validation={'status':'passed','checks':checks,'model_used':False,'task_success_assessed':False,
                    'verification_level':'scripted_real_physical_control_components'}
        write_json(a.output/'validation.json',validation)
        rec.finish({'status':'passed','task_success':None,'video':b.finalize_video(),'sim_steps':b.steps,'validation':validation})
    except Exception as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        validation={'status':'failed','checks':checks,'error_type':type(exc).__name__,
                    'model_used':False,'task_success_assessed':False}
        write_json(a.output/'validation.json',validation)
        rec.finish({'status':'failed','task_success':None,'validation':validation,'failure':str(exc),
                    'video':b.finalize_video() if b else None,'sim_steps':b.steps if b else 0})
        raise
    finally:
        if b is not None:b.close()


if __name__=='__main__':main()
