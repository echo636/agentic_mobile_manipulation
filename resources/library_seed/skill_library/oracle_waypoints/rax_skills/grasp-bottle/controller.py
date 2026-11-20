import math
import json

class Stop(Exception):
    pass

def rot(q, v):
    x,y,z,w=q
    uv=[y*v[2]-z*v[1], z*v[0]-x*v[2], x*v[1]-y*v[0]]
    uu=[y*uv[2]-z*uv[1], z*uv[0]-x*uv[2], x*uv[1]-y*uv[0]]
    return [v[i]+2*(w*uv[i]+uu[i]) for i in range(3)]

def add(a,b): return [x+y for x,y in zip(a,b)]
def sub(a,b): return [x-y for x,y in zip(a,b)]
def norm(v): return math.sqrt(sum(x*x for x in v))
def world(base,p): return add(base["position_m"],rot(base["quaternion_xyzw"],p))
def wp(p,q): return dict(position_m=p,quaternion_xyzw=q,gripper="hold")

class Robot:
    def __init__(self,ctx):
        self.ctx=ctx; self.obs=None; self.phase="initial"; self.target=None; self.metrics={}
    def call(self,name,**kw):
        try:
            r=self.ctx.call(name,**kw)
        except Exception as e:
            try: r=json.loads(str(e))
            except Exception: raise Stop(name+": "+type(e).__name__)
        if isinstance(r,str): r=json.loads(r)
        if "content" in r:
            r=json.loads(next(c["text"] for c in r["content"] if c["type"]=="text"))
        if "observation" in r: self.obs=r["observation"]
        if not r.get("ok",False):
            ex=r.get("execution") or {}
            raise Stop(name+": "+str(r.get("error"))+" phase="+str(ex.get("phase"))+" reached="+str(ex.get("reached")))
        return r
    def geom(self,ids,sections=()):
        return self.call("get_oracle_geometry",object_ids=ids,
            object_fields=["pose","aabb","links","fixed_base","task_names"],
            robot_sections=list(sections))
    def move(self,group,joints,g):
        current=g["robot"]["trunk" if group=="trunk" else "arms"][group[4:]]["joint_positions_rad"] if group!="trunk" else g["robot"]["trunk"]["joint_positions_rad"]
        if max(abs(a-b) for a,b in zip(current,joints))<0.01: return
        limits=g["robot"]["trunk"]["joint_limits_rad"] if group=="trunk" else g["robot"]["arms"][group[4:]]["joint_limits_rad"]
        if any(not lo+0.02<=v<=hi-0.02 for v,lo,hi in zip(joints,limits["lower"],limits["upper"])): raise Stop("joint limit")
        r=self.call("move_joints",group=group,positions_rad=joints)
        if not r["execution"].get("reached") or r["execution"].get("max_joint_error_rad",1)>0.03: raise Stop("joint reach")
    def refs(self):
        return [i["image_ref"] for i in self.obs.get("images",[]) if i["view"] in ("front","right_wrist","left_wrist")]
    def compact(self):
        if not self.obs: return {}
        o=self.obs["oracle_3d"]
        return dict(revision=self.obs["revision"],capture=self.obs["capture"]["capture_id"],
             refs=self.refs(),hands={a:v["held_object"] for a,v in o["robot"]["arms"].items()},
             target=next((x["pose"]["position_m"] for x in o["objects"] if x["id"]==self.target),None))
    def log(self,msg): print(json.dumps(dict(phase=self.phase,message=msg,**self.compact())))

def find(catalog,name):
    ids=[o["id"] for o in catalog if name in o.get("task_names",[])]
    if len(ids)!=1: raise Stop("ambiguous binding "+name)
    return ids[0]

def perceive(r):
    r.call("observe_scene")
    cat=r.obs["oracle_3d"].get("object_catalog",r.obs["oracle_3d"]["objects"])
    r.target=find(cat,"wine_bottle.n.01_1")
    counter=find(cat,"countertop.n.01_1")
    r.call("set_oracle_focus",object_ids=[r.target,counter])
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras","grasping_mode"])
    if any(a["held_object"] is not None for a in g["robot"]["arms"].values()): raise Stop("full pickup requires empty hands")
    if g["robot"]["grasping_mode"]!="assisted": raise Stop("wrong grasp mode")
    return counter,g

