"""RGB observer + private pixel-ray motor adapter for pinned OmniGibson 3.9.2."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

from ..contracts import SkillError
from ..deadline import EpisodeDeadline
from ..omnigibson_backend import OmniGibsonBackend
from ..observations.rig import DIRECTIONS, camera_mount, centered_head_height, look_at_orientation
from ..records import now
from .placement import CheckedPlacement
from .profiling import component, action_profile
from .carry import ControlledCarry
from .material_actions import CheckedMaterialActions
from .demo_motion import DemoMotion

class RGBBackend(DemoMotion, CheckedMaterialActions, ControlledCarry, CheckedPlacement, OmniGibsonBackend):
    mode="rgb_only"

    def __init__(self,*args,record_video=False,**kwargs):
        self.deadline=EpisodeDeadline.from_env()
        self.deadline.check()
        self.ideal_carry = os.environ.get('MAS_GRASP_MODE','controlled')=='controlled'
        self.demo_motion = os.environ.get('MAS_DEMO_MOTION','0') in {'1','true','yes'}
        if self.demo_motion and not self.ideal_carry:
            raise ValueError('Demo motion requires controlled carry')
        self._demo_arm = None
        self._demo_focus = None
        self._ideal_held=None;self._carry_relative=None;self._carry_contents=[];self._carry_dependencies=[];self._object_anchor=None
        self.fixed_surround_rgb = True
        self.private_viewer_grounding = False
        self.record_video = record_video
        self.video = None
        self.video_render_stride = int(os.environ.get("MAS_VIDEO_RENDER_STRIDE", "2"))
        if self.video_render_stride not in (1,2,3): raise ValueError("Video render stride must be 1, 2 or 3")
        self.video_render_flushes = int(os.environ.get('MAS_VIDEO_RENDER_FLUSHES', '4'))
        if self.video_render_flushes not in (2, 3, 4):
            raise ValueError('Video render flushes must be 2, 3 or 4; observation barriers stay at 4')
        self._recorded_pixels = None
        self._recorded_capture_step = None
        self.spectator = None
        self._spectator_anchor = None
        self._spectator_choice = None
        self._base_target = None
        self.image_size=int(os.environ.get('MAS_RGB_IMAGE_SIZE','512'))
        if self.image_size not in (512,768,1024):
            raise ValueError('RGB image size must be 512, 768 or 1024')
        self.rgb_jpeg_quality=int(os.environ.get('MAS_RGB_JPEG_QUALITY','92'))
        if self.rgb_jpeg_quality not in range(70,96):
            raise ValueError('RGB JPEG quality must be between 70 and 95')
        # Preserve the current four simultaneous virtual head cameras.
        # Their private depth is used only to ground the selected RGB pixel.
        self.rig_radius=0.0
        self.capture_index=0
        self.image_files={}
        self.current_frames={}
        self._sensor_packets={}
        self._sensor_intrinsics={}
        self.rig = {}
        super().__init__(*args,**kwargs)
        self._demo_trunk_home = (self.robot.get_joint_positions()[self.robot.trunk_control_idx].clone()
                                 if self.demo_motion else None)
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
        visual_points = []
        for link in self.robot.links.values():
            points = link.visual_boundary_points_world
            if points is not None:
                visual_points.extend(points.detach().cpu().tolist())
        self.rig_height = centered_head_height(visual_points, base_pos.detach().cpu().tolist())
        with self._startup_stage('surround_camera_setup_and_warmup'):
            for direction in DIRECTIONS:
                sensor = VisionSensor(relative_prim_path='/mas_rgb_'+direction, name='mas_rgb_'+direction,
                    modalities=['rgb','depth_linear'],image_width=self.image_size,image_height=self.image_size,
                    focal_length=10.0,horizontal_aperture=20.0,viewport_name=None)
                sensor.load(None)
                sensor.initialize()
                # Otherwise intrinsic_matrix lazily attaches a new annotator AFTER
                # RGB readback and warms only four frames, sometimes returning a
                # degenerate projection. Attach all before the shared barrier.
                sensor.initialize_sensors(names='camera_params')
                self.rig[direction] = sensor
            self._position_rig()
            for _ in range(20): self.og.sim.render()
        with self._startup_stage('spectator_camera_setup_and_warmup'):
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
                                          views=(*DIRECTIONS,'spectator'),demo_motion=self.demo_motion)
                self._position_spectator()
                for _ in range(20): self.og.sim.render()

    def _position_rig(self):
        """Kinematic sensor mount only: never writes robot pose or advances physics."""
        import omnigibson.utils.transform_utils as T
        base_pos, base_quat = self.robot.get_position_orientation()
        rotation = T.quat2mat(base_quat)
        for direction, sensor in self.rig.items():
            offset, basis = camera_mount(direction, self.rig_height, radius=self.rig_radius)
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
        if self._demo_focus is not None:
            # Film the selected interaction, while keeping part of the robot in shot.
            target = (target+self._demo_focus.cpu())/2
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
        # The caller positions every camera before a shared render barrier.
        # Rendering here would draw the old surround-camera poses unnecessarily.
        with (self.output/'spectator_poses.jsonl').open('a') as stream:
            stream.write(json.dumps({'env_step':self.steps,'position':camera.tolist(),'orientation':orientation.tolist(),
                                     'look_at':target.tolist(),'clearance_m':clearance,'audience':'offline_only'})+'\n')

    def _prepare_spectator(self):
        try:
            with component(self,'spectator_pose'): self._position_spectator()
        except Exception as exc:
            with (self.output/'recording_warnings.jsonl').open('a') as stream:
                stream.write(json.dumps({'at':now(),'env_step':self.steps,'component':'spectator_pose',
                    'type':type(exc).__name__,'error':str(exc),'fallback':'last_camera_pose'})+'\n')

    def _video_frame(self, kind, rendered=None):
        if self.video is None or self.video.closed: return
        fresh=kind!='env_step' or self._recorded_pixels is None or self.steps%self.video_render_stride==0
        with component(self,'video_total'):
            if rendered is not None:
                self._recorded_pixels = rendered
                self._recorded_capture_step = self.steps
            elif fresh:
                self._prepare_spectator()
                self._recorded_pixels=self._render_rgb_views(
                    include_spectator=True, flushes=self.video_render_flushes)
                self._recorded_capture_step=self.steps
            with component(self,'video_encode_submit'):
                self.video.append(self._recorded_pixels,self.steps,kind,
                                  capture_env_step=self._recorded_capture_step,repeated=not fresh,
                                  check_active=lambda:self.deadline.check(changed=True))

    def _wait_for_rgb_annotations(self):
        """Finish Replicator capture at the current pose without advancing time.

        Shape-valid annotator buffers can still contain a previous camera pose.
        Isaac Sim 5.1's documented completion barrier waits for its render
        dispatcher; additional arbitrary render ticks are not that guarantee.
        This is called only by the simulator owner, for calibrated observations.
        """
        import numpy as np
        import omnigibson.lazy as lazy
        timeline = lazy.omni.timeline.get_timeline_interface()
        def state():
            position, orientation = self.robot.get_position_orientation()
            return {'env_step': self.steps, 'sim_time': float(self.og.sim.current_time),
                    'sim_step_index': int(self.og.sim.current_time_step_index),
                    'timeline_time': float(timeline.get_current_time()),
                    'position': position.detach().cpu().numpy().tolist(),
                    'orientation': orientation.detach().cpu().numpy().tolist(),
                    'joints': self.robot.get_joint_positions().detach().cpu().numpy().tolist()}
        before = state()
        # delta_time=0 is the official no-time-advance mode. Do not use
        # wait_until_complete(), which stops Replicator generation.
        with component(self, 'render_annotation_barrier'):
            # Replicator updates SDGPipeline USD attributes while dispatching
            # the capture. OG requires these writes in its edit scope, whose
            # exit also synchronizes the changes to Fabric.
            with self.og.sim.editing_usd():
                lazy.omni.replicator.core.orchestrator.step(
                    delta_time=0.0, pause_timeline=False, wait_for_render=True, rt_subframes=1)
        after = state()
        changed = [key for key in ('env_step', 'sim_step_index') if before[key] != after[key]]
        changed += [key for key in ('sim_time', 'timeline_time')
                    if not math.isclose(before[key], after[key], rel_tol=0., abs_tol=1e-10)]
        changed += [key for key in ('position', 'orientation', 'joints')
                    if not np.allclose(before[key], after[key], rtol=0., atol=1e-6)]
        with (self.output / 'annotation_barriers.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': now(), 'audience': 'executor_private',
                'status': 'failed' if changed else 'passed', 'api': 'replicator.orchestrator.step',
                'delta_time': 0., 'pause_timeline': False, 'wait_for_render': True, 'rt_subframes': 1,
                'before': before, 'after': after, 'changed_fields': changed}) + '\n')
        if changed:
            raise RuntimeError('Replicator capture changed simulation state: ' + ', '.join(changed))

    def _render_rgb_views(self, include_spectator=False, flushes=4, require_calibration=False):
        """Render-product resizing can invalidate all cameras for several frames.

        Wait with render-only ticks; never advance physics to warm up recording.
        """
        import numpy as np
        self._position_rig()
        sensors = dict(self.rig)
        if include_spectator: sensors['spectator'] = self.spectator
        # Flush render-product latency after camera poses change, with physics frozen.
        for _ in range(flushes - 1):
            with component(self,'render'):self.og.sim.render()
        shapes = {}; readiness_failures=[]
        for attempt in range(30):
            with component(self,'render'):self.og.sim.render()
            if require_calibration:
                self._wait_for_rgb_annotations()
            pixels = {}; packets = {}; intrinsics={}; pending={}
            for view,sensor in sensors.items():
                with component(self,'sensor_readback'):data,info = sensor.get_obs()
                raw = data.get('rgb')
                if raw is None: continue
                rgb = raw.detach().cpu().numpy()
                shapes[view] = list(rgb.shape)
                if rgb.ndim == 3 and rgb.shape[:2] == (self.image_size,self.image_size) and rgb.shape[2] >= 3:
                    if require_calibration and view in self.rig:
                        try:
                            intrinsic=sensor.intrinsic_matrix.clone()
                        except (AssertionError,KeyError) as exc:
                            pending[view]=str(exc)
                            continue
                        matrix=intrinsic.detach().cpu().numpy()
                        if (matrix.shape!=(3,3) or not np.isfinite(matrix).all()
                            or matrix[0,0]<=0 or matrix[1,1]<=0 or abs(matrix[2,2]-1)>1e-6):
                            pending[view]='invalid_camera_intrinsics'
                            continue
                        depth=data.get('depth_linear')
                        if depth is None or tuple(depth.shape[:2])!=(self.image_size,self.image_size):
                            pending[view]='depth_render_product_not_ready'
                            continue
                        intrinsics[view]=intrinsic
                    pixels[view] = rgb[...,:3].astype(np.uint8)
                    packets[view] = (data,info)
            if pending:readiness_failures.append({'attempt':attempt+1,'pending':pending})
            if len(pixels) == len(sensors):
                self._sensor_packets = packets
                if require_calibration:
                    self._sensor_intrinsics=intrinsics
                    with (self.output/'camera_readiness.jsonl').open('a') as stream:
                        stream.write(json.dumps({'status':'passed','env_step':self.steps,'render_attempts':attempt+1,
                                                 'retries':readiness_failures,'physics_steps':0})+'\n')
                return pixels
        if require_calibration:
            with (self.output/'camera_readiness.jsonl').open('a') as stream:
                stream.write(json.dumps({'status':'failed','env_step':self.steps,'render_attempts':30,
                                         'retries':readiness_failures,'physics_steps':0})+'\n')
        raise RuntimeError('Camera render products did not become ready: '+json.dumps({'rgb_shapes':shapes,'calibration':pending}))

    def _step(self, action):
        self.deadline.check(changed=True)
        if self._base_target is not None:self._restore_base_target()
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
        if self.video is not None and not self.video.closed:
            self._prepare_spectator()
        # The observation and its replay frame use identical RGB arrays, from
        # one frozen simulation state and the unchanged four-tick barrier.
        rendered = self._render_rgb_views(include_spectator=self.video is not None and not self.video.closed,
                                          require_calibration=True)
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
            Image.fromarray(pixels).save(path,quality=self.rgb_jpeg_quality)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            position,orientation=sensor.get_position_orientation()
            frame={'sensor':sensor,'position':position.clone(),'orientation':orientation.clone(),
                   'intrinsic':self._sensor_intrinsics[view],'width':pixels.shape[1],'height':pixels.shape[0]}
            data,info=self._sensor_packets[view]
            frame['depth_linear']=data['depth_linear'].detach().cpu().clone()
            np.savez_compressed(private_folder/f'{ref}.npz',depth_linear=frame['depth_linear'].numpy())
            (private_folder/f'{ref}.json').write_text(json.dumps({'audience':'executor_private_only',
                'image_ref':ref,'rgb_sha256':digest,
                'position':position.tolist(),'orientation':orientation.tolist(),'intrinsic':frame['intrinsic'].tolist()}))
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
        self._video_frame('observation_boundary', rendered=rendered)
        return {'images':images,'observation_mode':self.mode,'capture':capture}

    def image_bytes(self,ref):
        if ref not in self.image_files: raise KeyError(ref)
        return self.image_files[ref].read_bytes(),'image/jpeg'

    def _ground(self,target, *, require_object=True):
        """Use the chosen pixel's depth backprojection directly as the target.

        Navigation needs only this point. Manipulation additionally resolves the
        first visual object on the same ray, solely for its simulator handle.
        No mesh/collision depth comparison, point replacement, alternate pixel,
        semantic target search or task-scope lookup occurs.
        """
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
        diagnostic={'at':now(),'routing':'direct_pixel_depth_backprojection',
            'depth_linear':depth,'hit_position':point.tolist(),'depth_consistency_check':False,
            'object_resolution_required':require_object,'selected_pixel':target['point'],
            'raster_pixel':[px,py],'image_ref':target['image_ref'],'audience':'executor_private'}
        obj=None
        if require_object:
            from .visual_mesh_grounding import query_visual_surface
            unit=direction/self.torch.linalg.norm(direction)
            try:
                with component(self,'visual_grounding'):
                    obj,visual=query_visual_surface(self,start,unit)
            except SkillError as exc:
                diagnostic.update(status='failed',code=exc.code,detail=str(exc),
                                  visual_query=getattr(exc,'diagnostics',{}))
                with (self.output/'grounding_diagnostics.jsonl').open('a') as stream:stream.write(json.dumps(diagnostic)+'\n')
                raise
            diagnostic.update(visual,selected_object_prim=obj.prim_path)
        with (self.output/'grounding_diagnostics.jsonl').open('a') as stream:stream.write(json.dumps(diagnostic)+'\n')
        return obj,point,diagnostic

    def _turn(self,degrees,max_steps):
        import omnigibson.utils.transform_utils as T
        pos,quat = self.robot.get_position_orientation()
        rotation = T.quat2mat(quat)
        yaw = math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        return self._execute_base_path([pos[:2].cpu().tolist()],yaw+math.radians(degrees),max_steps)

    def _approach_visible(self, xy, point, selected_object, *, margin=.04, max_distance=1.4):
        """Check the selected point in actual candidate camera frusta and rays."""
        from omnigibson.utils.sampling_utils import raytest
        from ..observations.rig import visible_rig_rays
        torch=self.torch
        if math.dist(xy,point[:2].cpu().tolist())>max_distance:return False
        ignore=[l.prim_path for l in self.robot.links.values()]
        held=self._get_held()
        if held is not None:ignore.extend(l.prim_path for l in held.links.values())
        ignore.extend(l.prim_path for obj,_ in self._carry_contents for l in obj.links.values())
        yaw=math.atan2(float(point[1])-xy[1],float(point[0])-xy[0])
        rays=visible_rig_rays(xy,yaw,float(self.robot.get_position_orientation()[0][2]),self.rig_height,
                              point.cpu().tolist(),margin=margin,radius=self.rig_radius)
        for _,origin,_ in rays:
            hit=raytest(torch.tensor(origin),point.cpu(),ignore_bodies=ignore)
            if not hit['hit'] or float(torch.linalg.norm(hit['position'].cpu()-point.cpu()))<.10:return True
        return False

    def _navigate(self, target, max_steps, *, for_manipulation=False):
        """Jinkai visual-point GT strategy with a private OmniGibson substrate."""
        from dataclasses import asdict
        from .gt_navigation import GridMap, NavigationError, plan_navigation, visual_approach_settings, STRATEGY
        trav=self.env.scene.trav_map
        position,_=self.robot.get_position_orientation()
        point=target.get_position_orientation()[0]
        standoff,margin=(.7,.04) if for_manipulation else visual_approach_settings(
            self.rig_height,float(position[2]),float(point[2]))
        floor=min(range(len(trav.floor_heights)),key=lambda i:abs(float(position[2])-trav.floor_heights[i]))
        occupancy=trav._erode_trav_map(trav.floor_map[floor].clone()).cpu().numpy()
        height,width=occupancy.shape
        grid=GridMap(width,height,float(trav.map_resolution),
                     (-width*trav.map_resolution/2,-height*trav.map_resolution/2),
                     (occupancy!=0).astype('uint8').tobytes())
        try:
            with component(self,'navigation_planning'):
                plan=plan_navigation(grid,position[:2].cpu().tolist(),point[:2].cpu().tolist(),
                                     standoff=standoff,
                                     candidate_filter=lambda xy:self._approach_visible(
                                         xy,point,getattr(target,'selected_object',None),
                                         margin=margin,max_distance=max(1.4,standoff+.35)))
        except NavigationError as exc:
            raise SkillError(exc.code,str(exc)) from exc
        details={'at':now(),'audience':'executor_private','strategy':STRATEGY,'floor':floor,
                 'plan':asdict(plan),'map_resolution_m':grid.resolution,
                 'visual_standoff_m':standoff,'visual_margin':margin,
                 'map_sha256':hashlib.sha256(grid.free).hexdigest(),
                 'dynamic_collision_check':False,'collision_substrate':'static_eroded_grid',
                 'precomputed_walkability':True,'online_mapping':False}
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
            raise SkillError(exc.code,str(exc),changed=self.steps>before) from exc
        finally:
            self._base_target=None
        return {'motor':'gt_grid_feedback_kinematic','nav_status':'reached','motion_steps':motion_steps,
                'steps':self.steps-before,'final_position_error_m':final_error,'actual_path_distance_m':travelled,
                'heading_tolerance_rad':follower.heading_tolerance,
                'final_yaw_tolerance_rad':follower.final_yaw_tolerance,
                'progress_measure':'directed_path_distance_or_target_yaw_convergence',
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
        if primitive in {'look','navigate_to','release','wait'} and getattr(self,'_demo_focus',None) is not None:
            self._demo_focus=None;self._spectator_anchor=None
        if primitive=='look':return self._turn(kwargs['yaw_degrees'],max_steps)
        if primitive=='release' and self.ideal_carry:return self._ideal_release(max_steps)
        if primitive=='wait':
            count=min(max_steps,int(round(float(kwargs.get('seconds',5.0))/self.og.sim.get_sim_step_dt())))
            with self._anchored_operation():
                for _ in range(count):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            return {'primitive':'wait','steps':count,'sim_seconds':count*self.og.sim.get_sim_step_dt()}
        if primitive=='release':return self.execute(primitive,None,max_steps)
        obj,point,grounding=self._ground(target,require_object=primitive!='navigate_to')
        if primitive=='navigate_to':
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),selected_object=obj)
            return {**self._navigate(anchor,max_steps),'private_grounding':grounding}
        if obj is None or not hasattr(obj,'states'):
            raise SkillError('invalid_visual_target','No manipulable object at selected pixel')
        held=self._get_held()
        if primitive=='grasp' and held is None and getattr(self,'demo_motion',False):
            self._demo_arm=None
        if primitive=='grasp' and obj.fixed_base: raise SkillError('fixed_object','Fixed object')
        if primitive=='grasp' and held is not None and held is not obj: raise SkillError('hand_occupied','Hand occupied')
        if primitive in {'attach','hang','place_inside','place_on_top','wipe','sweep','vacuum',
                         'spray','spread','soak','cut'} and held is None: raise SkillError('empty_hand','Empty hand')
        if held is not None and primitive in {'open','close','toggle_on','toggle_off'}:
            # Controlled carry can operate the switch on the object already
            # being carried. This does not grant an extra hand for a different
            # object, or change articulated-container handling while carrying.
            held_self_toggle=self.ideal_carry and held is obj and primitive in {'toggle_on','toggle_off'}
            if not held_self_toggle:raise SkillError('hand_occupied','Hand occupied')
        from omnigibson.object_states import Open,Inside
        if primitive=='place_inside' and Open in obj.states and not obj.states[Open].get_value():
            raise SkillError('container_closed','Placement into closed container rejected')
        if primitive=='grasp' and Inside in obj.states:
            for parent in self.env.scene.objects:
                if parent is not obj and hasattr(parent,'states') and Open in parent.states and not parent.states[Open].get_value() and obj.states[Inside].get_value(parent):
                    raise SkillError('container_closed','Grasp through closed container rejected')
        base=self.robot.get_position_orientation()[0]
        # A long cabinet/floor AABB can contain the base while the selected
        # visible surface is far away. Reach belongs to that selected point.
        reach_limit=1.4
        if float(self.torch.linalg.norm(base[:2]-point[:2]))>reach_limit:
            if primitive in {'attach','hang'}:
                raise SkillError('out_of_reach','Navigate to a visible approach to the selected parent before attaching')
            # Approach only the selected target, within this action's existing
            # step budget; no new object discovery or goal access.
            before_approach=self.steps
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),selected_object=obj)
            self._navigate(anchor,max(1,max_steps-60),for_manipulation=True)
            max_steps-=self.steps-before_approach
            base=self.robot.get_position_orientation()[0]
            if float(self.torch.linalg.norm(base[:2]-point[:2]))>reach_limit or max_steps<30:
                raise SkillError('out_of_reach','No usable approach to the selected surface within this action budget',changed=True)
        shown = (self._demo_reach(point,max_steps,anchor=None if obj is held else obj,
                                  arm=self._demo_arm if held is not None else None,
                                  action=primitive)
                 if getattr(self,'demo_motion',False) else 0)
        max_steps -= shown
        contact_limit=(.45 if primitive=='spray' else .30 if primitive=='vacuum' else .18)
        if (shown and self._demo_last_reach_error>contact_limit and obj is not held
                and max_steps>170):
            # A visual standoff may keep the selected pixel in view while the
            # posture-limited hand cannot touch it. Reuse that exact pixel for
            # one closer, visibility-checked approach; never select a new goal.
            first_error=self._demo_last_reach_error
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),
                                   selected_object=obj)
            before_retry=self.steps
            self._navigate(anchor,max_steps-120,for_manipulation=True)
            max_steps-=self.steps-before_retry
            self._demo_record(action=primitive,status='approach_retry',first_error_m=first_error,
                              target_point=point.tolist())
            retry=self._demo_reach(point,max_steps,anchor=obj,
                                   arm=self._demo_arm if held is not None else None,
                                   action=primitive+'_after_approach')
            shown+=retry;max_steps-=retry
        if shown and self._demo_last_reach_error>contact_limit:
            raise SkillError('out_of_reach',
                             'Robot hand could not approach the selected surface; choose a closer visible approach',
                             changed=True)
        if shown and primitive in {'wipe','sweep','vacuum','spray','spread','soak','cut'}:
            stroke=point.clone()
            if primitive in {'soak','cut'}:
                stroke[2]-=.06
            else:
                stroke[0]+=.08
            stroke_steps=self._demo_reach(stroke,max_steps,anchor=obj,
                                          arm=self._demo_arm,action=primitive+'_stroke')
            shown+=stroke_steps;max_steps-=stroke_steps
        if primitive=='grasp' and self.ideal_carry:
            before_grasp=self.steps
            result=self._ideal_grasp(obj,max_steps)
            if shown and self._demo_arm is not None:
                hand=self.robot.eef_links[self._demo_arm].get_position_orientation()[0]
                lifted=hand.clone();lifted[2]+=.16
                shown+=self._demo_reach(lifted,max_steps-(self.steps-before_grasp),
                                        arm=self._demo_arm,action='lift_after_grasp')
                shown+=self._demo_transport_pose(max_steps-(self.steps-before_grasp))
        elif primitive in {'attach','hang'}:
            if primitive=='hang' and not any(word in str(obj.category).lower() for word in ('nail','hook','hanger')):
                raise SkillError('unsupported_relation','Selected object is not a hanging anchor')
            result=self._checked_attach(obj,max_steps)
            if primitive=='hang':
                result={**result,'primitive':'hang'}
        elif primitive in {'wipe','sweep','vacuum','spray','spread'}:
            result=self._checked_surface_action(primitive,obj,max_steps)
        elif primitive=='soak':
            result=self._checked_soak(obj,max_steps)
        elif primitive=='cut':
            result=self._checked_cut(obj,max_steps)
        elif primitive in {'open','close','toggle_on','toggle_off'} and self.ideal_carry:
            result=self._ideal_state_action(primitive,obj,max_steps)
        elif primitive=='place_on_top':
            result=self._checked_place_on_top(obj,max_steps,point,kwargs.get('placement_yaw_degrees'))
        elif primitive=='place_inside' and self.inside_placement=='official_volume':
            result=self._checked_place_inside(obj,max_steps)
        else:
            result=self.execute(primitive,obj.name,max_steps)
        if shown:
            result={**result,'demo_motion':{'real_env_steps':shown,'ideal_contact':True}}
        return {**result,'private_grounding':grounding}

    def evaluate(self):
        result=super().evaluate()
        result['protocol']='rgb_agent_ideal_executor_v9_shared_episode_clock'
        result['observation_mode']=self.mode
        result['demo_motion']=getattr(self,'demo_motion',False)
        return result

    def provenance(self):
        from .gt_navigation import SOURCE_COMMIT, STRATEGY
        result=super().provenance()
        result.update(executor='controlled_carry_and_checked_placement_plus_jinkai_gt_navigation' if self.ideal_carry else 'symbolic_manipulation_plus_jinkai_gt_grid_navigation',
                      observation_mode=self.mode,image_size=self.image_size,demo_motion=getattr(self,'demo_motion',False),
                      grounding='direct_pixel_depth_backprojection_private_executor_only',
                      model_visible_truth=False)
        result['grounding_protocol'] = {'position':'same_pixel_depth_linear_backprojection',
            'depth_consistency_check':False, 'navigation_object_lookup':False,
            'manipulation_object_lookup':'first_visual_surface_on_selected_ray',
            'mesh_hit_replaces_backprojected_point':False, 'robot_mesh_blocks_object_selection':True}
        result['grasp_protocol'] = {'mode':'controlled_pose_carry' if self.ideal_carry else 'official_symbolic_fixed_joint','fixed_joint':not self.ideal_carry,'collision_and_gravity_disabled':False,'rigid_contents_follow':self.ideal_carry,'payload_relations':['Inside','OnTop'],'payload_closure':'transitive_rigid_support_with_postplacement_verification'}
        result['record_video'] = self.record_video
        result['video_capture_policy'] = {'render_stride':self.video_render_stride,
            'video_render_flushes':self.video_render_flushes, 'observation_render_flushes':4,
            'observation_pixels_reused_for_video':True, 'fresh_observation_boundaries':True,
            'intermediate_frames':'explicit_previous_frame_hold','no_motion_interpolation':True}
        result['placement'] = {'on_top':'strict_selected_surface_cuboid_no_object_wide_fallback',
            'inside':'official_volume','verification':'selected_surface_geometric_support_required; contact_predicate_diagnostic_only; official_Inside_and_payload_relations',
            'base_anchor':'control_and_sampler_physics_steps',
            'failure_policy':'restore_pre_action_state','goal_access':False}
        result['goal_evaluation_optimization'] = 'interned_literals_in_one_grounding_call; predicate_cache_within_one_read_only_scoring_pass; official_formula_unchanged'
        result['base_execution'] = 'feedback_greedy_grid_0.5m_s_60deg_s; selected-object approach; ideal grasp/place pose changes'
        result['navigation'] = {'strategy':STRATEGY,'source_repository':'dadwadw233/habitat-gs',
            'source_branch':'jinkai/harness','source_commit':SOURCE_COMMIT,
            'geometry':'OmniGibson static eroded traversability grid',
            'map_input':'precomputed scene floor traversability; selected-pixel depth supplies the target only',
            'localization':'private simulator pose',
            'precomputed_walkability':True,'static_map_loader_disabled':False,'online_mapping':False,
            'habitat_native_navmesh':False,'dynamic_collision_check':False,
            'erosion':{'implementation':'pinned OmniGibson _erode_trav_map without robot argument',
                'configured_default_erosion_radius':0.57,'map_resolution_m':0.05,
                'kernel':'12x12 square at configured resolution; parameter is not a disk radius'},
            'segment_validation':'exact_grid_supercover_for_planner_and_follower',
            'approach_visibility':'actual_four_camera_frusta_and_collision_rays',
            'goal_selection':'jinkai visual-point candidate sampling and ranking',
            'pixel_grounding':'same RGB pixel depth and visual mesh triangle ray; no neighboring-pixel substitution',
            'model_visible_gt':False}
        result['robot_camera_views'] = list(DIRECTIONS)
        result['private_grounding_provider']='CPU visual mesh ray + selected-pixel depth; no segmentation annotator'
        result['surround'] = 'four_fixed_cameras_one_simulation_state_no_robot_rotation'
        result['camera_rig'] = {'horizontal_fov_degrees':90,'pitch_down_degrees':20,
            'mount_radius_m':self.rig_radius,'mount_height_m':self.rig_height,'views':list(DIRECTIONS),
            'height_policy':'own_visual_geometry_below_lower_20_percent_with_5cm_clearance',
            'stock_wrist_cameras_enabled':False}
        return result

    def close(self):
        try:
            self.finalize_video()
        finally:
            super().close()
