import math
import json

RECIPE = "countertop_pick"
DEFAULT_PARAMETERS = {"arm": "auto", "ready_posture": "countertop_ready",
                      "lift_m": 0.10, "hold_steps": 60}
DEFAULT_CONSTRAINTS = {"allow_base_motion": False, "allow_trunk_motion": True}


class EntityRef:
    """An episode-independent task binding, resolved from current Oracle geometry."""
    def __init__(self, *, task_name):
        if not isinstance(task_name, str) or not task_name.strip():
            raise ValueError("EntityRef.task_name must be a nonempty string")
        self.task_name = task_name


def _entity(value):
    if isinstance(value, EntityRef):
        return value.task_name
    if isinstance(value, dict) and set(value) == {"task_name"}:
        return EntityRef(**value).task_name
    raise ValueError("Use EntityRef(task_name=...) or {'task_name': ...}")


def _options(recipe, targets, parameters, constraints, revision):
    if recipe != RECIPE:
        raise ValueError("Unknown recipe: " + str(recipe))
    if not isinstance(targets, dict) or "object" not in targets or set(targets)-{"object", "support"}:
        raise ValueError("targets requires object; optional support is the countertop")
    names = {k: _entity(v) for k, v in targets.items()}
    names.setdefault("support", "countertop.n.01_1")
    if names["object"] == names["support"]:
        raise ValueError("object and support must differ")
    values = {}
    for label, supplied, defaults in (("parameters", parameters, DEFAULT_PARAMETERS),
                                      ("constraints", constraints, DEFAULT_CONSTRAINTS)):
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict) or set(supplied)-set(defaults):
            raise ValueError("Unknown or invalid " + label)
        values[label] = {**defaults, **supplied}
    p, c = values["parameters"], values["constraints"]
    if p["arm"] not in ("auto", "left", "right"):
        raise ValueError("arm must be auto, left or right")
    if p["ready_posture"] != "countertop_ready":
        raise ValueError("Unknown ready_posture")
    if type(p["lift_m"]) not in (int, float) or not math.isfinite(p["lift_m"]) or not .05 <= p["lift_m"] <= .15:
        raise ValueError("lift_m must be finite and within [0.05, 0.15] metres")
    if type(p["hold_steps"]) is not int or p["hold_steps"] < 60:
        raise ValueError("hold_steps must be an integer >= 60")
    if any(type(v) is not bool for v in c.values()):
        raise ValueError("Motion permissions must be booleans")
    if revision is not None and (type(revision) is not int or revision < 0):
        raise ValueError("revision must be a nonnegative integer or None")
    return names, p, c


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
    def __init__(self,ctx,targets,parameters,constraints,revision):
        self.ctx=ctx; self.obs=None; self.phase="initial"; self.target=None; self.metrics={}; self.arm=None; self.grasps=0; self.motors=0; self.failed_motors=0
        self.targets=targets; self.parameters=parameters; self.constraints=constraints; self.expected_revision=revision
    def call(self,name,**kw):
        if name=="navigate_to" and (not self.constraints["allow_base_motion"] or self.grasps):
            raise Stop("base motion forbidden by constraints or after grasp")
        motor=name in ("navigate_to","move_joints","execute_waypoints","grasp","wait","set_gripper","release")
        if name=="grasp":
            self.grasps+=1
            if self.grasps!=1: raise Stop("exactly one grasp allowed")
        if motor: self.motors+=1
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
            if motor: self.failed_motors+=1
            ex=r.get("execution") or {}
            self.metrics["failed_execution"]={k:ex[k] for k in ex if k not in ("observation","images")}
            raise Stop(name+": "+str(r.get("error"))+" phase="+str(ex.get("phase"))+" reached="+str(ex.get("reached")))
        return r
    def geom(self,ids,sections=()):
        return self.call("get_oracle_geometry",object_ids=ids,
            object_fields=["pose","aabb","links","fixed_base","task_names"],
            robot_sections=list(sections))
    def move(self,group,joints,g):
        current=g["robot"]["trunk" if group=="trunk" else "arms"][group[4:]]["joint_positions_rad"] if group!="trunk" else g["robot"]["trunk"]["joint_positions_rad"]
        if max(abs(a-b) for a,b in zip(current,joints))<0.01: return
        if group=="trunk" and not self.constraints["allow_trunk_motion"]:
            raise Stop("trunk motion forbidden by constraints")
        limits=g["robot"]["trunk"]["joint_limits_rad"] if group=="trunk" else g["robot"]["arms"][group[4:]]["joint_limits_rad"]
        if any(not lo+0.02<=v<=hi-0.02 for v,lo,hi in zip(joints,limits["lower"],limits["upper"])): raise Stop("joint limit")
        r=self.call("move_joints",group=group,positions_rad=joints)
        ex=r["execution"]
        if not ex.get("reached"): raise Stop("joint move did not reach")
        if group=="trunk":
            # Native move completes inside its reported joint tolerance; allow
            # the unchanged standing targets to settle, then apply our stricter
            # measured joint and body-posture checks.
            self.call("wait",steps=10)
            actual=self.geom([],["trunk"])["robot"]["trunk"]["joint_positions_rad"]
            error=max(abs(a-b) for a,b in zip(actual,joints))
        else:
            error=ex.get("max_joint_error_rad",1)
        if error>0.03: raise Stop("joint reach error="+str(error))
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
    if r.expected_revision is not None and r.obs["revision"]!=r.expected_revision:
        raise Stop("stale revision: refresh observations before Manip")
    cat=r.obs["oracle_3d"].get("object_catalog",r.obs["oracle_3d"]["objects"])
    r.target=find(cat,r.targets["object"])
    counter=find(cat,r.targets["support"])
    r.call("set_oracle_focus",object_ids=[r.target,counter])
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras","grasping_mode"])
    if any(a["held_object"] is not None for a in g["robot"]["arms"].values()): raise Stop("full pickup requires empty hands")
    if g["robot"]["grasping_mode"]!="assisted": raise Stop("wrong grasp mode")
    obj=g["objects"][0]["pose"]
    if rot(g["robot"]["base_pose_world"]["quaternion_xyzw"],rot(obj["quaternion_xyzw"],[0,0,1]))[2]<0.97:
        raise Stop("full pickup requires initially upright bottle")
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

