"""RGB observer + private pixel-ray motor adapter for pinned OmniGibson 3.9.2."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

from ..contracts import SkillError
from ..runtime.deadline import EpisodeDeadline
from .omnigibson_backend import OmniGibsonBackend
from ..observations.rig import DIRECTIONS, camera_mount, robot_head_mount_height, look_at_orientation
from ..records import JsonlAppend, now
from .placement import CheckedPlacement
from .profiling import component, action_profile
from .carry import ControlledCarry
from .cloth_actions import CheckedClothActions
from .material_actions import CheckedMaterialActions
from .material_transfer import CheckedMaterialTransfer
from .demo_motion import DemoMotion
from .local_rgb import LocalRGBManipulation
from .local_manipulation import LocalLimits

class RGBBackend(LocalRGBManipulation, DemoMotion, CheckedClothActions, CheckedMaterialActions, CheckedMaterialTransfer, ControlledCarry, CheckedPlacement, OmniGibsonBackend):
    mode="rgb_only"

    def _config(self, task, gpu, max_steps):
        config = super()._config(task, gpu, max_steps)
        if getattr(self, 'use_gpu_dynamics', False):
            # Environment loads external sensors while physics is stopped.
            # Avoid adding render products to an already active GPU particle
            # scene. The same rig is positioned and warmed after task restore.
            sensors = list(config['env'].get('external_sensors') or [])
            names = {row.get('name') for row in sensors}
            # camera_params is an auxiliary annotator, explicitly excluded
            # from VisionSensor.all_modalities. Attach it during rig warmup.
            requested = [('mas_rgb_'+direction, ['rgb', 'depth_linear'])
                         for direction in DIRECTIONS]
            if self.record_video:
                requested.append(('mas_spectator', ['rgb']))
            for name, modalities in requested:
                if name in names:
                    raise ValueError('Reserved RGB sensor name is already configured: '+name)
                kwargs = dict(image_width=self.image_size, image_height=self.image_size, viewport_name=None)
                if name != 'mas_spectator':
                    kwargs.update(focal_length=10.0, horizontal_aperture=20.0)
                row = dict(sensor_type='VisionSensor', name=name, relative_prim_path='/'+name,
                    modalities=modalities, sensor_kwargs=kwargs, include_in_obs=False)
                sensors.append(row)
            config['env']['external_sensors'] = sensors
        return config

    def _preloaded_rgb_sensor(self, name):
        if not getattr(self, 'use_gpu_dynamics', False):
            return None
        sensor = (self.env.external_sensors or {}).get(name)
        if sensor is None or not sensor.initialized:
            raise RuntimeError('GPU RGB sensor was not initialized before physics: '+name)
        return sensor

    def __init__(self,*args,record_video=False,**kwargs):
        self.deadline=EpisodeDeadline.from_env()
        self.deadline.check()
        # A symbolic grasp creates the simulator's native grasp joint once.
        # Subsequent contacts and released objects belong to the physics solver.
        self.ideal_carry = False
        self.demo_motion = os.environ.get('MAS_DEMO_MOTION','0') in {'1','true','yes'}
        if self.demo_motion and not self.ideal_carry:
            raise ValueError('Demo motion requires controlled carry')
        self.local_manipulation = os.environ.get("MAS_LOCAL_MANIPULATION", "1") not in {"0", "false", "no"}
        self.local_limits = LocalLimits()
        self._local_operation = None
        self._demo_arm = None
        self._demo_focus = None
        self.fixed_surround_rgb = True
        self.private_viewer_grounding = False
        self.renderer_pixel_owner = os.environ.get('MAS_RENDERER_PIXEL_OWNER','0') == '1'
        self._renderer_owner_annotators = {}
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
        if self.image_size not in (512,768,1024,2048):
            raise ValueError('RGB image size must be 512, 768, 1024 or 2048')
        self.rgb_jpeg_quality=int(os.environ.get('MAS_RGB_JPEG_QUALITY','92'))
        if self.rgb_jpeg_quality not in range(70,96):
            raise ValueError('RGB JPEG quality must be between 70 and 95')
        # Preserve the current four simultaneous virtual head cameras.
        # Their private depth is used only to ground the selected RGB pixel.
        # Put the model-facing cameras ahead of the robot head. The steeper
        # pitch keeps nearby floor targets in frame at navigation distance.
        self.rig_radius=0.35
        self.rig_pitch_degrees=35.0
        self.capture_index=0
        self.image_files={}
        self.current_frames={}
        self._sensor_packets={}
        self._sensor_intrinsics={}
        self.rig = {}
        super().__init__(*args,**kwargs)
        base_z = float(self.robot.get_position_orientation()[0][2])
        floor_z = min(self.env.scene.trav_map.floor_heights, key=lambda z: abs(z-base_z))
        self._base_floor_clearance = base_z-floor_z
        self._demo_trunk_home = (self.robot.get_joint_positions()[self.robot.trunk_control_idx].clone()
                                 if self.demo_motion else None)
        # Native symbolic operations do not command trunk motion. Hold the
        # restored posture through the position controller instead of adopting
        # each load-induced deflection as the next no-op target. Physics still
        # owns the measured joints; no pose is reset during stepping.
        self._trunk_hold_target = (None if self.demo_motion else
            self.robot.get_joint_positions()[self.robot.trunk_control_idx].clone())
        (self.output/'trunk_control.json').write_text(json.dumps({
            'policy': 'explicit_demo_motion' if self.demo_motion else 'fixed_restored_position_target',
            'target': None if self._trunk_hold_target is None else self._trunk_hold_target.tolist(),
            'actuation': 'ordinary_position_controller', 'per_step_joint_pose_writes': False,
        }, indent=2)+'\n')
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
        self.rig_height = robot_head_mount_height(
            self.robot,
            pitch_degrees=self.rig_pitch_degrees, radius=self.rig_radius)
        # Use the grasp posture for the first observation and navigation.
        with self._startup_stage('initialize_manipulation_arm'):
            self._initialize_manipulation_arm()
        with self._startup_stage('surround_camera_setup_and_warmup'):
            for direction in DIRECTIONS:
                sensor = self._preloaded_rgb_sensor('mas_rgb_'+direction)
                if sensor is None:
                    sensor = VisionSensor(relative_prim_path='/mas_rgb_'+direction, name='mas_rgb_'+direction,
                        modalities=['rgb','depth_linear'],image_width=self.image_size,image_height=self.image_size,
                        focal_length=10.0,horizontal_aperture=20.0,viewport_name=None)
                    with self._startup_stage(f'surround_camera_{direction}_load'):
                        sensor.load(None)
                    with self._startup_stage(f'surround_camera_{direction}_initialize'):
                        sensor.initialize()
                else:
                    with self._startup_stage(f'surround_camera_{direction}_reuse_preloaded'):
                        pass
                # Otherwise intrinsic_matrix lazily attaches a new annotator AFTER
                # RGB readback and warms only four frames, sometimes returning a
                # degenerate projection. Attach all before the shared barrier.
                with self._startup_stage(f'surround_camera_{direction}_camera_params'):
                    sensor.initialize_sensors(names='camera_params')
                if self.renderer_pixel_owner:
                    # OG's seg_instance also enables semantic segmentation. In
                    # Isaac Sim 5.1 its OgnInstanceSegmentation node can crash
                    # while indexing the semantic mapping during render. Pixel
                    # ownership needs only the renderer's mesh path, not that
                    # semantic grouping or OG's particle/category remapping.
                    import omnigibson.lazy as lazy
                    with self._startup_stage(f'surround_camera_{direction}_private_owner'):
                        with self.og.sim.editing_usd():
                            annotator = lazy.omni.replicator.core.AnnotatorRegistry.get_annotator(
                                'instance_id_segmentation_fast')
                            annotator.attach([sensor.render_product])
                        self._renderer_owner_annotators[direction] = annotator
                self.rig[direction] = sensor
            self._position_rig()
            for frame in range(20):
                with self._startup_stage(f'surround_camera_warmup_frame_{frame + 1:02d}'):
                    self.og.sim.render()
        with self._startup_stage('spectator_camera_setup_and_warmup'):
            if self.record_video:
                from ..reporting.video import EpisodeVideo
                from omnigibson.sensors import VisionSensor
                # Use an independent offscreen render product at its final size. Do
                # not resize/destroy the shared GUI viewer product: it invalidates
                # robot RGB annotators in the pinned headless runtime.
                self.spectator = self._preloaded_rgb_sensor('mas_spectator')
                if self.spectator is None:
                    self.spectator = VisionSensor(relative_prim_path='/mas_spectator',name='mas_spectator',
                                                  modalities=['rgb'],image_width=self.image_size,
                                                  image_height=self.image_size,viewport_name=None)
                    with self._startup_stage('spectator_camera_load'):
                        self.spectator.load(None)
                    with self._startup_stage('spectator_camera_initialize'):
                        self.spectator.initialize()
                else:
                    with self._startup_stage('spectator_camera_reuse_preloaded'):
                        pass
                self.video = EpisodeVideo(self.output, fps=1.0/self.og.sim.get_sim_step_dt(),size=self.image_size,
                                          views=(*DIRECTIONS,'spectator'),demo_motion=self.demo_motion)
                self._position_spectator()
                for frame in range(20):
                    with self._startup_stage(f'spectator_camera_warmup_frame_{frame + 1:02d}'):
                        self.og.sim.render()

    def _position_rig(self):
        """Kinematic sensor mount only: never writes robot pose or advances physics."""
        import omnigibson.utils.transform_utils as T
        base_pos, base_quat = self.robot.get_position_orientation()
        rotation = T.quat2mat(base_quat)
        for direction, sensor in self.rig.items():
            offset, basis = camera_mount(direction, self.rig_height, radius=self.rig_radius,
                                         pitch_degrees=self.rig_pitch_degrees)
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
        if getattr(self, '_spectator_log', None) is None:
            self._spectator_log = JsonlAppend(self.output/'spectator_poses.jsonl')
        self._spectator_log.append({'env_step':self.steps,'position':camera.tolist(),'orientation':orientation.tolist(),
                                   'look_at':target.tolist(),'clearance_m':clearance,'audience':'offline_only'})

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
        self._check_renderer_health()
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
        self._check_renderer_health()
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
        self._check_renderer_health()
        import numpy as np
        self._position_rig()
        sensors = dict(self.rig)
        if include_spectator: sensors['spectator'] = self.spectator
        # Flush render-product latency after camera poses change, with physics frozen.
        for _ in range(flushes - 1):
            with component(self,'render'):self.og.sim.render()
            self._check_renderer_health()
        shapes = {}; readiness_failures=[]
        for attempt in range(30):
            with component(self,'render'):self.og.sim.render()
            self._check_renderer_health()
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
                        if getattr(self,'renderer_pixel_owner',False):
                            with component(self,'sensor_readback'):
                                owner=self._renderer_owner_annotators[view].get_data()
                            ids=owner.get('data') if isinstance(owner,dict) else None
                            labels=owner.get('info',{}).get('idToLabels') if isinstance(owner,dict) else None
                            if (ids is None or tuple(ids.shape)!=(self.image_size,self.image_size)
                                or not isinstance(labels,dict)):
                                pending[view]='private_instance_render_product_not_ready'
                                continue
                            # Copy while the same render barrier is still in
                            # force. The model receives only the RGB packet.
                            data['_renderer_instance_ids']=self.torch.as_tensor(ids).clone()
                            info['_renderer_instance_labels']={str(k):v for k,v in labels.items()}
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
        self._check_renderer_health()
        self.deadline.check(changed=True)
        local=getattr(self,'_local_operation',None)
        if local is not None:local.check()
        if self._base_target is not None:
            target, self._base_target = self._base_target, None
            self._transfer_base_pose(target['position'],target['orientation'])
        trunk_target = getattr(self, '_trunk_hold_target', None)
        if trunk_target is not None:
            action = action.clone()
            action[self.robot.controller_action_idx['trunk']] = trunk_target.to(action)
        # RGB observation boundaries and the video cadence already own rendering.
        # OmniGibson's ordinary env.step also renders implicitly, duplicating
        # that work. Its native no-render branch advances the same physics
        # substeps, state transitions, task evaluation and simulation clock.
        # Restore explicitly even on exceptions: OG 3.9.2's context manager
        # does not put its own restoration in a finally block.
        implicit_render = self.og.sim._render_on_step
        try:
            with self.og.sim.render_on_step(False):
                with component(self,'physics_and_metrics'):super()._step(action)
        finally:
            self.og.sim._render_on_step = implicit_render
        try:
            self._check_placement_objects()
        finally:
            # Physics already advanced, including when a transition replaced a
            # watched object. Keep that executed step in the continuous replay.
            self._video_frame('env_step')

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
            arrays={'depth_linear':frame['depth_linear'].numpy()}
            if getattr(self,'renderer_pixel_owner',False):
                frame['instance_ids']=data['_renderer_instance_ids'].detach().cpu().clone()
                frame['instance_labels']=dict(info['_renderer_instance_labels'])
                arrays['instance_ids']=frame['instance_ids'].numpy()
            np.savez_compressed(private_folder/f'{ref}.npz',**arrays)
            (private_folder/f'{ref}.json').write_text(json.dumps({'audience':'executor_private_only',
                'image_ref':ref,'rgb_sha256':digest,
                'position':position.tolist(),'orientation':orientation.tolist(),'intrinsic':frame['intrinsic'].tolist(),
                **({'instance_labels':frame['instance_labels']} if 'instance_labels' in frame else {})}))
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

    def _ground(self,target, *, require_object=True, reject_particles=False):
        """Use the chosen pixel's depth backprojection directly as the target.

        Navigation needs only this point. Manipulation resolves visual geometry
        along this pixel's original camera ray at the measured depth, solely for
        its simulator handle. The point is unchanged; no alternate pixel,
        semantic target search or task-scope lookup occurs.
        """
        import omnigibson.utils.transform_utils as T
        frame=self.current_frames.get(target['image_ref'])
        if frame is None: raise SkillError('stale_image_ref','Expired RGB capture')
        x,y=target['point'];K=frame['intrinsic'].cpu()
        px=round(x*(frame['width']-1));py=round(y*(frame['height']-1))
        # USD cameras look along -Z; image down corresponds to camera -Y.
        # Replicator samples pixel centers; array indices denote pixel corners.
        local=self.torch.tensor([(px+.5-float(K[0,2]))/float(K[0,0]),
                                 -(py+.5-float(K[1,2]))/float(K[1,1]),-1.0])
        start=frame['position'].cpu()
        depth=float(frame['depth_linear'][py,px])
        if not math.isfinite(depth) or not 0<depth<30:
            raise SkillError('no_surface_at_point','No supported visible surface at selected pixel')
        direction=T.quat2mat(frame['orientation'].cpu()) @ local
        point=start+direction*depth
        diagnostic={'at':now(),'routing':'direct_pixel_depth_backprojection',
            'depth_linear':depth,'hit_position':point.tolist(),'depth_consistency_check':False,
            'object_resolution_required':require_object,'selected_pixel':target['point'],
            'raster_pixel':[px,py],'pixel_center':[px+.5,py+.5],
            'image_ref':target['image_ref'],'audience':'executor_private'}
        obj=None
        if require_object:
            from .visual_mesh_grounding import query_visual_surface
            ownership={}
            if 'instance_ids' in frame and 'instance_labels' in frame:
                label=frame['instance_labels'].get(str(int(frame['instance_ids'][py,px])))
                if isinstance(label,str):
                    # Raw renderer IDs identify visual meshes below the object
                    # root. Match an ancestor at a path boundary, so e.g. obj_1
                    # cannot claim pixels rendered by obj_10.
                    matches=[o for o in [*self.env.scene.objects,self.robot]
                             if label in (o.name,str(o.prim_path))
                             or label.startswith(str(o.prim_path)+'/')]
                    selected=max(matches,key=lambda o:len(str(o.prim_path)),default=None)
                    if selected is not None:ownership['renderer_object']=selected
            try:
                with component(self,'visual_grounding'):
                    obj,visual=query_visual_surface(self,point,reject_particles=reject_particles,
                                                    camera_position=start,**ownership)
            except SkillError as exc:
                diagnostic.update(status='failed',code=exc.code,detail=str(exc),
                                  visual_query=getattr(exc,'diagnostics',{}))
                with (self.output/'grounding_diagnostics.jsonl').open('a') as stream:stream.write(json.dumps(diagnostic)+'\n')
                raise
            diagnostic.update(visual,selected_object_prim=obj.prim_path)
        with (self.output/'grounding_diagnostics.jsonl').open('a') as stream:stream.write(json.dumps(diagnostic)+'\n')
        return obj,point,diagnostic

    def _turn(self,degrees,max_steps):
        from .visual_target import rotate
        pos,quat=self.robot.get_position_orientation()
        forward=rotate((1.,0.,0.),quat)
        yaw=math.atan2(forward[1],forward[0])+math.radians(degrees)
        return self._execute_base_path([pos[:2].cpu().tolist()],yaw,max_steps)

    def _navigate(self, target, max_steps, *, for_manipulation=False):
        """Select one walkable destination and call OmniGibson's native pathfinder."""
        import cv2
        import numpy as np
        from .compat.native_astar import sync_added_fixture_map, sync_open_door_map
        opened_doors = sync_open_door_map(self)
        added_fixtures = sync_added_fixture_map(self)
        trav=self.env.scene.trav_map
        position=self.robot.get_position_orientation()[0]
        point=target.get_position_orientation()[0]
        floor=min(range(len(trav.floor_heights)),key=lambda i:abs(float(position[2])-trav.floor_heights[i]))
        start=position[:2].cpu();hint=point[:2].cpu()
        floor_click=not for_manipulation and float(point[2])-trav.floor_heights[floor]<=.15
        # Objects need a nearby standing position; a floor click is the actual
        # destination and must not be projected back into the start's region.
        occupancy=trav._erode_trav_map(trav.floor_map[floor].clone(),robot=self.robot).cpu().numpy()
        source=tuple(trav.world_to_map(start).tolist())
        if not (0<=source[0]<occupancy.shape[0] and 0<=source[1]<occupancy.shape[1]):
            raise SkillError('navigation_invalid_start','Start is outside the native map')
        # Native A* seeds the actual start even in a blocked cell, then admits
        # traversable eight-connected neighbors. Match that graph for selection
        # without changing the native map or making the start a destination.
        connected=occupancy.astype('uint8')
        connected[source]=255
        _,labels=cv2.connectedComponents(connected,connectivity=8)
        cells=np.column_stack(np.where((labels==labels[source]) & (occupancy!=0)))
        if not len(cells):
            raise SkillError('navigation_invalid_start','No native traversable neighbor at the start')
        reach_fallback=None;floor_snap=None
        if floor_click:
            endpoint=hint
            clicked=tuple(trav.world_to_map(hint).tolist())
            walkable=(0<=clicked[0]<occupancy.shape[0] and 0<=clicked[1]<occupancy.shape[1]
                      and occupancy[clicked]!=0)
            if not walkable:
                # A floor spot under a chair or beside a wall is a real place the
                # base cannot occupy. Go to the closest connected walkable cell
                # and report the move as incomplete. A walkable click in another,
                # disconnected region is still refused by the pathfinder below.
                points=trav.map_to_world(self.torch.as_tensor(cells,dtype=start.dtype)+.5)
                distances=self.torch.linalg.norm(points-hint,dim=1)
                nearest=int(self.torch.argmin(distances))
                if float(distances[nearest])>self.local_limits.floor_click_snap_m:
                    raise SkillError('navigation_unreachable','Selected floor point is not walkable and no walkable floor is near it')
                endpoint=points[nearest];floor_snap=float(distances[nearest])
        else:
            # Use cell centers for object approaches. Both explicit navigation
            # and local manipulation must stay near the selected object.
            points=trav.map_to_world(self.torch.as_tensor(cells,dtype=start.dtype)+.5)
            distances=self.torch.linalg.norm(points-hint,dim=1)
            within=distances<=self.local_limits.reach_m
            if bool(within.any()):
                points=points[within];distances=distances[within]
                scores=abs(distances-1.25)
            else:
                # The eroded static map can keep the base farther from a deep
                # table or a furnished corner than the nominal reach. Stand at
                # the closest walkable cell instead of refusing the target;
                # the wider bound applies only when no nearer cell exists.
                within=distances<=self.local_limits.fallback_reach_m
                points=points[within];distances=distances[within]
                if not len(points):
                    raise SkillError('navigation_unreachable','No native walkable destination within target reach')
                scores=distances
                reach_fallback=float(distances.min())
            near=scores<=scores.min()+trav.map_resolution
            points=points[near]
            endpoint=points[self.torch.argmin(self.torch.linalg.norm(points-start,dim=1))]
            operation=getattr(self,'_local_operation',None)
            if reach_fallback is not None and operation is not None:
                operation.reach_m=min(self.local_limits.fallback_reach_m,reach_fallback+2*float(trav.map_resolution))
        record={'at':now(),'parent_request_id':getattr(self,'_active_request_id',None),
                'audience':'executor_private','start':start.tolist(),'target':point.cpu().tolist(),
                'endpoint':endpoint.tolist(),'strategy':'omnigibson_native_shortest_path',
                'floor':floor,'mode':'point' if floor_click else 'approach',
                'resolution_m':float(trav.map_resolution),'precomputed_walkability':True,
                'map_sha256':hashlib.sha256(occupancy.tobytes()).hexdigest(),
                'native_map_open_doors':opened_doors,'native_map_added_fixtures':added_fixtures,
                'reach_fallback_m':reach_fallback,'floor_click_snap_m':floor_snap,
                'dynamic_collision_check':False}
        try:
            self.deadline.check()
            path,length=trav.get_shortest_path(floor,start,endpoint,entire_path=True,robot=self.robot)
            if path is None:
                raise SkillError('navigation_unreachable','Native pathfinder found no route to the destination')
            route=[start.tolist(),*path.cpu().tolist(),endpoint.tolist()]
            heading=hint-(start if floor_click and floor_snap is None else endpoint)
            yaw=math.atan2(float(heading[1]),float(heading[0]))
            record.update(status='planned',path=route,path_distance_m=float(length))
            result=self._execute_base_path(route,yaw,max_steps)
            self.navigation_distance+=result['actual_path_distance_m']
            actual=self.robot.get_position_orientation()[0][:2].cpu()
            target_distance=float(self.torch.linalg.norm(actual-hint))
            tolerance=(trav.map_resolution if floor_click else self.local_limits.reach_m if reach_fallback is None
                       else min(self.local_limits.fallback_reach_m,reach_fallback+2*float(trav.map_resolution)))
            nav_status=('reached' if floor_click else 'approached') if target_distance<=tolerance else 'incomplete'
            return {**result,'strategy':record['strategy'],'path_distance_m':float(length),
                    'nav_status':nav_status,
                    'dynamic_collision_check':False,'precomputed_walkability':True}
        except SkillError as exc:
            record.update(status='failed',error_code=exc.code,error=str(exc))
            raise
        finally:
            with (self.output/'navigation_plans.jsonl').open('a') as stream:
                stream.write(json.dumps(record)+'\n')

    def _transfer_base_pose(self, position, orientation):
        """One symbolic state transfer of the robot and its currently held load."""
        from omnigibson.utils import transform_utils as T
        from .particle_payload import snapshot_particles, relocate_particles
        held=self._get_held()
        objects=[] if held is None else [held,*[child for child,_,_ in self._grasp_dependencies(held)]]
        # Robot.set_position_orientation already transports the native AG
        # object. Carry only its loose contents across this symbolic change.
        relative=[] if held is None else [(obj,T.relative_pose_transform(
            *obj.get_position_orientation(),*held.get_position_orientation())) for obj in objects[1:]]
        particles=snapshot_particles(self.env.scene,objects,include_supported=True) if objects else []
        self.robot.set_position_orientation(position,orientation)
        for obj,pose in relative:
            obj.set_position_orientation(*T.pose_transform(*held.get_position_orientation(),*pose))
        relocate_particles(self.env.scene,particles)

    def _execute_base_path(self, points, end_yaw, max_steps):
        """Set the symbolic endpoint once and advance one ordinary environment step."""
        import omnigibson.utils.transform_utils as T
        if max_steps<1:raise SkillError('action_timeout','No environment step available for navigation')
        self.deadline.check()
        position,orientation=self.robot.get_position_orientation()
        goal=position.clone();goal[:2]=position.new_tensor(points[-1])
        # A prior contact can lift or tip the base. Transport to the floor,
        # rather than carrying that transient elevation to the next endpoint.
        floor_z=min(self.env.scene.trav_map.floor_heights,key=lambda z:abs(z-float(position[2])))
        goal[2]=floor_z+self._base_floor_clearance
        quat=T.euler2quat(orientation.new_tensor([0.,0.,end_yaw]))
        local=getattr(self,'_local_operation',None)
        if local is not None:local.admit_pose((*points[-1],end_yaw))
        before=self.steps
        self._transfer_base_pose(goal,quat)
        # This updates native physics, object states and evaluation once. There
        # is no fixed settling window, endpoint rejection or automatic retry.
        self._step(self.primitives._empty_action())
        actual,actual_quat=self.robot.get_position_orientation()
        displacement=math.dist(position[:2].cpu().tolist(),actual[:2].cpu().tolist())
        route_distance=sum(math.dist(a,b) for a,b in zip(points,points[1:]))
        with (self.output/'base_motion.jsonl').open('a') as stream:
            stream.write(json.dumps({'at':now(),'env_step':self.steps,'audience':'executor_private',
                'commanded_position':goal.tolist(),'actual_position':actual.tolist(),
                'actual_orientation':actual_quat.tolist(),'commanded_yaw':end_yaw,
                'position_error_m':math.dist(actual[:2].cpu().tolist(),points[-1]),
                'planned_route_distance_m':route_distance,'continuous_motion_simulated':False,
                'follower':'symbolic_route_endpoint'})+'\n')
        return {'steps':self.steps-before,'motion_steps':self.steps-before,
                'motor':'symbolic_route_endpoint','nav_status':'endpoint_applied',
                'actual_path_distance_m':displacement,'planned_route_distance_m':route_distance,
                'distance_measure':'observed_endpoint_displacement; continuous_path_not_executed',
                'continuous_motion_simulated':False,'physical_controller':False}

    @action_profile
    def execute_visual(self,primitive,target,max_steps,**kwargs):
        before=self.steps
        complete=getattr(self,'_complete_pending_object_initialization',None)
        try:
            result=RGBBackend._execute_visual(self,primitive,target,max_steps,**kwargs)
        except SkillError:
            if complete is not None:complete(max_steps-(self.steps-before))
            raise
        # Native transitions register replacement objects at the end of
        # env.step; their states initialize on the next step. An aborted
        # placement used to expose those objects to the next grasp. Finish
        # the lifecycle after action contexts unwind, including tool errors.
        # Do not attempt physics recovery after an unexpected native failure.
        if complete is not None:complete(max_steps-(self.steps-before))
        return result

    def _complete_pending_object_initialization(self, remaining_steps=8):
        from .scene_lifecycle import finish_pending_objects
        finish_pending_objects(self, remaining_steps)

    def _execute_visual(self,primitive,target,max_steps,**kwargs):
        if primitive in {'look','navigate_to','release','wait'} and getattr(self,'_demo_focus',None) is not None:
            self._demo_focus=None;self._spectator_anchor=None
        if primitive=='look':return self._turn(kwargs['yaw_degrees'],max_steps)
        if primitive=='wait':
            count=min(max_steps,int(round(float(kwargs.get('seconds',5.0))/self.og.sim.get_sim_step_dt())))
            for _ in range(count):self._step(self.primitives._empty_action())
            return {'primitive':'wait','steps':count,'sim_seconds':count*self.og.sim.get_sim_step_dt()}
        if primitive=='release':
            return self.execute(primitive,None,max_steps)
        obj,point,grounding=self._ground(target,require_object=primitive!='navigate_to',reject_particles=primitive=='grasp')
        if primitive=='navigate_to':
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),selected_object=obj)
            return {**self._navigate(anchor,max_steps),'private_grounding':grounding}
        if getattr(self,'local_manipulation',False):
            return self._execute_local_manipulation(primitive,obj,point,grounding,max_steps,kwargs)
        return self._execute_bound_manipulation(primitive,obj,point,grounding,max_steps,**kwargs)

    def _manipulation_preconditions(self,primitive,obj):
        if obj is None or not hasattr(obj,'states'):
            raise SkillError('invalid_visual_target','No manipulable object at selected pixel')
        held=self._get_held()
        if primitive=='fold':self._fold_preconditions(obj)
        if primitive=='grasp' and obj.fixed_base: raise SkillError('fixed_object','Fixed object')
        if primitive=='grasp' and held is not None and held is not obj: raise SkillError('hand_occupied','Hand occupied')
        if primitive in {'attach','hang','place_inside','place_on_top','place_under','place_next_to','wipe','sweep','vacuum',
                         'spray','spread','soak','cut','pour','scoop'} and held is None: raise SkillError('empty_hand','Empty hand')
        if held is not None and primitive in {'open','close','toggle_on','toggle_off'}:
            # Controlled carry can operate the switch on the object already
            # being carried. This does not grant an extra hand for a different
            # object, or change articulated-container handling while carrying.
            held_self_toggle=held is obj and primitive in {'toggle_on','toggle_off'}
            if not held_self_toggle:raise SkillError('hand_occupied','Hand occupied')
        from omnigibson.object_states import Open,Inside
        if primitive=='place_inside' and Open in obj.states and not obj.states[Open].get_value():
            raise SkillError('container_closed','Placement into closed container rejected')
        if primitive=='grasp':
            from .container_support import closed_container
            if closed_container(obj,self.env.scene.objects) is not None:
                raise SkillError('container_closed','Grasp through closed container rejected')
        if getattr(self,'local_manipulation',False):
            from omnigibson.object_states import ToggledOn,AttachedTo,OnTop,Under,NextTo
            state={'open':Open,'close':Open,'toggle_on':ToggledOn,'toggle_off':ToggledOn}.get(primitive)
            if state is not None and state not in obj.states:
                raise SkillError('pre_condition_error','Selected object does not support this operation',
                                 stage='unsupported_object_action')
            if primitive in {'place_inside','place_on_top','place_under','place_next_to'}:
                relation={'place_inside':Inside,'place_on_top':OnTop,'place_under':Under,'place_next_to':NextTo}[primitive]
                if held is obj:
                    raise SkillError('same_placement_object','Placement reference is the carried object')
                if relation not in held.states:
                    raise SkillError('unsupported_relation','Selected carry/destination does not support this relation')
                if primitive=='place_inside' and not any(link.is_meta_link and link.meta_link_type in {'fillable','openfillable'} for link in obj.links.values()):
                    raise SkillError('unsupported_relation','Selected destination has no fillable volume')
            if primitive in {'attach','hang'}:
                if held is obj or AttachedTo not in held.states or AttachedTo not in obj.states:
                    raise SkillError('unsupported_relation','Selected objects do not support attachment')
                if primitive=='hang' and not any(word in str(obj.category).lower() for word in ('nail','hook','hanger')):
                    raise SkillError('unsupported_relation','Selected object is not a hanging anchor')
                if held.states[AttachedTo].parent is not obj:
                    links=held.states[AttachedTo]._find_attachment_links(obj,bypass_alignment_checking=True)
                    if any(link is None for link in links):raise SkillError('unsupported_relation','No compatible attachment link')
            if primitive in {'wipe','sweep','vacuum','spray','spread'}:
                self._surface_action_preconditions(primitive,obj)
            if primitive=='soak':self._soak_preconditions(obj)
            if primitive in {'pour','scoop'}:self._transfer_preconditions(primitive,obj)
            if primitive=='cut':
                if 'slicer' not in held._abilities:
                    raise SkillError('cutting_tool_required','Held object does not support cutting')
                if not {'sliceable','diceable'}.intersection(obj._abilities):
                    raise SkillError('cut_target_unsupported','Selected target does not support cutting')

    def _execute_bound_manipulation(self,primitive,obj,point,grounding,max_steps,**kwargs):
        """Execute the same selected object/surface, without recursive act/grounding."""
        self._manipulation_preconditions(primitive,obj)
        held=self._get_held()
        # The local controller already handles reach. Legacy explicit mode
        # needs the same operation range here, without implicit movement.
        if not getattr(self,'local_manipulation',False):
            base=self.robot.get_position_orientation()[0]
            if float(self.torch.linalg.norm(base[:2]-point[:2]))>1.4:
                raise SkillError('out_of_reach',
                                 'Selected surface is beyond manipulation reach; call navigate_to, '
                                 'then reselect the target in the returned RGB before retrying')
        if primitive=='grasp' and held is None and getattr(self,'demo_motion',False):
            self._demo_arm=None
        # A carried slicer may trigger the official rule during an arm
        # demonstration step. Save identity/output metadata while the selected
        # object is alive, and resolve replacement before touching it again.
        cut_target = self._capture_cut_target(obj) if primitive == 'cut' else None
        def completed_cut(stage, demo_steps=0):
            if cut_target is None:
                return None
            result = self._cut_replacement_result(cut_target, stage)
            if result is None:
                return None
            if demo_steps:
                result = {**result, 'demo_motion': {'real_env_steps': demo_steps, 'ideal_contact': True}}
            return {**result, 'private_grounding': grounding}
        # A cut target must be free to be destroyed by its legitimate transition;
        # holding its root pose after env.step would dereference a deleted prim.
        shown = (self._demo_reach(point,max_steps,anchor=None if obj is held or primitive == 'cut' else obj,
                                  arm=self._demo_arm if held is not None else None,
                                  action=primitive)
                 if getattr(self,'demo_motion',False) else 0)
        max_steps -= shown
        completed = completed_cut('demo_reach', shown)
        if completed is not None:
            return completed
        contact_limit=(.45 if primitive=='spray' else .30 if primitive=='vacuum' else .18)
        if shown and self._demo_last_reach_error>contact_limit:
            raise SkillError('out_of_reach',
                             'Robot hand could not reach the selected surface from this base pose; '
                             'call navigate_to, then reselect the target in fresh RGB',
                             changed=True)
        if shown and primitive in {'wipe','sweep','vacuum','spray','spread','soak','cut'}:
            stroke=point.clone()
            if primitive in {'soak','cut'}:
                stroke[2]-=.06
            else:
                stroke[0]+=.08
            stroke_steps=self._demo_reach(stroke,max_steps,anchor=None if primitive == 'cut' else obj,
                                          arm=self._demo_arm,action=primitive+'_stroke')
            shown+=stroke_steps;max_steps-=stroke_steps
            completed = completed_cut('demo_stroke', shown)
            if completed is not None:
                return completed
        before_effect_steps=self.steps if shown else 0
        if primitive=='grasp':
            result=self._grasp_state(obj,max_steps,point)
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
        elif primitive in {'pour','scoop'}:
            result=self._checked_transfer(primitive,obj,max_steps,point)
        elif primitive=='cut':
            result=self._checked_cut(obj,max_steps)
        elif primitive=='fold':
            result=self._checked_fold(obj,max_steps)
        elif primitive in {'open','close','toggle_on','toggle_off'}:
            result=self._state_action(primitive,obj,max_steps)
        elif primitive in {'place_on_top','place_inside','place_under','place_next_to'}:
            result=self._place_state(primitive,obj,max_steps,point,kwargs.get('placement_yaw_degrees'),
                                     roll_degrees=kwargs.get('placement_roll_degrees'))
        else:
            # Native symbolic actions own their state change and settling.
            result=self.execute(primitive,obj.name,max_steps)
        if shown and primitive in {'place_inside','place_on_top','place_under','place_next_to','attach','hang','open','close',
                                   'toggle_on','toggle_off'}:
            # A stretched arm must not sweep the destination over when the
            # model's next navigation action moves the base away.
            shown+=self._demo_transport_pose(max_steps-(self.steps-before_effect_steps))
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
        result=super().provenance()
        result.update(executor='native_grasp_and_state_actions_plus_gt_navigation',
                      observation_mode=self.mode,image_size=self.image_size,demo_motion=getattr(self,'demo_motion',False),
                      grounding='direct_pixel_depth_backprojection_private_executor_only',
                      model_visible_truth=False)
        result['grounding_protocol'] = {'position':'same_pixel_depth_linear_backprojection',
            'depth_consistency_check':False, 'navigation_object_lookup':False,
            'pixel_sample':'renderer_pixel_center_index_plus_half',
            'manipulation_object_lookup':'same_pixel_visual_ray_at_measured_depth',
            'coincident_surface_resolution':('same_frame_renderer_instance_within_float32_roundoff'
                if getattr(self,'renderer_pixel_owner',False) else 'camera_frontmost_within_float32_roundoff'),
            'private_instance_labels':getattr(self,'renderer_pixel_owner',False),
            'private_owner_annotator':('instance_id_segmentation_fast'
                if getattr(self,'renderer_pixel_owner',False) else None),
            'mesh_hit_replaces_backprojected_point':False, 'robot_surface_selection_rejected':True}
        result['grasp_protocol'] = {'mode':'native_assisted_grasp_joint', 'fixed_joint':True,
            'collision_and_gravity_disabled':False, 'payload_transfer':'once_at_state_change',
            'pose_projection_during_physics':False, 'released_objects':'native_physics'}
        result['manipulation_navigation'] = {'implicit_navigation':getattr(self,'local_manipulation',False),
            'protocol':'target_locked_single_approach_v3' if getattr(self,'local_manipulation',False) else 'explicit_navigation_v1',
            'local_limits':__import__('dataclasses').asdict(self.local_limits) if getattr(self,'local_manipulation',False) else None,
            'map':'native_traversability_with_door_state_updates','resolution_m':float(self.env.scene.trav_map.map_resolution),
            'local_search':'native_shortest_path_to_target_reach','max_approaches':1,
            'dynamic_collision_check':False,'departure_check':False,'settling_verification':False,
            'target_lock':'private_selected_link_relative_surface',
            'scope':'GT-assisted symbolic endpoint; no continuous driving or real-arm IK',
            'reach_check':'base_to_selected_point_xy','reach_limit_m':self.local_limits.reach_m,
            'reach_fallback':'closest walkable cell when none lies within the nominal reach',
            'reach_fallback_limit_m':self.local_limits.fallback_reach_m,
            'floor_click_snap':'closest connected walkable cell when the clicked floor cell is not walkable; reported incomplete',
            'floor_click_snap_limit_m':self.local_limits.floor_click_snap_m}
        result['record_video'] = self.record_video
        result['video_capture_policy'] = {'render_stride':self.video_render_stride,
            'video_render_flushes':self.video_render_flushes, 'observation_render_flushes':4,
            'observation_pixels_reused_for_video':True, 'fresh_observation_boundaries':True,
            'intermediate_frames':'explicit_previous_frame_hold','no_motion_interpolation':True}
        result['control_step_rendering'] = {
            'implicit_render':False, 'explicit_render':'observation_and_video_boundaries',
            'physics_substeps':'native_unchanged', 'task_and_state_updates':'native_unchanged',
            'exception_policy':'restore_previous_implicit_render_setting'}
        result['placement'] = {'on_top':'single_geometric_release_and_free_settling',
            'inside':'single_geometric_release_and_free_settling','verification':'selected_surface_support_and_physical_settlement; official_Inside_and_payload_relations',
            'surface_target':'RGB selected point and shelf retained; local candidate search',
            'surface_geometry':'object collision mesh; no diagonal-square yaw envelope',
            'base_anchor':None,'released_body_pinning':False,
            'failure_policy':'retain_committed_physical_result',
            'candidate_sampling':{'on_top_inside_and_next_to':'read_only_geometry_no_physics_trials_or_rollback'},
            'goal_access':False}
        result['goal_evaluation_optimization'] = 'interned_literals_in_one_grounding_call; predicate_cache_within_one_read_only_scoring_pass; official_formula_unchanged'
        result['base_execution'] = 'native_map_route_then_single_endpoint_transfer_and_one_native_step'
        result['navigation'] = {'strategy':'omnigibson_native_shortest_path',
            'map':'OmniGibson scene traversability',
            'map_resolution_m':float(self.env.scene.trav_map.map_resolution),
            'erosion':'native robot reset-pose AABB; no carried-item expansion',
            'search':'OmniGibson TraversableMap.get_shortest_path',
            'localization':'private simulator pose','precomputed_walkability':True,
            'door_state_sync':'opened doorway footprints only; closed doors retain the native map',
            'added_fixture_sync':'footprints of task-declared fixed supports are blocked',
            'online_mapping':False,'dynamic_collision_check':False,
            'continuous_motion_simulated':False,'physical_controller':False,
            'departure_certificate':False,'endpoint_settling_gate':False,
            'physics_steps_after_transfer':1,'model_visible_gt':False}
        result['robot_camera_views'] = list(DIRECTIONS)
        result['private_grounding_provider']=('same-pixel visual ray at measured depth; same-capture private instance labels resolve numerical ties'
            if getattr(self,'renderer_pixel_owner',False) else
            'same-pixel visual ray at measured depth; no segmentation annotator')
        result['surround'] = 'four_fixed_cameras_one_simulation_state_no_robot_rotation'
        result['camera_rig'] = {'horizontal_fov_degrees':90,'pitch_down_degrees':self.rig_pitch_degrees,
            'mount_radius_m':self.rig_radius,'mount_height_m':self.rig_height,'views':list(DIRECTIONS),
            'height_policy':'initial_visual_head_and_body_excluding_arms_and_hands_with_5cm_clearance',
            'stock_wrist_cameras_enabled':False}
        return result

    def close(self):
        try:
            self.finalize_video()
        finally:
            try:
                self._close_placement_record()
            finally:
                try:
                    if getattr(self, '_spectator_log', None) is not None:
                        self._spectator_log.close()
                finally:
                    super().close()