def project(obs,p):
    # Surround camera calibration inferred from multiple current-session
    # pixel/depth-grounded navigation observations: 90deg HFOV, 35deg down,
    # 0.35m radial offset, 1.745723m camera height above the standing base.
    # Recompute relative height from the current base; never reuse scene coordinates.
    base=obs["oracle_3d"]["robot"]["base_pose_world"]
    height=1.745723
    angle=math.atan2(p[1],p[0])
    views=[("front",0),("left",math.pi/2),("back",math.pi),("right",-math.pi/2)]
    name,yaw=min(views,key=lambda v:abs(math.atan2(math.sin(angle-v[1]),math.cos(angle-v[1]))))
    f=math.cos(yaw)*p[0]+math.sin(yaw)*p[1]-0.35
    side=math.sin(yaw)*p[0]-math.cos(yaw)*p[1]
    dz=p[2]-height; c=math.cos(math.radians(35)); s=math.sin(math.radians(35))
    d=c*f-s*dz; up=s*f+c*dz
    if d<=0: raise Stop("camera projection behind")
    uv=[0.5+0.5*side/d,0.5-0.5*up/d]
    if any(x<0.015 or x>0.985 for x in uv): raise Stop("target outside surround view")
    return dict(image_ref=next(i["image_ref"] for i in obs["images"] if i["view"]==name),point=uv)

def navigate(r,counter,g):
    r.phase="empty_robot_ideal_navigation"
    for k in range(8):
        p=g["objects"][0]["pose"]["position_m"]
        distance=math.hypot(p[0],p[1])
        if distance<=1.36: return g
        # Pixel is grounded in current geometry; an occluder can become an
        # intermediate approach. Require measurable target-distance progress.
        selection=project(r.obs,p)
        r.call("navigate_to",target=selection)
        ng=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras","grasping_mode"])
        if any(a["held_object"] for a in ng["robot"]["arms"].values()): raise Stop("held navigation")
        np=ng["objects"][0]["pose"]["position_m"]
        if math.hypot(np[0],np[1])>distance-0.025: raise Stop("navigation made insufficient progress")
        g=ng
    raise Stop("navigation iteration bound")

def verify(r,g,initial_z,initial_h,reference=None):
    rb=g["robot"]; arm=rb["arms"]["right"]; other=rb["arms"]["left"]
    ng=arm["native_grasp"]
    if arm["held_object"]!=r.target or ng["object_id"]!=r.target or ng["target_link"]!="base_link" or not ng["constraint_type"] or other["held_object"] is not None:
        raise Stop("ambiguous native one-hand retention")
    obj=g["objects"][0]["pose"]["position_m"]
    lift=world(rb["base_pose_world"],obj)[2]-initial_z
    if lift<0.05: raise Stop("lift below 0.05m")
    posture=rb["trunk"]["body_posture"]
    if posture["upper_body_tilt_deg"]>5 or posture["upper_body_height_above_base_m"]<initial_h-0.08: raise Stop("not standing upright")
    eef=arm["eef_pose"]; q=eef["quaternion_xyzw"]
    rel=rot([-q[0],-q[1],-q[2],q[3]],sub(obj,eef["position_m"]))
    drift=0 if reference is None else norm(sub(rel,reference))
    if drift>0.03: raise Stop("object-to-EEF drift")
    return rel,lift,posture["upper_body_tilt_deg"],posture["upper_body_height_above_base_m"],drift