def scene_obstacles(r):
    cat=r.obs["oracle_3d"].get("object_catalog",r.obs["oracle_3d"]["objects"])
    excluded=("floors","ceilings","roof","room_light","track_light","wall_nail","magazine","newspaper")
    ids=[o["id"] for o in cat if o.get("category") not in excluded and o["id"]!=r.target]
    obs=[]
    for i in range(0,len(ids),8):
        obs.extend(r.geom(ids[i:i+8])["objects"])
    return obs

def ray_box(origin,end,box):
    # A camera can lie inside a loose outer AABB (e.g. stairs) while seeing
    # exterior floor. Such a box is not a decisive occluder; candidate floor
    # goals still require full footprint clearance and native checked routing.
    if all(box["min_m"][i]<=origin[i]<=box["max_m"][i] for i in range(3)):
        return False
    lo=0.0; hi=1.0
    for i in range(3):
        d=end[i]-origin[i]
        if abs(d)<1e-9:
            if origin[i]<box["min_m"][i] or origin[i]>box["max_m"][i]: return False
        else:
            a=(box["min_m"][i]-origin[i])/d; b=(box["max_m"][i]-origin[i])/d
            lo=max(lo,min(a,b)); hi=min(hi,max(a,b))
            if lo>hi: return False
    return hi>0.01 and lo<0.99

def floor_stage(r,p,obstacles):
    # Use visible open floor within the current local Oracle scope before
    # selecting a distant elevated target through intervening furniture.
    heading=math.atan2(p[1],p[0]); dist=math.hypot(p[0],p[1])
    candidates=[]
    base=r.obs["oracle_3d"]["robot"]["base_pose_world"]
    for radius in (2.0,1.6,1.2):
        for deg in (0,20,-20,40,-40,60,-60,80,-80,100,-100):
            angle=heading+math.radians(deg)
            point=[radius*math.cos(angle),radius*math.sin(angle),-base["position_m"][2]+0.002]
            remaining=math.hypot(p[0]-point[0],p[1]-point[1])
            if remaining>dist-0.3: continue
            clear=True
            for ob in obstacles:
                b=ob["aabb"]
                if b["min_m"][2]>1.72 or b["max_m"][2]<0.08: continue
                dx=max(b["min_m"][0]-point[0],0,point[0]-b["max_m"][0])
                dy=max(b["min_m"][1]-point[1],0,point[1]-b["max_m"][1])
                if math.hypot(dx,dy)<0.62:
                    clear=False; break
            if not clear: continue
            try: pixel=project(r.obs,point)
            except Stop: continue
            name=next(im["view"] for im in r.obs["images"] if im["image_ref"]==pixel["image_ref"])
            yaw=dict(front=0,left=math.pi/2,back=math.pi,right=-math.pi/2)[name]
            origin=[0.35*math.cos(yaw),0.35*math.sin(yaw),1.745723]
            if any(ray_box(origin,point,ob["aabb"]) for ob in obstacles): continue
            candidates.append((remaining+0.002*abs(deg),pixel))
    if not candidates: return None
    return min(candidates,key=lambda v:v[0])[1]

