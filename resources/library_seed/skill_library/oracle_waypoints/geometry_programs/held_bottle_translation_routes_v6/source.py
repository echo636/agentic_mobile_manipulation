"""Model-authored held-object alignment; mesh cross-sections and measured rigid hand/object offset. No grasp sampler is reauthored."""
import numpy as np
def rot(q):
 x,y,z,w=np.array(q)/np.linalg.norm(q)
 return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
def quat(r):
 k=np.array([[r[0,0]-r[1,1]-r[2,2],r[1,0]+r[0,1],r[2,0]+r[0,2],r[2,1]-r[1,2]],[r[1,0]+r[0,1],r[1,1]-r[0,0]-r[2,2],r[2,1]+r[1,2],r[0,2]-r[2,0]],[r[2,0]+r[0,2],r[2,1]+r[1,2],r[2,2]-r[0,0]-r[1,1],r[1,0]-r[0,1]],[r[2,1]-r[1,2],r[0,2]-r[2,0],r[1,0]-r[0,1],np.trace(r)]])/3
 _,v=np.linalg.eigh(k);return v[:,-1].tolist()
def aa(axis,a):
 axis=np.asarray(axis,dtype=float);axis/=np.linalg.norm(axis)
 k=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
 return np.eye(3)+np.sin(a)*k+(1-np.cos(a))*(k@k)
def section_radius(v,f,z,c):
 ds=[]
 for tri in v[f]:
  hits=[]
  for a,b in ((tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])):
   if (a[2]-z)*(b[2]-z)<0:
    hits.append((a+(b-a)*(z-a[2])/(b[2]-a[2]))[:2])
  if len(hits)==2:
   a,b=hits;d=b-a;t=np.clip(np.dot(c-a,d)/max(np.dot(d,d),1e-15),0,1)
   ds.append(np.linalg.norm(a+t*d-c))
 if not ds:raise ValueError("No closed rim section")
 return min(ds)
def mouth_targets(task,oracle,parameters):
 arm=parameters['arm']; hand=oracle['robot']['arms'][arm]
 obj=next(o for o in oracle['objects'] if o['id']==parameters['object_id'])
 bucket=next(o for o in oracle['objects'] if o['id']==parameters['container_id'])
 if hand['held_object']!=obj['id']:raise ValueError("Required retained object absent")
 hp=np.array(hand['eef_pose']['position_m']);hr=rot(hand['eef_pose']['quaternion_xyzw'])
 op=np.array(obj['pose']['position_m']); bp=np.array(bucket['pose']['position_m'])
 pts=np.array(obj['collision_mesh']['vertices_m']);bv=np.array(bucket['collision_mesh']['vertices_m']);bf=np.array(bucket['collision_mesh']['faces'])
 rim=bv[:,2].max(); radius=min(section_radius(bv,bf,rim-h,bp[:2]) for h in (.012,.03,.06))
 toward=op[:2]-bp[:2];toward/=np.linalg.norm(toward)
 local=pts-op;delta=op-hp;out=[]
 for yi,yaw in enumerate((0,30,-30,60,-60,90,-90,120,-120,180)):
  for ti,(axis,ang) in enumerate((((1,0,0),0),((1,0,0),20),((1,0,0),-20),((0,1,0),20),((0,1,0),-20))):
   d=aa((0,0,1),np.deg2rad(yaw))@aa(axis,np.deg2rad(ang))
   pp=local@d.T; rad=np.linalg.norm(pp[:,:2],axis=1).max()
   free=radius-rad-.035
   if free<=0:continue
   for oi,fraction in enumerate((0.,.75)):
    target=np.r_[bp[:2]+toward*free*fraction, max(pts[:,2].min(),rim+.16)-pp[:,2].min()]
    goal=target-d@delta;R=d@hr
    name='mouth_y%d_t%d_o%d'%(yi,ti,oi)
    angle=np.arccos(np.clip((np.trace(d)-1)/2,-1,1))
    score=float(np.linalg.norm(goal-hp)+.055*angle+.04*fraction)
    phase=dict(name=name+'_hold',arm=arm,frame='robot_base',goal=dict(position_m=goal.tolist(),quaternion_xyzw=quat(R)),gripper='hold',contact_target=None)
    out.append(dict(name=name,score=score,phases=[phase]))
 return dict(candidates=sorted(out,key=lambda c:c['score']),prepare_gripper=None)

def slerp(q0,q1,t):
 q0=np.asarray(q0,dtype=float);q0/=np.linalg.norm(q0)
 q1=np.asarray(q1,dtype=float);q1/=np.linalg.norm(q1)
 dot=np.dot(q0,q1)
 if dot<0:q1=-q1;dot=-dot
 if dot>.9995:
  q=q0+t*(q1-q0);return q/np.linalg.norm(q)
 a=np.arccos(np.clip(dot,-1,1))
 return (np.sin((1-t)*a)*q0+np.sin(t*a)*q1)/np.sin(a)

def generate(task,oracle,parameters):
 arm=parameters['arm'];h=oracle['robot']['arms'][arm]
 hp=np.array(h['eef_pose']['position_m']);q=h['eef_pose']['quaternion_xyzw']
 targets=mouth_targets(task,oracle,parameters)['candidates']
 target=next(c for c in targets if c['name']=='mouth_y0_t0_o1')['phases'][0]['goal']
 end=np.array(target['position_m']);out=[]
 for idx,style in enumerate(('straight','x_then_y','y_then_x','raised','lowered')):
  phases=[]
  for i in range(1,17):
   t=i/16;p=hp+t*(end-hp)
   if style=='x_then_y':
    p[0]=hp[0]+min(2*t,1)*(end[0]-hp[0]);p[1]=hp[1]+max(2*t-1,0)*(end[1]-hp[1])
   if style=='y_then_x':
    p[1]=hp[1]+min(2*t,1)*(end[1]-hp[1]);p[0]=hp[0]+max(2*t-1,0)*(end[0]-hp[0])
   if style=='raised':p[2]+=.10*np.sin(np.pi*t)
   if style=='lowered':p[2]-=.08*np.sin(np.pi*t)
   phases.append(dict(name=style+'_%02d'%i,arm=arm,frame='robot_base',goal=dict(position_m=p.tolist(),quaternion_xyzw=q),gripper='hold',contact_target=None))
  out.append(dict(name=style,score=float(idx),phases=phases))
 return dict(candidates=out,prepare_gripper=None)
