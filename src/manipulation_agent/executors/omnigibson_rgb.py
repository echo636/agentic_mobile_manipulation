"""RGB observer + private pixel-ray motor adapter for pinned OmniGibson 3.9.2."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

from ..contracts import SkillError
from ..omnigibson_backend import OmniGibsonBackend
from ..observations.rig import DIRECTIONS, camera_mount, look_at_orientation
from ..records import now
from .placement import CheckedPlacement
from .profiling import component, action_profile
from .carry import ControlledCarry

class RGBBackend(ControlledCarry, CheckedPlacement, OmniGibsonBackend):
    mode="rgb_only"

    def __init__(self,*args,record_video=False,**kwargs):
        self.ideal_carry = os.environ.get('MAS_GRASP_MODE','controlled')=='controlled'
        self._ideal_held=None;self._carry_relative=None;self._carry_contents=[];self._object_anchor=None
        self.fixed_surround_rgb = True
        self.record_video = record_video
        self.video = None
        self.video_render_stride = int(os.environ.get("MAS_VIDEO_RENDER_STRIDE", "2"))
        if self.video_render_stride not in (1,2,3): raise ValueError("Video render stride must be 1, 2 or 3")
        self._recorded_pixels = None
        self._recorded_capture_step = None
        self.spectator = None
        self._spectator_anchor = None
        self._spectator_choice = None
        self._base_target = None
        self.image_size=512
        self.capture_index=0
        self.image_files={}
        self.current_frames={}
        self._sensor_packets={}
        self.rig = {}
        super().__init__(*args,**kwargs)
        # Isaac Sim 5.1 documents a Replicator frame-loss issue when the
        # throttling extension toggles asynchronous rendering. Our capture
        # contract requires synchronous render-only flushes on the owner thread.
        import carb.settings
        settings=carb.settings.get_settings()
        settings.set_bool('/exts/isaacsim.core.throttling/enable_async',False)
        settings.set_bool('/app/asyncRendering',False)
        settings.set_int('/rtx/post/dlss/execMode',2)
        (self.output/'render_settings.json').write_text(json.dumps({
            'async_rendering':False,'throttling_enable_async':False,'dlss_exec_mode':2,
            'source':'https://docs.isaacsim.omniverse.nvidia.com/5.1.0/overview/known_issues.html',
            'purpose':'synchronous Replicator capture; not a proven native crash fix'},indent=2)+'\n')
        cameras = [n for n in self.robot.sensors if 'Camera' in n]
        if cameras:
            raise RuntimeError('Stock head/wrist camera exclusion was not applied')
        from omnigibson.sensors import VisionSensor
        base_pos = self.robot.get_position_orientation()[0]
        self.rig_height = float(self.robot.aabb[1][2] - base_pos[2]) + 0.05
        for direction in DIRECTIONS:
            sensor = VisionSensor(relative_prim_path='/mas_rgb_'+direction, name='mas_rgb_'+direction,
                modalities=['rgb','depth_linear','seg_semantic','seg_instance_id'],image_width=self.image_size,image_height=self.image_size,
                focal_length=10.0,horizontal_aperture=20.0,viewport_name=None)
            sensor.load(None)
            sensor.initialize()
            self.rig[direction] = sensor
        self._position_rig()
        for _ in range(20): self.og.sim.render()
        if self.record_video:
            from ..video import EpisodeVideo
            from omnigibson.sensors import VisionSensor
            # Use an independent offscreen render product at its final size. Do
            # not resize/destroy the shared GUI viewer product: it invalidates
            # robot RGB annotators in the pinned headless runtime.
            self.spectator = VisionSensor(relative_prim_path='/mas_spectator',name='mas_spectator',
                                          modalities=['rgb'],image_width=self.image_size,
                                          image_height=self.image_size,viewport_name=None)
            self.spectator.load(None)
            self.spectator.initialize()
            self.video = EpisodeVideo(self.output, fps=1.0/self.og.sim.get_sim_step_dt(),size=self.image_size,
                                      views=(*DIRECTIONS,'spectator'))
            self._position_spectator()
            for _ in range(20): self.og.sim.render()

    def _position_rig(self):
        """Kinematic sensor mount only: never writes robot pose or advances physics."""
        import omnigibson.utils.transform_utils as T
        base_pos, base_quat = self.robot.get_position_orientation()
        rotation = T.quat2mat(base_quat)
        for direction, sensor in self.rig.items():
            offset, basis = camera_mount(direction, self.rig_height)
            offset = self.torch.tensor(offset, device=base_pos.device)
            basis = self.torch.tensor(basis, device=base_pos.device)
            sensor.set_position_orientation(base_pos + rotation @ offset, T.mat2quat(rotation @ basis))

    def _position_spectator(self):
        """Collision-aware filming camera; its geometry/poses stay offline."""
        import omnigibson.utils.transform_utils as T
        from omnigibson.utils.sampling_utils import raytest
        torch = self.torch
        pos, quat = self.robot.get_position_orientation()
        pos = pos.cpu(); rotation = T.quat2mat(quat.cpu())
        yaw = math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        if self._spectator_anchor is not None:
            old_pos,old_yaw = self._spectator_anchor
            delta = math.atan2(math.sin(yaw-old_yaw),math.cos(yaw-old_yaw))
            if float(torch.linalg.norm(pos-old_pos)) < .002: return
        lo,hi = self.robot.aabb
        target = (lo.cpu()+hi.cpu())/2
        ignore = [link.prim_path for link in self.robot.links.values()]
        best = None
        for i,angle in enumerate((135,-135,90,-90,180,0,45,-45)):
            theta = math.radians(angle)
            for j,height in enumerate((.8,1.2)):
                offset = torch.tensor([2.4*math.cos(theta),2.4*math.sin(theta),height])
                distance = float(torch.linalg.norm(offset)); direction = offset/distance
                hit = raytest(target,target+offset,ignore_bodies=ignore)
                clearance = float(torch.linalg.norm(hit['position'].cpu()-target))-.25 if hit['hit'] else distance
                usable = max(.12,min(distance,clearance))
                score = usable-.015*i-.005*j + (.65 if (i,j)==self._spectator_choice else 0)
                if best is None or score>best[0]: best=(score,target+direction*usable,usable,(i,j))
        _,camera,clearance,self._spectator_choice = best
        orientation = torch.tensor(look_at_orientation(camera.tolist(),target.tolist()),dtype=camera.dtype)
        self.spectator.set_position_orientation(camera,orientation)
        self._spectator_anchor = (pos.clone(),yaw)
        self.og.sim.render()
        with (self.output/'spectator_poses.jsonl').open('a') as stream:
            stream.write(json.dumps({'env_step':self.steps,'position':camera.tolist(),'orientation':orientation.tolist(),
                                     'look_at':target.tolist(),'clearance_m':clearance,'audience':'offline_only'})+'\n')

    def _video_frame(self, kind):
        if self.video is None or self.video.closed: return
        fresh=kind!='env_step' or self._recorded_pixels is None or self.steps%self.video_render_stride==0
        with component(self,'video_total'):
            if fresh:
                try:
                    with component(self,'spectator_pose'):self._position_spectator()
                except Exception as exc:
                    with (self.output/'recording_warnings.jsonl').open('a') as stream:
                        stream.write(json.dumps({'at':now(),'env_step':self.steps,'component':'spectator_pose',
                            'type':type(exc).__name__,'error':str(exc),'fallback':'last_camera_pose'})+'\n')
                self._recorded_pixels=self._render_rgb_views(include_spectator=True)
                self._recorded_capture_step=self.steps
            with component(self,'video_encode_submit'):
                self.video.append(self._recorded_pixels,self.steps,kind,
                                  capture_env_step=self._recorded_capture_step,repeated=not fresh)

    def _render_rgb_views(self, include_spectator=False):
        """Render-product resizing can invalidate all cameras for several frames.

        Wait with render-only ticks; never advance physics to warm up recording.
        """
        import numpy as np
        self._position_rig()
        sensors = dict(self.rig)
        if include_spectator: sensors['spectator'] = self.spectator
        # Flush render-product latency after camera poses change, with physics frozen.
        for _ in range(3):
            with component(self,'render'):self.og.sim.render()
        shapes = {}
        for attempt in range(30):
            with component(self,'render'):self.og.sim.render()
            pixels = {}; packets = {}
            for view,sensor in sensors.items():
                with component(self,'sensor_readback'):data,info = sensor.get_obs()
                raw = data.get('rgb')
                if raw is None: continue
                rgb = raw.detach().cpu().numpy()
                shapes[view] = list(rgb.shape)
                if rgb.ndim == 3 and rgb.shape[:2] == (self.image_size,self.image_size) and rgb.shape[2] >= 3:
                    pixels[view] = rgb[...,:3].astype(np.uint8)
                    packets[view] = (data,info)
            if len(pixels) == len(sensors):
                self._sensor_packets = packets
                return pixels
        raise RuntimeError('RGB render products did not become ready: '+json.dumps(shapes))

    def _step(self, action):
        self._restore_object_anchor()
        self._carry_follow()
        with component(self,'physics_and_metrics'):super()._step(action)
        if self._base_target is not None:
            self._restore_base_target()
        self._restore_object_anchor();self._carry_follow()
        self._video_frame('env_step')

    def _restore_base_target(self):
        """Ideal base actuator holds posture while moving; no passive joint drift."""
        import omnigibson.utils.transform_utils as T
        target=self._base_target
        self.robot.set_joint_positions(target['posture'],indices=target['indices'])
        self.robot.set_position_orientation(target['position'],target['orientation'])
        self.robot.keep_still()
        if target['held'] is not None:
            target['held'].set_position_orientation(*T.pose_transform(
                target['position'],target['orientation'],*target['relative']))
            target['held'].keep_still()

    def mark_video_tool(self, name, arguments, request_id):
        if self.video: self.video.mark(name,arguments,request_id)

    def finalize_video(self):
        return self.video.finish(self.steps) if self.video else None

    def _objects(self):
        # PRIVATE routing after an agent selects a pixel; no task-scope candidate discovery.
        return {o.name:o for o in self.env.scene.objects if o is not self.robot and hasattr(o,'states')}

    def observe(self):
        from PIL import Image
        import numpy as np
        start_step = self.steps
        before_pos,before_quat = self.robot.get_position_orientation()
        before_joints = self.robot.get_joint_positions().clone()
        rendered = self._render_rgb_views()
        self.capture_index+=1
        self.current_frames={}
        folder=self.output/'frames';folder.mkdir(exist_ok=True)
        private_folder=self.output/'executor_frames';private_folder.mkdir(exist_ok=True)
        images=[];audit=[]
        capture = {'capture_id':f'capture-{self.capture_index:05d}', 'captured_at':now(),
                   'sim_step':start_step,'sim_time_seconds':start_step*self.og.sim.get_sim_step_dt()}
        for view,sensor in self.rig.items():
            ref=f'rgb-{self.capture_index:05d}-{view}'
            pixels=rendered[view]
            path=folder/f'{ref}.jpg'
            Image.fromarray(pixels).save(path,quality=92)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            position,orientation=sensor.get_position_orientation()
            frame={'sensor':sensor,'position':position.clone(),'orientation':orientation.clone(),
                   'intrinsic':sensor.intrinsic_matrix.clone(),'width':pixels.shape[1],'height':pixels.shape[0]}
            data,info=self._sensor_packets[view]
            frame['depth_linear']=data['depth_linear'].detach().cpu().clone()
            frame['seg_instance_id']=data['seg_instance_id'].detach().cpu().clone()
            frame['instance_paths']=dict(info['seg_instance_id'])
            np.savez_compressed(private_folder/f'{ref}.npz',depth_linear=frame['depth_linear'].numpy(),seg_instance_id=frame['seg_instance_id'].numpy())
            (private_folder/f'{ref}.json').write_text(json.dumps({'audience':'executor_private_only',
                'image_ref':ref,'rgb_sha256':digest,
                'position':position.tolist(),'orientation':orientation.tolist(),'intrinsic':frame['intrinsic'].tolist(),
                'instance_paths':frame['instance_paths']}))
            self.current_frames[ref]=frame
            self.image_files[ref]=path
            images.append({'image_ref':ref,'view':view,'width':pixels.shape[1],'height':pixels.shape[0],
                           'mime_type':'image/jpeg','sha256':digest})
            audit.append({'image_ref':ref,'file':str(path.relative_to(self.output)),'sha256':digest})
        with (self.output/'captures.jsonl').open('a') as stream:
            stream.write(json.dumps({'capture':self.capture_index,'env_steps':self.steps,
                                     'synchronized_capture':capture,'images':audit})+'\n')
        after_pos,after_quat = self.robot.get_position_orientation()
        after_joints = self.robot.get_joint_positions()
        unchanged = self.steps == start_step and self.torch.equal(before_pos,after_pos) and self.torch.equal(before_quat,after_quat) and self.torch.equal(before_joints,after_joints)
        with (self.output/'observation_capture_audit.jsonl').open('a') as stream:
            stream.write(json.dumps({'capture':capture,'audience':'executor_private','no_robot_motion':bool(unchanged),
                'start_step':start_step,'end_step':self.steps,'before_position':before_pos.tolist(),
                'after_position':after_pos.tolist(),'before_orientation':before_quat.tolist(),
                'after_orientation':after_quat.tolist(),'rig_sensor_names':[s.name for s in self.rig.values()],
                'stock_sensor_names':list(self.robot.sensors)})+'\n')
        if not unchanged: raise RuntimeError('Read-only camera capture changed robot state')
        self._video_frame('observation_boundary')
        return {'images':images,'observation_mode':self.mode,'capture':capture}

    def image_bytes(self,ref):
        if ref not in self.image_files: raise KeyError(ref)
        return self.image_files[ref].read_bytes(),'image/jpeg'

    def _ground(self,target):
        """Route exactly the chosen rendered pixel to the ideal actuator target.

        Private render-instance identity and depth use exactly the selected RGB
        pixel. Collision disagreement is recorded without substituting another
        object. No class search, alternate pixels or task-scope lookup occurs;
        segmentation and geometry never cross the model observation boundary.
        """
        from omnigibson.utils.sampling_utils import raytest
        import omnigibson.utils.transform_utils as T
        frame=self.current_frames.get(target['image_ref'])
        if frame is None: raise SkillError('stale_image_ref','Expired RGB capture')
        x,y=target['point'];K=frame['intrinsic'].cpu()
        px=round(x*(frame['width']-1));py=round(y*(frame['height']-1))
        # USD cameras look along -Z; image down corresponds to camera -Y.
        local=self.torch.tensor([(px-float(K[0,2]))/float(K[0,0]),
                                 -(py-float(K[1,2]))/float(K[1,1]),-1.0])
        start=frame['position'].cpu()
        depth=float(frame['depth_linear'][py,px])
        if not math.isfinite(depth) or not 0<depth<30:
            raise SkillError('no_surface_at_point','No supported visible surface at selected pixel')
        direction=T.quat2mat(frame['orientation'].cpu()) @ local
        point=start+direction*depth
        unit=direction/self.torch.linalg.norm(direction)
        hit=raytest(start,start+unit*30.0,ignore_bodies=[link.prim_path for link in self.robot.links.values()])
        gap=float(self.torch.linalg.norm(hit['position'].cpu()-point)) if hit['hit'] else None
        tolerance=max(.03,.02*float(self.torch.linalg.norm(point-start)))
        # The visible mesh at this exact RGB pixel owns the selection. Collision
        # proxies can disagree with render meshes (door handles, thin surfaces).
        # No search over nearby pixels, categories, task scope or goal states.
        instance=int(frame['seg_instance_id'][py,px])
        visual_path=frame['instance_paths'].get(instance,frame['instance_paths'].get(str(instance),''))
        obj=next((o for o in self.env.scene.objects if visual_path==o.prim_path or visual_path.startswith(o.prim_path+'/')),None)
        diagnostic={'at':now(),'routing':'same_pixel_render_instance_and_depth',
            'rigid_body':hit.get('rigidBody',''),'visual_mesh':visual_path,'depth_linear':depth,
            'hit_position':point.tolist(),'collision_position':hit['position'].tolist() if hit['hit'] else None,
            'depth_agreement_error_m':gap,'depth_agreement_tolerance_m':tolerance,
            'collision_agrees':gap is not None and gap<=tolerance,'selected_pixel':target['point'],
            'raster_pixel':[px,py],'image_ref':target['image_ref'],'audience':'executor_private'}
        with (self.output/'grounding_diagnostics.jsonl').open('a') as stream:stream.write(json.dumps(diagnostic)+'\n')
        if obj is None or obj is self.robot:
            raise SkillError('invalid_visual_target','Selected RGB pixel has no supported non-robot visual object')
        return obj,point,diagnostic

    def _turn(self,degrees,max_steps):
        import omnigibson.utils.transform_utils as T
        pos,quat = self.robot.get_position_orientation()
        rotation = T.quat2mat(quat)
        yaw = math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        return self._execute_base_path([pos[:2].cpu().tolist()],yaw+math.radians(degrees),max_steps)

    def _approach_visible(self, xy, point, selected_object):
        """Reject endpoints behind intervening geometry for this selected target."""
        from omnigibson.utils.sampling_utils import raytest
        torch=self.torch
        if math.dist(xy,point[:2].cpu().tolist())>1.4:return False
        start=point.cpu().clone();start[0]=xy[0];start[1]=xy[1]
        start[2]=max(float(self.robot.get_position_orientation()[0][2])+.65,float(point[2]))
        ignore=[l.prim_path for l in self.robot.links.values()]
        held=self._get_held()
        if held is not None:ignore.extend(l.prim_path for l in held.links.values())
        hit=raytest(start,point.cpu(),ignore_bodies=ignore)
        if not hit['hit']:return True
        body=hit.get('rigidBody','')
        return (selected_object is not None and body.startswith(selected_object.prim_path+'/')) or float(torch.linalg.norm(hit['position'].cpu()-point.cpu()))<.10

    def _navigate(self, target, max_steps):
        """Jinkai visual-point GT strategy with a private OmniGibson substrate."""
        from dataclasses import asdict
        from .gt_navigation import GridMap, NavigationError, plan_navigation, STRATEGY
        trav=self.env.scene.trav_map
        position,_=self.robot.get_position_orientation()
        point=target.get_position_orientation()[0]
        floor=min(range(len(trav.floor_heights)),key=lambda i:abs(float(position[2])-trav.floor_heights[i]))
        occupancy=trav._erode_trav_map(trav.floor_map[floor].clone()).cpu().numpy()
        height,width=occupancy.shape
        grid=GridMap(width,height,float(trav.map_resolution),
                     (-width*trav.map_resolution/2,-height*trav.map_resolution/2),
                     (occupancy!=0).astype('uint8').tobytes())
        try:
            with component(self,'navigation_planning'):
                plan=plan_navigation(grid,position[:2].cpu().tolist(),point[:2].cpu().tolist(),
                                     candidate_filter=lambda xy:self._approach_visible(xy,point,getattr(target,'selected_object',None)))
        except NavigationError as exc:
            raise SkillError('navigation_unreachable',str(exc)) from exc
        details={'at':now(),'audience':'executor_private','strategy':STRATEGY,'floor':floor,
                 'plan':asdict(plan),'map_resolution_m':grid.resolution,
                 'map_sha256':hashlib.sha256(grid.free).hexdigest(),
                 'dynamic_collision_check':False,'collision_substrate':'static_eroded_grid'}
        with (self.output/'navigation_plans.jsonl').open('a') as stream:
            stream.write(json.dumps(details)+'\n')
        result=self._execute_gt_plan(grid,plan,max_steps)
        self.navigation_distance+=result['actual_path_distance_m']
        return {**result,'strategy':STRATEGY,'planned_path_distance_m':plan.geodesic_m,
                'candidate_count':plan.candidates_considered,'reachable_candidates':plan.candidates_reachable,
                'start_grid_offset_m':plan.start_grid_offset_m,'dynamic_collision_check':False}

    def _execute_gt_plan(self, grid, plan, max_steps):
        """Follow the GT path using actual pose feedback and bounded commands."""
        import omnigibson.utils.transform_utils as T
        from .gt_navigation import GreedyGridFollower, NavigationError
        settling=10
        if max_steps<=settling:raise SkillError('action_timeout','Insufficient GT follower step budget')
        pos,quat=self.robot.get_position_orientation()
        follower=GreedyGridFollower(grid,plan,self.og.sim.get_sim_step_dt())
        held=self._get_held()
        relative=T.relative_pose_transform(*held.get_position_orientation(),pos,quat) if held else None
        joints=self.robot.get_joint_positions()
        indices=self.torch.tensor([i for i in range(len(joints)) if i not in self.robot.base_idx.tolist()],device=joints.device)
        posture=joints[indices].clone();before=self.steps;travelled=0.;previous=pos[:2].cpu().tolist()
        try:
            with (self.output/'base_motion.jsonl').open('a') as stream:
                while True:
                    actual_pos,actual_quat=self.robot.get_position_orientation()
                    rotation=T.quat2mat(actual_quat)
                    actual_yaw=math.atan2(float(rotation[1,0]),float(rotation[0,0]))
                    command=follower.next_pose((*actual_pos[:2].cpu().tolist(),actual_yaw))
                    if command is None:break
                    if self.steps-before>=max_steps-settling:
                        raise SkillError('action_timeout','GT follower exhausted its control-step budget',changed=self.steps>before)
                    x,y,yaw=command
                    new_pos=pos.clone();new_pos[0]=x;new_pos[1]=y
                    orientation=T.euler2quat(self.torch.tensor([0.,0.,yaw],device=quat.device))
                    self._base_target={'position':new_pos,'orientation':orientation,'indices':indices,
                                       'posture':posture,'held':held,'relative':relative}
                    self._restore_base_target()
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                    measured,measured_quat=self.robot.get_position_orientation()
                    actual_xy=measured[:2].cpu().tolist();travelled+=math.dist(previous,actual_xy);previous=actual_xy
                    stream.write(json.dumps({'at':now(),'env_step':self.steps,'commanded_position':new_pos.tolist(),
                        'actual_position':measured.tolist(),'actual_orientation':measured_quat.tolist(),
                        'commanded_yaw':yaw,'follower':'greedy_grid_pose_feedback','audience':'executor_private'})+'\n')
            motion_steps=self.steps-before
            if self._base_target is not None:
                for _ in range(settling):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            actual_pos,actual_quat=self.robot.get_position_orientation()
            final_error=math.dist(actual_pos[:2].cpu().tolist(),plan.goal)
            if final_error>follower.position_tolerance or not grid.navigable(grid.cell(actual_pos[:2].cpu().tolist())):
                raise SkillError('navigation_unreachable','GT follower final pose verification failed',changed=self.steps>before)
        except NavigationError as exc:
            raise SkillError('navigation_unreachable',str(exc),changed=self.steps>before) from exc
        finally:
            self._base_target=None
        return {'motor':'gt_grid_feedback_kinematic','nav_status':'reached','motion_steps':motion_steps,
                'steps':self.steps-before,'final_position_error_m':final_error,'actual_path_distance_m':travelled,
                'max_speed_m_s':.5,'max_yaw_speed_deg_s':60,'physical_controller':False}

    def _execute_base_path(self, points, end_yaw, max_steps):
        """Execute each bounded ideal pose in simulation and capture its real RGB.

        Privileged traversability and held-object attachment belong to the motor
        executor only. This is kinematic actuation, not physical path control.
        """
        import omnigibson.utils.transform_utils as T
        from .base_motion import trajectory
        pos,quat=self.robot.get_position_orientation()
        rotation = T.quat2mat(quat)
        yaw = math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        poses = trajectory(points,yaw,end_yaw,self.og.sim.get_sim_step_dt())
        settling = 10
        if len(poses)+settling > max_steps:
            raise SkillError('action_timeout','Selected route exceeds bounded motor time; choose a nearer visible point')
        held=self._get_held()
        relative=T.relative_pose_transform(*held.get_position_orientation(),pos,quat) if held else None
        before = self.steps
        joints=self.robot.get_joint_positions()
        indices=self.torch.tensor([i for i in range(len(joints)) if i not in self.robot.base_idx.tolist()],device=joints.device)
        posture=joints[indices].clone()
        try:
            with (self.output/'base_motion.jsonl').open('a') as stream:
                for x,y,angle in poses:
                    new_pos=pos.clone();new_pos[0]=x;new_pos[1]=y
                    new_quat=T.euler2quat(self.torch.tensor([0.,0.,angle],device=quat.device))
                    self._base_target={'position':new_pos,'orientation':new_quat,'indices':indices,
                                       'posture':posture,'held':held,'relative':relative}
                    self._restore_base_target()
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                    actual_pos,actual_quat=self.robot.get_position_orientation()
                    stream.write(json.dumps({'env_step':self.steps,'commanded_position':new_pos.tolist(),
                        'actual_position':actual_pos.tolist(),'actual_orientation':actual_quat.tolist(),
                        'commanded_yaw':angle,'posture_max_error':float(self.torch.max(self.torch.abs(self.robot.get_joint_positions()[indices]-posture))),
                        'audience':'executor_private'})+'\n')
            for _ in range(settling):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        finally:
            self._base_target=None
        return {'motor':'ideal_kinematic_path','motion_steps':len(poses),'settling_steps':settling,
                'steps':self.steps-before,'max_speed_m_s':.5,'max_yaw_speed_deg_s':60,
                'physical_controller':False,'posture_hold':'ideal_joint_position_projection_each_control_step'}

    @action_profile
    def execute_visual(self,primitive,target,max_steps,**kwargs):
        if primitive=='look':return self._turn(kwargs['yaw_degrees'],max_steps)
        if primitive=='release' and self.ideal_carry:return self._ideal_release(max_steps)
        if primitive=='wait':
            count=min(max_steps,int(round(float(kwargs.get('seconds',5.0))/self.og.sim.get_sim_step_dt())))
            for _ in range(count):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            return {'primitive':'wait','steps':count,'sim_seconds':count*self.og.sim.get_sim_step_dt()}
        if primitive=='release':return self.execute(primitive,None,max_steps)
        obj,point,grounding=self._ground(target)
        if primitive=='navigate_to':
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),selected_object=obj)
            return {**self._navigate(anchor,max_steps),'private_grounding':grounding}
        if obj is None or not hasattr(obj,'states'):
            raise SkillError('invalid_visual_target','No manipulable object at selected pixel')
        held=self._get_held()
        if primitive=='grasp' and obj.fixed_base: raise SkillError('fixed_object','Fixed object')
        if primitive=='grasp' and held is not None and held is not obj: raise SkillError('hand_occupied','Hand occupied')
        if primitive in {'place_inside','place_on_top'} and held is None: raise SkillError('empty_hand','Empty hand')
        if primitive in {'open','close','toggle_on','toggle_off'} and held is not None: raise SkillError('hand_occupied','Hand occupied')
        from omnigibson.object_states import Open,Inside
        if primitive=='place_inside' and Open in obj.states and not obj.states[Open].get_value():
            raise SkillError('container_closed','Placement into closed container rejected')
        if primitive=='grasp' and Inside in obj.states:
            for parent in self.env.scene.objects:
                if parent is not obj and hasattr(parent,'states') and Open in parent.states and not parent.states[Open].get_value() and obj.states[Inside].get_value(parent):
                    raise SkillError('container_closed','Grasp through closed container rejected')
        base=self.robot.get_position_orientation()[0]
        lo,hi=obj.aabb; nearest=self.torch.maximum(lo[:2],self.torch.minimum(base[:2],hi[:2]))
        if float(self.torch.linalg.norm(base[:2]-nearest))>1.4:
            # Approach only the selected target, within this action's existing
            # step budget; no new object discovery or goal access.
            before_approach=self.steps
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),selected_object=obj)
            self._navigate(anchor,max(1,max_steps-60))
            max_steps-=self.steps-before_approach
            base=self.robot.get_position_orientation()[0]
            nearest=self.torch.maximum(lo[:2],self.torch.minimum(base[:2],hi[:2]))
            if float(self.torch.linalg.norm(base[:2]-nearest))>1.4 or max_steps<30:
                raise SkillError('out_of_reach','No usable approach to the selected surface within this action budget',changed=True)
        if primitive=='grasp' and self.ideal_carry:
            result=self._ideal_grasp(obj,max_steps)
        elif primitive in {'open','close','toggle_on','toggle_off'} and self.ideal_carry:
            result=self._ideal_state_action(primitive,obj,max_steps)
        elif primitive=='place_on_top':
            result=self._checked_place_on_top(obj,max_steps,point,kwargs.get('placement_yaw_degrees'))
        elif primitive=='place_inside' and self.inside_placement=='official_volume':
            result=self._checked_place_inside(obj,max_steps)
        else:
            result=self.execute(primitive,obj.name,max_steps)
        return {**result,'private_grounding':grounding}

    def evaluate(self):
        result=super().evaluate()
        result['protocol']='rgb_agent_ideal_executor_v7_controlled_carry'
        result['observation_mode']=self.mode
        return result

    def provenance(self):
        from .gt_navigation import SOURCE_COMMIT, STRATEGY
        result=super().provenance()
        result.update(executor='controlled_carry_and_checked_placement_plus_jinkai_gt_navigation' if self.ideal_carry else 'symbolic_manipulation_plus_jinkai_gt_grid_navigation',
                      observation_mode=self.mode,image_size=self.image_size,
                      grounding='same_pixel_render_instance_and_depth_private_executor_only',
                      model_visible_truth=False)
        result['grasp_protocol'] = {'mode':'controlled_pose_carry' if self.ideal_carry else 'official_symbolic_fixed_joint','fixed_joint':not self.ideal_carry,'collision_and_gravity_disabled':False,'rigid_contents_follow':self.ideal_carry}
        result['record_video'] = self.record_video
        result['video_capture_policy'] = {'render_stride':self.video_render_stride,'fresh_observation_boundaries':True,'intermediate_frames':'explicit_previous_frame_hold','no_motion_interpolation':True}
        result['placement'] = {'on_top':'selected_surface_cuboid_then_official_sampler',
            'inside':'official_volume','verification':'physical_selected_surface_support_or_official_OnTop; official_Inside',
            'failure_policy':'restore_pre_action_state','goal_access':False}
        result['goal_evaluation_optimization'] = 'interned_literals_in_one_grounding_call; predicate_cache_within_one_read_only_scoring_pass; official_formula_unchanged'
        result['base_execution'] = 'feedback_greedy_grid_0.5m_s_60deg_s; selected-object approach; ideal grasp/place pose changes'
        result['navigation'] = {'strategy':STRATEGY,'source_repository':'dadwadw233/habitat-gs',
            'source_branch':'jinkai/harness','source_commit':SOURCE_COMMIT,
            'geometry':'OmniGibson static eroded traversability grid',
            'habitat_native_navmesh':False,'dynamic_collision_check':False,
            'goal_selection':'jinkai visual-point candidate sampling and ranking',
            'pixel_grounding':'same RGB pixel render instance and depth; no neighboring-pixel substitution',
            'model_visible_gt':False}
        result['robot_camera_views'] = list(DIRECTIONS)
        result['surround'] = 'four_fixed_cameras_one_simulation_state_no_robot_rotation'
        result['camera_rig'] = {'horizontal_fov_degrees':90,'pitch_down_degrees':20,
            'mount_radius_m':0.35,'mount_height_m':self.rig_height,'views':list(DIRECTIONS),
            'stock_wrist_cameras_enabled':False}
        return result

    def close(self):
        try:
            self.finalize_video()
        finally:
            super().close()