def navigate(r,counter,g):
    r.phase="empty_robot_ideal_navigation"
    stowed=False
    for k in range(8):
        p=g["objects"][0]["pose"]["position_m"]
        distance=math.hypot(p[0],p[1])
        # Navigation preserves base yaw. A station is only provisional;
        # actual prepared shoulder reach is checked before the single grasp.
        if distance<=1.25: return g
        if not r.constraints["allow_base_motion"]:
            raise Stop("approach_required: move closer while empty, or explicitly allow_base_motion")
        if distance>2.25:
            selection=floor_stage(r,p,scene_obstacles(r))
            if selection is None:
                # Compact rooms may have no distant floor goal outside inflated
                # outer AABBs. Request the currently projected bottle approach
                # through the existing checked native navigation planner.
                selection=project(r.obs,p)
                r.log("no conservative floor stage; select visible bottle approach")
        else:
            selection=project(r.obs,p)
        if any(a["held_object"] is not None for a in g["robot"]["arms"].values()):
            raise Stop("empty hands required before ideal navigation")
        previous_base=g["robot"]["base_pose_world"]["position_m"]
        r.call("navigate_to",target=selection)
        ng=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras","grasping_mode"])
        if norm(sub(ng["robot"]["base_pose_world"]["position_m"],previous_base))<0.025:
            raise Stop("navigation did not displace base")
        if any(a["held_object"] for a in ng["robot"]["arms"].values()): raise Stop("held navigation")
        np=ng["objects"][0]["pose"]["position_m"]
        if math.hypot(np[0],np[1])>distance-0.025: raise Stop("navigation made insufficient progress")
        g=ng
        if distance>2.25 and not stowed:
            r.phase="prepare_empty_navigation_in_clear_floor"
            nearby=scene_obstacles(r)
            blockers=[]
            for ob in nearby:
                b=ob["aabb"]
                if all(b["min_m"][i]<hi and b["max_m"][i]>lo for i,(lo,hi) in enumerate(((-0.35,0.35),(-0.45,0.45),(0.60,1.55)))):
                    blockers.append(ob["id"])
            # Stowing is optional preparation at an intermediate floor stop.
            # If its swept corridor is not clear, keep the current arms still
            # and continue native checked empty navigation; never try that move.
            if not blockers:
                for side in ("left","right"):
                    sign=1 if side=="left" else -1
                    r.move("arm_"+side,[1.2,0,0.7*sign,-2,0.85*sign,-0.6*sign,-0.85*sign],g)
                    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras"])
                    if any(a["contacts"]["finger_count"] for a in g["robot"]["arms"].values()): raise Stop("navigation preparation contact")
                stowed=True
            else:
                r.log("defer optional intermediate arm stow: "+",".join(blockers[:4]))
            r.phase="empty_robot_ideal_navigation"
    raise Stop("navigation iteration bound")

def verify(r,g,initial_z,initial_h,reference=None):
    rb=g["robot"]; arm=rb["arms"][r.arm]; other=rb["arms"]["left" if r.arm=="right" else "right"]
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

def record_baseline(r,g):
    # Episode-local initial baselines survive development continuation, while
    # each independent episode records its own geometry before any motor call.
    notes=r.call("task_notes",operation="read",content=None,expected_version=None)["notes"]
    if notes["content"]:
        baseline=json.loads(notes["content"])
        if baseline.get("schema")=="bottle_initial_v1":
            if r.targets!={"object":"wine_bottle.n.01_1","support":"countertop.n.01_1"}:
                raise Stop("legacy baseline belongs to a different target binding")
        elif baseline.get("schema")!="manip_countertop_pick_v1" or baseline.get("targets")!=r.targets:
            raise Stop("episode baseline belongs to a different recipe or target binding")
    else:
        baseline=dict(schema="manip_countertop_pick_v1",targets=r.targets,
            initial_z=world(g["robot"]["base_pose_world"],g["objects"][0]["pose"]["position_m"])[2],
            initial_h=g["robot"]["trunk"]["body_posture"]["upper_body_height_above_base_m"],
            capture=g["capture"]["capture_id"],sim_step=g["capture"]["sim_step"])
        r.call("task_notes",operation="write",content=json.dumps(baseline),expected_version=notes["version"])
    initial_z=baseline["initial_z"]; initial_h=baseline["initial_h"]
    r.metrics.update(initial_capture=baseline["capture"],initial_step=baseline["sim_step"])
    return initial_z,initial_h


