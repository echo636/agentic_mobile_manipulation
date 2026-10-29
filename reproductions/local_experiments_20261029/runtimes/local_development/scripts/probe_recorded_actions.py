"""Scripted regression replay of recorded RGB selections; not an autonomous evaluation."""
import argparse,copy,json,sys,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder,write_json
from manipulation_agent.contracts import SkillError

p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--events',type=Path,required=True);p.add_argument('--limit',type=int,default=12)
a=p.parse_args();r=Recorder(a.output,{'task':a.task,'instance':301,'seed':0,'policy':'scripted_recorded_actions',
 'source_events':str(a.events),'validation_level':'real_simulator_executor_regression_not_model_test','not_a_benchmark_attempt':True})
b=None
try:
 from manipulation_agent.executors.omnigibson_rgb import RGBBackend
 b=RGBBackend(a.task,301,a.output,seed=0,record_video=True,inside_placement='official_volume')
 r.run['backend']=b.provenance();write_json(a.output/'run.json',r.run)
 events=[json.loads(l) for l in a.events.read_text().splitlines()]
 calls=[e for e in events if e['kind']=='tool_call' and e['name'] in ['act','look']][:a.limit]
 results=[]
 for i,c in enumerate(calls):
  args=copy.deepcopy(c['arguments']);obs=b.observe();target=args.get('target')
  if target:
   view=target['image_ref'].rsplit('-',1)[-1]
   target['image_ref']=next(v['image_ref'] for v in obs['images'] if v['view']==view)
  primitive=args.get('primitive','look');request='regression-'+str(i)
  b.mark_video_tool('act',args,request)
  r.event('diagnostic_call',{'original_event':c['id'],'arguments':args})
  try:
   if target:
    obj,point,ground=b._ground(target)
    r.event('diagnostic_grounding',{'name':obj.name if obj else None,'point':point.tolist(),'grounding':ground})
   details=b.execute_visual(primitive,target,700,**({'yaw_degrees':args['yaw_degrees']} if primitive=='look' else {}))
   result={'primitive':primitive,'status':'passed','details':details}
  except SkillError as exc:
   result={'primitive':primitive,'status':'failed','code':exc.code,'error':str(exc)}
  result['robot_pose_finite']=bool(b.torch.isfinite(b.robot.get_position_orientation()[0]).all() and b.torch.isfinite(b.robot.get_position_orientation()[1]).all())
  r.event('diagnostic_result',result);results.append(result);print(json.dumps(result),flush=True)
 validation={'status':'passed' if all(x['robot_pose_finite'] for x in results) else 'failed','level':'scripted_action_sequence_completed',
  'model_used':False,'task_success_assessed':False,'action_results':results,'all_actions_succeeded':all(x['status']=='passed' for x in results)}
 write_json(a.output/'validation.json',validation)
 r.finish({'status':validation['status'],'validation':validation,'task_success':None,'sim_steps':b.steps,'video':b.finalize_video()})
except Exception as exc:
 (a.output/'traceback.txt').write_text(traceback.format_exc());write_json(a.output/'validation.json',{'status':'failed','error':str(exc),'level':'scripted_executor_regression'})
 r.finish({'status':'failed','failure':str(exc),'task_success':None});raise
finally:
 if b is not None:b.close()