def task(r):
    counter,g=perceive(r)
    initial_z=world(g["robot"]["base_pose_world"],g["objects"][0]["pose"]["position_m"])[2]
    initial_h=g["robot"]["trunk"]["body_posture"]["upper_body_height_above_base_m"]
    standing=list(g["robot"]["trunk"]["joint_positions_rad"])
    g=navigate(r,counter,g)
    r.phase="prepare_arms"
    # Tuck left shoulder rearward while bending elbow so its fingers clear
    # the near counter edge. Bending that elbow alone hit the counter.
    r.move("arm_left",[1.2,0,0.7,-2,0.85,-0.6,-0.85],g)
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras"])
    r.move("arm_right",[0,0,0,-1.5,0,0,0],g)
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras"])
    if any(a["contacts"]["finger_count"] for a in g["robot"]["arms"].values()): raise Stop("unexpected preparation finger contact")
    cat=r.obs["oracle_3d"].get("object_catalog",r.obs["oracle_3d"]["objects"])
    obstacle_ids=[x["id"] for x in cat if x["category"] in ("bar","top_cabinet","oven","walls","range_hood","drop_in_sink","fridge","microwave") and x["id"]!=counter]
    obstacles=[]
    for i in range(0,len(obstacle_ids),8):
        obstacles+=r.geom(obstacle_ids[i:i+8])["objects"]
    target=g["objects"][0]; p=list(target["aabb"]["center_m"])
    p[2]=target["aabb"]["max_m"][2]-0.065
    yaw=math.atan2(p[1]+0.252,p[0]+0.079)
    q=[-math.sin(yaw/2)/math.sqrt(2),math.cos(yaw/2)/math.sqrt(2),math.sin(yaw/2)/math.sqrt(2),math.cos(yaw/2)/math.sqrt(2)]
    fingers=g["robot"]["arms"]["right"]["finger_geometry"]
    off=[sum(f["aabb_in_eef"]["center_m"][i] for f in fingers)/len(fingers) for i in range(3)]
    contact=sub(p,rot(q,off))
    direction=[math.cos(yaw),math.sin(yaw),0]
    pre=sub(contact,[0.10*x for x in direction])
    lift=add(contact,[0,0,0.10])
    # Counter and overhead bounds for the hand/bottle corridor.
    if p[2]-0.055<g["objects"][1]["aabb"]["max_m"][2]: raise Stop("hand-counter clearance")
    for ob in obstacles:
        b=ob["aabb"]
        if b["min_m"][2]>p[2] and b["min_m"][2]<lift[2]+0.13 and b["min_m"][0]<contact[0]<b["max_m"][0] and b["min_m"][1]<contact[1]<b["max_m"][1]: raise Stop("overhead clearance")
    r.phase="native_assisted_grasp"
    r.log("begin")
    result=r.call("grasp",arm="right",frame="robot_base",allow_trunk=True,target_object_id=r.target,target_link="base_link",grasp_kind="free_object",
       pregrasp_waypoints=[wp(pre,q)],approach_waypoints=[wp(contact,q)],verification_waypoints=[wp(lift,q)],min_lift_m=0.05,settle_steps=15)
    ex=result.get("execution") or {}
    if not ex.get("verified") or not ex.get("retention",{}).get("retained"): raise Stop("native grasp verification")
    print(json.dumps({"phase":r.phase,"verified":ex.get("verified"),"lifted_m":ex.get("lifted_m")}))
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links"])
    a=g["robot"]["arms"]["right"]
    if a["held_object"]!=r.target or a["native_grasp"]["object_id"]!=r.target: raise Stop("grasp did not retain target")
    r.phase="retract_retained_bottle"
    e=a["eef_pose"]; retract=sub(e["position_m"],[0.25*x for x in direction])
    result=r.call("execute_waypoints",arm="right",frame="robot_base",waypoints=[wp(retract,e["quaternion_xyzw"])])
    if not result["execution"].get("reached"): raise Stop("retraction failed")
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links"])
    if g["robot"]["arms"]["right"]["held_object"]!=r.target: raise Stop("retention during retract")
    r.phase="restore_standing"
    r.move("trunk",standing,g)
    g=r.geom([r.target],["base_pose_world","arms","trunk"])
    ref,li,ti,he,dr=verify(r,g,initial_z,initial_h)
    minimum_lift=li; max_tilt=ti; minimum_height=he; max_drift=dr
    r.phase="steady_60_steps"
    for step in range(60):
        r.call("wait",steps=1)
        g=r.geom([r.target],["base_pose_world","arms","trunk"])
        _,li,ti,he,dr=verify(r,g,initial_z,initial_h,ref)
        minimum_lift=min(minimum_lift,li); max_tilt=max(max_tilt,ti)
        minimum_height=min(minimum_height,he); max_drift=max(max_drift,dr)
    r.metrics=dict(steady_steps=60,min_lift_m=minimum_lift,max_tilt_deg=max_tilt,min_height_m=minimum_height,initial_height_m=initial_h,max_drift_m=max_drift)
    return dict(ok=True,metrics=r.metrics,**r.compact())

def main(ctx):
    r=Robot(ctx)
    try: return task(r)
    except Exception as e:
        return dict(ok=False,phase=r.phase,error=str(e),metrics=r.metrics,**r.compact())