def task(r):
    counter,g=perceive(r)
    initial_z,initial_h=record_baseline(r,g)
    g=navigate(r,counter,g)
    r.phase="prepare_arms"
    p=g["objects"][0]["aabb"]["center_m"]
    bearing=math.atan2(p[1],p[0])
    # A frontal shoulder is laterally offset from the bottle. At long
    # downward reaches it can saturate the inward shoulder joint even when
    # scalar reach is below 0.82m. Turn the nearer shoulder toward the target
    # whenever the neutral frontal contact would need more than 0.70m.
    torso=g["robot"]["trunk"]["body_posture"]
    shoulder_height=torso["upper_body_height_above_base_m"]+0.30
    frontal_reach=math.sqrt(max(0,math.hypot(p[0],p[1])-0.10)**2+0.236**2+
                            (shoulder_height-(g["objects"][0]["aabb"]["max_m"][2]-0.04))**2)
    side_reach=frontal_reach>0.70
    if side_reach:
        # A full lateral turn at a short station over-folds the elbow
        # during the elevated pregrasp. Partial yaw keeps inward shoulder
        # motion away from its limit while retaining elbow extension.
        lateral_angle=math.pi/2 if math.hypot(p[0],p[1])>0.90 else math.pi/4
        choices=[math.atan2(math.sin(bearing+d),math.cos(bearing+d)) for d in (-lateral_angle,lateral_angle)]
        choices=[a for a in choices if abs(a)<2.95]
        if not choices: raise Stop("no side-facing trunk yaw")
        facing=min(choices,key=abs)
    else:
        facing=bearing
    if abs(facing)>2.95: raise Stop("target bearing outside conservative trunk yaw")
    reach_pitch=(0.40 if math.hypot(p[0],p[1])>0.90 else 0.25)*(1 if p[0]>=0 else -1)
    trunk_target=[reach_pitch,0,reach_pitch,facing]
    if not r.constraints["allow_trunk_motion"] and max(abs(a-b) for a,b in zip(
            g["robot"]["trunk"]["joint_positions_rad"],trunk_target))>=0.01:
        raise Stop("trunk preparation required but forbidden by constraints")
    # Navigation preserves base yaw; tuck both arms before changing upper-body
    # yaw and upright pitch. Recompute the selected shoulder after this motion.
    for side in ("left","right"):
        sign=1 if side=="left" else -1
        r.move("arm_"+side,[1.2,0,0.7*sign,-2,0.85*sign,-0.6*sign,-0.85*sign],g)
        g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras"])
    r.phase="prepare_upright_reach"
    # R1Pro torso axes +Y,+Y,-Y,+Z: q3=q1+q2 keeps torso_link4 upright.
    r.move("trunk",trunk_target,g)
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras"])
    prepared=g["robot"]["trunk"]["body_posture"]
    if prepared["upper_body_tilt_deg"]>2 or prepared["upper_body_height_above_base_m"]<initial_h-0.065:
        raise Stop("prepared trunk is not sufficiently upright/high")
    r.metrics.update(prepared_tilt_deg=prepared["upper_body_tilt_deg"],prepared_height_m=prepared["upper_body_height_above_base_m"])
    shoulder={side:next(b["aabb"]["center_m"] for b in g["robot"]["body_links"] if b["link"]==side+"_arm_link1") for side in ("left","right")}
    r.arm=(min(shoulder,key=lambda side:norm(sub(g["objects"][0]["aabb"]["center_m"],shoulder[side])))
           if r.parameters["arm"]=="auto" else r.parameters["arm"])
    r.metrics.update(reach_pitch_rad=reach_pitch,facing_yaw_rad=facing)
    # Keep a lateral approach below overhead cabinets. The stock lateral
    # ready pose can remain above our joint-error limit even after settling.
    ready=([0.30,1.25 if r.arm=="left" else -1.25,0,-1.57,1.57 if r.arm=="left" else -1.57,0,0]
           if side_reach and lateral_angle>math.pi/3 else [0,0,0,-1.5,0,0,0])
    r.move("arm_"+r.arm,ready,g)
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links","wrist_cameras"])
    if any(a["contacts"]["finger_count"] for a in g["robot"]["arms"].values()): raise Stop("unexpected preparation finger contact")
    cat=r.obs["oracle_3d"].get("object_catalog",r.obs["oracle_3d"]["objects"])
    obstacle_ids=[x["id"] for x in cat if x["category"] in ("bar","top_cabinet","oven","range_hood","drop_in_sink","fridge","microwave","dishwasher","wine_bottle","can_of_soda") and x["id"] not in (counter,r.target)]
    obstacles=[]
    for i in range(0,len(obstacle_ids),8):
        obstacles+=r.geom(obstacle_ids[i:i+8])["objects"]
    target=g["objects"][0]; p=list(target["aabb"]["center_m"])
    p[2]=target["aabb"]["max_m"][2]-0.040
    shoulder=next(b["aabb"]["center_m"] for b in g["robot"]["body_links"] if b["link"]==r.arm+"_arm_link1")
    yaw=math.atan2(p[1]-shoulder[1],p[0]-shoulder[0])
    q=[-math.sin(yaw/2)/math.sqrt(2),math.cos(yaw/2)/math.sqrt(2),math.sin(yaw/2)/math.sqrt(2),math.cos(yaw/2)/math.sqrt(2)]
    fingers=g["robot"]["arms"][r.arm]["finger_geometry"]
    # Select a contact depth within the common finger interval, toward its
    # distal half, rather than treating the center of inflated AABBs as exact.
    low=max(f["aabb_in_eef"]["min_m"][2] for f in fingers)
    high=min(f["aabb_in_eef"]["max_m"][2] for f in fingers)
    if high-low<0.05: raise Stop("ambiguous finger contact interval")
    off=[0,0,low+0.60*(high-low)]
    contact=sub(p,rot(q,off))
    direction=[math.cos(yaw),math.sin(yaw),0]
    pre=sub(contact,[0.12*x for x in direction])
    high_pre=add(pre,[0,0,0.10])
    near=sub(contact,[0.04*x for x in direction])
    lift=add(contact,[0,0,r.parameters["lift_m"]])
    r.metrics["shoulder_reach_m"]=norm(sub(contact,shoulder))
    if r.metrics["shoulder_reach_m"]>0.82: raise Stop("station outside conservative shoulder reach")
    if p[2]-0.055<g["objects"][1]["aabb"]["max_m"][2]: raise Stop("hand-counter clearance")
    for ob in obstacles:
        b=ob["aabb"]
        if b["min_m"][2]>p[2] and b["min_m"][2]<lift[2]+0.14 and b["min_m"][0]-0.08<contact[0]<b["max_m"][0]+0.08 and b["min_m"][1]-0.08<contact[1]<b["max_m"][1]+0.08: raise Stop("overhead clearance")
    r.metrics.update(arm=r.arm,station_distance_m=math.hypot(p[0],p[1]),finger_contact_depth_m=off[2],contact_height_above_counter_m=p[2]-g["objects"][1]["aabb"]["max_m"][2])
    r.phase="native_assisted_grasp"
    r.log("begin")
    result=r.call("grasp",arm=r.arm,frame="robot_base",allow_trunk=False,target_object_id=r.target,target_link="base_link",grasp_kind="free_object",
       pregrasp_waypoints=[wp(high_pre,q),wp(pre,q)],approach_waypoints=[wp(near,q),wp(contact,q)],verification_waypoints=[wp(lift,q)],min_lift_m=0.05,settle_steps=15)
    ex=result.get("execution") or {}
    if not ex.get("verified") or not ex.get("retention",{}).get("retained"): raise Stop("native grasp verification")
    print(json.dumps({"phase":r.phase,"verified":ex.get("verified"),"lifted_m":ex.get("lifted_m")}))
    g=r.geom([r.target,counter],["base_pose_world","arms","trunk","body_links"])
    a=g["robot"]["arms"][r.arm]
    if a["held_object"]!=r.target or a["native_grasp"]["object_id"]!=r.target: raise Stop("grasp did not retain target")
    # Fixed-trunk grasp preserves the measured upright prepared posture.
    # Verify actual retention, lift and posture before starting the hold.
    r.phase="verify_fixed_trunk_pickup"
    g=r.geom([r.target],["base_pose_world","arms","trunk"])
    ref,li,ti,he,dr=verify(r,g,initial_z,initial_h)
    minimum_lift=li; max_tilt=ti; minimum_height=he; max_drift=dr
    r.phase="steady_hold_batched"
    hold_start_step=g["capture"]["sim_step"]
    r.metrics.update(hold_start_step=hold_start_step,hold_start_capture=g["capture"]["capture_id"],hold_start_refs=r.refs())
    # Batch the requested hold within the native per-call maximum. The Harness
    # records every physical step; continuous success needs independent audit.
    remaining=r.parameters["hold_steps"]
    while remaining:
        count=min(150,remaining)
        r.call("wait",steps=count)
        remaining-=count
    g=r.geom([r.target],["base_pose_world","arms","trunk"])
    hold_end_step=g["capture"]["sim_step"]
    r.metrics.update(hold_end_step=hold_end_step,hold_end_capture=g["capture"]["capture_id"],hold_steps_observed=hold_end_step-hold_start_step)
    if hold_end_step-hold_start_step!=r.parameters["hold_steps"]: raise Stop("unexpected physical hold step count")
    _,li,ti,he,dr=verify(r,g,initial_z,initial_h,ref)
    minimum_lift=min(minimum_lift,li); max_tilt=max(max_tilt,ti)
    minimum_height=min(minimum_height,he); max_drift=max(max_drift,dr)
    r.metrics.update(grasp_calls=r.grasps,motor_calls=r.motors,failed_motor_calls=r.failed_motors,hold_steps_requested=r.parameters["hold_steps"],hold_metric_scope="endpoints_only",continuous_hold_validated=False,min_lift_m=minimum_lift,max_tilt_deg=max_tilt,min_height_m=minimum_height,initial_height_m=initial_h,max_drift_m=max_drift)
    if r.grasps!=1 or r.failed_motors: raise Stop("execution cleanliness")
    return dict(ok=True,metrics=r.metrics,**r.compact())

