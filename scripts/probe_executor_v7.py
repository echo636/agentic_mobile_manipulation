"""Scripted motor regression with explicit private reprojection, never a model test.

Reuses selected object identities / points from an archived action trace. Pixels
are reprojected into fresh RGB when a repaired approach changes the camera pose.
No component success is reported as autonomous task success.
"""
import argparse,copy,json,sys,time,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder,write_json
from manipulation_agent.contracts import SkillError

p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--events',type=Path,required=True);p.add_argument('--limit',type=int,default=12);a=p.parse_args()
r=Recorder(a.output,{'task':a.task,'instance':301,'seed':0,'policy':'scripted_private_reprojection',
 'source_events':str(a.events),'validation_level':'real_motor_regression_with_private_object_selection',
 'model_used':False,'not_a_benchmark_attempt':True});backend=None
try:
 from manipulation_agent.executors.omnigibson_rgb import RGBBackend
 backend=RGBBackend(a.task,301,a.output,seed=0,record_video=True,inside_placement='official_volume');b=backend
 r.run['backend']=b.provenance();write_json(a.output/'run.json',r.run)
 events=[json.loads(l) for l in a.events.read_text().splitlines()];calls=[];latest=None;last_nav=None
 for e in events:
  if e['kind']=='tool_call' and e['name'] in ['act','look']:
   latest={**e,'grounding':None};calls.append(latest)
  elif e['kind']=='private_executor_result' and latest:
   latest['grounding']=e.get('details',{}).get('private_grounding')
 results=[]
 def name_for(path):return next((o.name for o in b.env.scene.objects if path==o.prim_path or path.startswith(o.prim_path+'/')),None)
 def choose(expected,world_point,original):
  from omnigibson.utils import transform_utils as T
  import cv2,numpy as np
  obj=b.env.scene.object_registry('name',expected)
  if obj is None:raise SkillError('invalid_visual_target','Recorded object not loaded in this task instance')
  # Prefer the original selected physical point, preserving shelf / floor intent.
  if world_point is not None:
   point=b.torch.tensor(world_point)
   for ref,frame in b.current_frames.items():
    local=T.quat2mat(frame['orientation'].cpu()).T@(point-frame['position'].cpu());depth=-float(local[2]);K=frame['intrinsic'].cpu()
    if depth<=.02:continue
    x=(float(local[0])/depth*float(K[0,0])+float(K[0,2]))/(frame['width']-1)
    y=(-float(local[1])/depth*float(K[1,1])+float(K[1,2]))/(frame['height']-1)
    if not(0<=x<=1 and 0<=y<=1):continue
    candidate={'image_ref':ref,'point':[x,y]}
    try:owner,hit,_=b._ground(candidate)
    except SkillError:continue
    if owner is obj and float(b.torch.linalg.norm(hit.cpu()-point))<.15:return candidate,'reprojected_recorded_world_point'
  candidates=[]
  for ref,frame in b.current_frames.items():
   ids=[int(i) for i,path in frame['instance_paths'].items() if path==obj.prim_path or path.startswith(obj.prim_path+'/')]
   if not ids:continue
   mask=np.isin(frame['seg_instance_id'].numpy(),ids).astype('uint8');distance=cv2.distanceTransform(mask,cv2.DIST_L2,3)
   py,px=np.unravel_index(distance.argmax(),distance.shape)
   if distance[py,px]>0:candidates.append((float(distance[py,px]),ref,int(px),int(py),frame))
  if not candidates:raise SkillError('invalid_visual_target','Recorded object is not currently visible; diagnostic will not teleport or reveal it')
  _,ref,px,py,frame=max(candidates,key=lambda x:x[0]);return {'image_ref':ref,'point':[px/(frame['width']-1),py/(frame['height']-1)]},'private_visible_instance_interior'
 for i,c in enumerate(calls[:a.limit]):
  args=copy.deepcopy(c['arguments']);primitive=args.get('primitive','look');obs=b.observe();target=args.get('target');ground=c.get('grounding') or {};expected=name_for(ground.get('rigid_body',''))
  if primitive=='grasp' and expected is None:expected=last_nav
  if primitive=='navigate_to':last_nav=expected
  start=time.monotonic();method='recorded_pixel';result={'primitive':primitive,'original_event':c['id'],'expected_object_private':expected}
  try:
   if target:
    view=target['image_ref'].rsplit('-',1)[-1];target['image_ref']=next(f['image_ref'] for f in obs['images'] if f['view']==view)
    if expected:target,method=choose(expected,ground.get('hit_position'),target);args['target']=target
    owner,point,resolved=b._ground(target);result.update(actual_object_private=owner.name if owner else None)
   b.mark_video_tool(c['name'],args,'regression-'+str(i));r.event('diagnostic_call',{'arguments':args,'selection_method':method,'expected_object_private':expected})
   details=b.execute_visual(primitive,target,700,**({'yaw_degrees':args['yaw_degrees']} if primitive=='look' else {}))
   result.update(status='passed',details=details)
  except SkillError as exc:result.update(status='failed',code=exc.code,error=str(exc))
  result.update(wall_seconds=time.monotonic()-start,robot_pose_finite=bool(all(b.torch.isfinite(v).all() for v in b.robot.get_position_orientation())))
  held=b._get_held();result['held_pose_finite']=held is None or bool(all(b.torch.isfinite(v).all() for v in held.get_position_orientation()))
  results.append(result);r.event('diagnostic_result',result);print(json.dumps(result),flush=True)
  write_json(a.output/'validation_progress.json',{'status':'running','results':results})
 validation={'status':'passed' if all(x['status']=='passed' and x['robot_pose_finite'] and x['held_pose_finite'] for x in results) else 'failed',
             'level':'scripted_motor_sequence_not_model_benchmark','model_used':False,'task_success_assessed':False,'action_results':results,
             'all_poses_finite':all(x['robot_pose_finite'] and x['held_pose_finite'] for x in results)}
 write_json(a.output/'validation.json',validation);r.finish({'status':validation['status'],'validation':validation,'task_success':None,'sim_steps':b.steps,'video':b.finalize_video()})
except Exception as exc:
 (a.output/'traceback.txt').write_text(traceback.format_exc());write_json(a.output/'validation.json',{'status':'failed','error':str(exc),'level':'scripted_motor_regression'});r.finish({'status':'failed','failure':str(exc),'task_success':None});raise
finally:
 if backend is not None:backend.close()