def Manip(ctx, *, recipe, targets, parameters=None, constraints=None, revision=None):
    """Execute a typed recipe through the supplied Harness context only.

    The runtime invokes this frozen implementation for one manip tool call.
    Endpoint checks never certify the independent continuous-hold goal.
    """
    try:
        names,parameters,constraints=_options(recipe,targets,parameters,constraints,revision)
    except ValueError as e:
        return dict(ok=False,recipe=recipe,phase="validate_parameters",error=str(e))
    r=Robot(ctx,names,parameters,constraints,revision)
    try: result=task(r)
    except Exception as e:
        r.metrics.update(grasp_calls=r.grasps,motor_calls=r.motors,failed_motor_calls=r.failed_motors)
        result=dict(ok=False,phase=r.phase,error=str(e),metrics=r.metrics,**r.compact())
    result.update(recipe=recipe,targets=names,parameters=parameters,constraints=constraints,
                  transport="ideal_checked_empty_navigation" if constraints["allow_base_motion"] else "local_only",
                  validation="endpoint_checks_only; independent per-step evaluator required")
    return result


def navigate_then_pick(ctx):
    """Explicit ideal/kinematic empty approach, then the frozen local recipe."""
    targets={"object":EntityRef(task_name="wine_bottle.n.01_1")}
    names,parameters,constraints=_options(
        RECIPE,targets,None,{"allow_base_motion":True},None)
    r=Robot(ctx,names,parameters,constraints,None)
    try:
        counter,g=perceive(r)
        record_baseline(r,g)  # Capture initial world height before navigation.
        g=navigate(r,counter,g)
        station=dict(transport="ideal_checked_empty_navigation",
                     motor_calls=r.motors,failed_motor_calls=r.failed_motors,
                     **r.compact())
        r.log("empty approach complete; select local countertop_pick")
        result=ctx.call("manip",recipe=RECIPE,
                     targets={key:{"task_name":value} for key,value in names.items()},
                     parameters=parameters,
                     constraints={"allow_base_motion":False,
                                  "allow_trunk_motion":True},
                     revision=r.obs["revision"],allow_draft=True)
        result["navigation"]=station
        return result
    except Exception as e:
        return dict(ok=False,phase=r.phase,error=str(e),
                    transport="ideal_checked_empty_navigation",
                    motor_calls=r.motors,failed_motor_calls=r.failed_motors,
                    **r.compact())


def main(ctx):
    """Complete single-bottle task using only this frozen project's code."""
    return navigate_then_pick(ctx)
