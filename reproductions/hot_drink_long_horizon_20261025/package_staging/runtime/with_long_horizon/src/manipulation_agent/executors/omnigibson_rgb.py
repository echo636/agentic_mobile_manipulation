"""RGB observer + private pixel-ray motor adapter for pinned OmniGibson 3.9.2."""
from __future__ import annotations
import hashlib
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from ..contracts import SkillError
from ..deadline import EpisodeDeadline
from ..omnigibson_backend import OmniGibsonBackend
from ..observations.rig import DIRECTIONS, camera_mount, centered_head_height, look_at_orientation
from ..records import now
from .placement import CheckedPlacement
from .profiling import component, action_profile
from .carry import ControlledCarry, bulk_grasp_reason
from .material_actions import CheckedMaterialActions
from .demo_motion import DemoMotion
from ..high_level_hot_drink import HighLevelHotDrink


def env_flag(name):
    """Read a boolean environment switch without treating the string '0' as true."""
    return os.environ.get(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def ray_hits_selected_object(hit, selected_object):
    """An early ray hit on another part of the selected object is still visible."""
    path = getattr(selected_object, 'prim_path', None)
    body = hit.get('rigidBody') if isinstance(hit, dict) else None
    return bool(path and isinstance(body, str) and
                (body == path or body.startswith(path.rstrip('/') + '/')))


def stable_spectator_candidate(candidates, previous_choice, *, same_request):
    """Keep one filming side through an action unless its view collapses."""
    if not candidates:
        raise ValueError('At least one spectator pose is required')
    if same_request:
        previous=next((row for row in candidates if row[3]==previous_choice),None)
        if previous is not None and previous[2]>=.65:
            return previous
    return max(candidates,key=lambda row:row[0])


class RGBBackend(DemoMotion, CheckedMaterialActions, ControlledCarry, CheckedPlacement, OmniGibsonBackend):
    mode="rgb_only"

    SEMANTIC_HOT_DRINK_TARGETS = {
        'water_bottle': 'water_bottle.n.01_1',
        'refrigerator': 'electric_refrigerator.n.01_1',
        'cabinet': 'cabinet.n.01_1',
        'countertop': 'countertop.n.01_1',
        'stove': 'stove.n.01_1',
        'kettle': 'kettle.n.01_1',
        'mug': 'mug.n.04_1',
        'dinner_napkin': 'dinner_napkin.n.01_1',
        'place_mat': 'place_mat.n.01_1',
        'plate': 'plate.n.04_1',
        'teaspoon': 'teaspoon.n.02_1',
        'tray': 'tray.n.01_1',
        'sugar_cookie': 'sugar_cookie.n.01_1',
        'honey_jar': 'honey__jar.n.01_1',
        'coffee_table': 'coffee_table.n.01_1',
    }

    @staticmethod
    def _floor_relation_motor_point(primitive, obj, selected_point):
        """Approach a selected tree at its base for floor-relative placement."""
        if primitive not in {'place_next_to', 'place_under'} or 'tree' not in str(getattr(obj, 'category', '')).lower():
            return selected_point
        lower, upper = obj.aabb
        point = selected_point.clone()
        point[:2] = (lower[:2] + upper[:2]) / 2
        point[2] = lower[2] + .2
        return point

    def __init__(self,*args,record_video=False,**kwargs):
        self.deadline=EpisodeDeadline.from_env()
        self.deadline.check()
        self.semantic_magic = env_flag('MAS_SEMANTIC_MAGIC')
        self.high_level_harness = env_flag('MAS_HIGH_LEVEL_HARNESS')
        if self.semantic_magic and (not args or args[0] != 'prepare_and_serve_hot_drink'):
            raise ValueError('Semantic magic is scoped to prepare_and_serve_hot_drink')
        if self.high_level_harness and (not args or args[0] != 'prepare_and_serve_hot_drink'):
            raise ValueError('High-level hot-drink harness is scoped to prepare_and_serve_hot_drink')
        self.mode = 'rgb_semantic_magic' if self.semantic_magic else 'rgb_only'
        self.ideal_carry = os.environ.get('MAS_GRASP_MODE','controlled')=='controlled'
        self.demo_motion = os.environ.get('MAS_DEMO_MOTION','0') in {'1','true','yes'}
        if self.demo_motion and not self.ideal_carry:
            raise ValueError('Demo motion requires controlled carry')
        self._demo_arm = None
        self._demo_focus = None
        self._ideal_held=None;self._carry_relative=None;self._carry_orientation=None;self._carry_contents=[];self._carry_dependencies=[];self._object_anchor=None
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
        self._spectator_request_id = None
        # Diagnostic camera-only yaw.  It is intentionally kept outside the
        # robot pose so a read-only capture cannot be mistaken for motion.
        self._camera_yaw_offset = 0.0
        self._base_target = None
        self.image_size=int(os.environ.get('MAS_RGB_IMAGE_SIZE','512'))
        if self.image_size not in (512,768,1024):
            raise ValueError('RGB image size must be 512, 768 or 1024')
        self.rgb_jpeg_quality=int(os.environ.get('MAS_RGB_JPEG_QUALITY','92'))
        if self.rgb_jpeg_quality not in range(70,96):
            raise ValueError('RGB JPEG quality must be between 70 and 95')
        # Preserve the current four simultaneous virtual head cameras.
        # Their private depth is used only to ground the selected RGB pixel.
        # Put the model-facing cameras ahead of the robot head. Demo trials
        # may use a shallower pitch for high shelves or a steeper one for floor items.
        self.rig_radius=0.35
        self.rig_pitch_degrees=float(os.environ.get('MAS_RGB_PITCH_DEGREES','35'))
        if not math.isfinite(self.rig_pitch_degrees) or not 10<=self.rig_pitch_degrees<=45:
            raise ValueError('RGB pitch must be between 10 and 45 degrees down')
        self.capture_index=0
        self.image_files={}
        self.current_frames={}
        self._sensor_packets={}
        self._sensor_intrinsics={}
        self.rig = {}
        super().__init__(*args,**kwargs)
        self._high_level_base_z = (float(self.robot.get_position_orientation()[0][2])
                                   if self.high_level_harness else None)
        self.high_level_state = HighLevelHotDrink() if self.high_level_harness else None
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
        configured_height = os.environ.get('MAS_RGB_RIG_HEIGHT')
        self.rig_height = (float(configured_height) if configured_height is not None else centered_head_height(
            visual_points, base_pos.detach().cpu().tolist(),
            pitch_degrees=self.rig_pitch_degrees, radius=self.rig_radius))
        if not math.isfinite(self.rig_height) or self.rig_height <= 0:
            raise ValueError('RGB rig height must be a finite positive value')
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
        if self._camera_yaw_offset:
            c = math.cos(self._camera_yaw_offset); s = math.sin(self._camera_yaw_offset)
            yaw = self.torch.tensor([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]],
                                    device=base_pos.device, dtype=base_pos.dtype)
            rotation = rotation @ yaw
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
        request_id=self.video.context.get('request_id') if self.video else None
        candidates=[]
        for i,angle in enumerate((135,-135,90,-90,180,0,45,-45)):
            theta = math.radians(angle)
            for j,height in enumerate((.8,1.2)):
                offset = torch.tensor([2.4*math.cos(theta),2.4*math.sin(theta),height])
                distance = float(torch.linalg.norm(offset)); direction = offset/distance
                hit = raytest(target,target+offset,ignore_bodies=ignore)
                clearance = float(torch.linalg.norm(hit['position'].cpu()-target))-.25 if hit['hit'] else distance
                usable = max(.12,min(distance,clearance))
                score = usable-.015*i-.005*j + (
                    .65 if (i,j)==self._spectator_choice and usable>=.65 else 0)
                candidates.append((score,target+direction*usable,usable,(i,j)))
        _,camera,clearance,self._spectator_choice = stable_spectator_candidate(
            candidates,self._spectator_choice,
            same_request=request_id==self._spectator_request_id)
        self._spectator_request_id=request_id
        orientation = torch.tensor(look_at_orientation(camera.tolist(),target.tolist()),dtype=camera.dtype)
        self.spectator.set_position_orientation(camera,orientation)
        self._spectator_anchor = (pos.clone(),yaw)
        # The caller positions every camera before a shared render barrier.
        # Rendering here would draw the old surround-camera poses unnecessarily.
        with (self.output/'spectator_poses.jsonl').open('a') as stream:
            stream.write(json.dumps({'env_step':self.steps,'position':camera.tolist(),'orientation':orientation.tolist(),
                                     'look_at':target.tolist(),'clearance_m':clearance,
                                     'choice':self._spectator_choice,'request_id':request_id,
                                     'audience':'offline_only'})+'\n')

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
        if env_flag("OG_SKIP_PHYSICS_STEPS"):
            # Diagnostic path for scenes whose GPU particle system crashes
            # during PhysX stepping. Keep the requested action in the logical
            # executor while freezing the simulator state for this tick.
            self.steps += 1
            # Navigation can issue thousands of frozen ticks. Encoding a
            # video frame for each one dominates runtime without adding
            # observations; retain only an optional sparse diagnostic stream.
            interval = int(os.environ.get("OG_SKIP_PHYSICS_VIDEO_INTERVAL", "0"))
            if interval > 0 and self.steps % interval == 0:
                self._video_frame('env_step')
            return
        if os.environ.get("OG_DIRECT_SIM_STEP"):
            # Diagnostic path: advance PhysX without flushing the robot
            # articulation controller targets, which isolates GPU view failures
            # caused by the control-target bridge.
            self.og.sim.step()
            self.steps += 1
            self._video_frame('env_step')
            return
        if self._base_target is not None:self._restore_base_target()
        self._restore_object_anchor()
        self._restore_stabilized_containers()
        self._carry_follow()
        with component(self,'physics_and_metrics'):super()._step(action)
        if self._base_target is not None:
            self._restore_base_target()
        self._restore_object_anchor();self._restore_stabilized_containers();self._carry_follow()
        self._stabilize_high_level_base_height()
        self._video_frame('env_step')

    def _stabilize_high_level_base_height(self):
        """Keep the ideal mobile base on the task floor after a physics tick."""
        base_z = getattr(self, '_high_level_base_z', None)
        if base_z is None:
            return
        position, orientation = self.robot.get_position_orientation()
        error = float(position[2]) - base_z
        if abs(error) <= .03:
            return
        corrected = position.clone()
        corrected[2] = base_z
        self.robot.set_position_orientation(corrected, orientation)
        self.robot.keep_still()
        with (self.output / 'high_level_base_height_corrections.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': now(), 'env_step': self.steps,
                'before_z': float(position[2]), 'restored_z': base_z,
                'audience': 'executor_private'}) + '\n')

    def _restore_stabilized_containers(self):
        # Ideal support keeps a filled movable container upright when a later
        # navigation path brushes its body. This does not inspect task goals.
        for obj, joints in getattr(self,'_stabilized_open_joints',{}).items():
            for joint, position in joints:
                joint.set_pos(position)
        for obj,pose in getattr(self,'_stabilized_containers',{}).items():
            if obj is not self._get_held():
                obj.set_position_orientation(*pose);obj.keep_still()
                payload=getattr(self,'_stabilized_container_payloads',{}).get(obj,())
                if payload:self._relocate_container_payload(payload)

    def _restore_base_target(self):
        """Ideal base actuator holds posture while moving; no passive joint drift."""
        import omnigibson.utils.transform_utils as T
        target=self._base_target
        self.robot.set_joint_positions(target['posture'],indices=target['indices'])
        self.robot.set_position_orientation(target['position'],target['orientation'])
        self.robot.keep_still()
        if target['held'] is not None:
            held_pose=T.pose_transform(target['position'],target['orientation'],*target['relative'])
            if target['held'] is self._ideal_held and self._carry_orientation is not None:
                held_pose=(held_pose[0],self._carry_orientation)
            target['held'].set_position_orientation(*held_pose)
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
        if not unchanged:
            if (not env_flag("OG_SKIP_PHYSICS_STEPS") and
                    not os.environ.get("OG_ALLOW_PHYSICS_CAPTURE_DRIFT")):
                raise RuntimeError('Read-only camera capture changed robot state')
            # GPU particle scenes can complete an asynchronous articulation
            # update while a render product is read back. Keep the capture for
            # this diagnostic path and record the discrepancy instead of
            # converting it into a controller failure.
            with (self.output/'observation_capture_warnings.jsonl').open('a') as stream:
                stream.write(json.dumps({'at': now(), 'env_step': self.steps,
                                         'type': 'camera_capture_state_drift',
                                         'allowed': bool(os.environ.get('OG_ALLOW_PHYSICS_CAPTURE_DRIFT')),
                                         'skip_physics': env_flag('OG_SKIP_PHYSICS_STEPS')})+'\n')
        self._video_frame('observation_boundary', rendered=rendered)
        observation={'images':images,'observation_mode':self.mode,'capture':capture}
        if getattr(self, 'semantic_magic', False):
            targets=self._semantic_targets()
            held=self._get_held()
            observation['semantic_targets']=list(targets)
            observation['held_target']=next((name for name,obj in targets.items() if obj is held),None)
        return observation

    def _semantic_targets(self):
        scope=self.env.task.object_scope
        return {name:scope[key] for name,key in self.SEMANTIC_HOT_DRINK_TARGETS.items()
                if scope.get(key) is not None}

    def _high_level_record(self):
        with (self.output/'high_level_state.jsonl').open('a') as stream:
            stream.write(json.dumps({'at':now(),'env_step':self.steps,
                'action':self.high_level_state.history[-1],
                'state':self.high_level_state.snapshot(),
                'evaluation':self.high_level_state.evaluate()['semantic_goals'],
                'audience':'evaluator_private'})+'\n')

    def _high_level_payload_snapshot(self, name, root, targets):
        """Carry every semantic descendant, even when OG contact is missing."""
        import omnigibson.utils.transform_utils as T
        origin=root.get_position_orientation()
        edges=[];contents=[]
        for child_name,parent_name in self.high_level_state.descendants(name):
            child=targets[child_name];parent=targets[parent_name]
            edges.append((child,parent,'OnTop'))
            contents.append((child,T.relative_pose_transform(
                *child.get_position_orientation(),*origin)))
        return edges,contents

    def _high_level_place(self, target, *, inside=False):
        """Move the carried prop to a selected support without a contact search."""
        held=self._get_held()
        if held is None:raise SkillError('empty_hand','Carry an object first')
        lower,upper=target.aabb
        held_pos,held_quat=held.get_position_orientation()
        lower=lower.to(held_pos.device);upper=upper.to(held_pos.device)
        held_lower,_=held.aabb
        position=held_pos.clone()
        position[:2]=(lower[:2]+upper[:2])/2
        if not inside:
            # Spread direct table placements across the visible top. Nested
            # placements use their selected tray, plate or mat as support.
            count=sum(parent is target for parent in getattr(self,'_high_level_placed',{}).values())
            offsets=((0.,0.),(-.35,-.25),(.35,-.25),(-.35,.25),(.35,.25),
                     (0.,-.35),(0.,.35),(-.55,0.),(.55,0.))
            ox,oy=offsets[count%len(offsets)]
            position[0]+=ox*(upper[0]-lower[0])/2
            position[1]+=oy*(upper[1]-lower[1])/2
            position[2]=upper[2]+(held_pos[2]-held_lower[2])+.003
        else:
            position[2]=(lower[2]+upper[2])/2
        contents=list(getattr(self,'_carry_contents',[]))
        self._carry_detach()
        held.set_position_orientation(position,held_quat)
        held.keep_still()
        self._relocate_contents(held,contents)
        if not hasattr(self,'_stabilized_containers'):self._stabilized_containers={}
        self._stabilized_containers[held]=(position.clone(),held_quat.clone())
        for child,_ in contents:
            self._stabilized_containers[child]=tuple(
                value.clone() for value in child.get_position_orientation())
        if not hasattr(self,'_high_level_placed'):self._high_level_placed={}
        self._high_level_placed[held]=target
        self._carry_particle_carrier=None;self._carry_particle_contents=[];self._carry_filled_systems=[]
        with self._anchored_operation():
            for _ in range(6):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        return {'implementation':'high_level_prop_placement',
                'selected_support':target.name,'contact_predicate_required':False}

    def _execute_high_level_semantic(self,primitive,target,max_steps,*,auto_navigate=True,
                                     selected_object=None,**kwargs):
        """Apply a selected operation; named legacy runs may also auto-walk."""
        candidate=deepcopy(self.high_level_state)
        semantic_seconds=float(kwargs.get('seconds',5.0)) if primitive=='wait' else 0.0
        candidate.apply(primitive,target,seconds=semantic_seconds)
        if primitive=='wait':
            steps=min(max_steps,30)
            with self._anchored_operation():
                for _ in range(steps):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            self.high_level_state=candidate
            self._high_level_record()
            return {'primitive':'wait','implementation':'high_level_clock',
                    'semantic_seconds':semantic_seconds,
                    'simulated_steps':steps}
        if primitive=='release':
            held=self._get_held()
            self._carry_detach()
            with self._anchored_operation():
                for _ in range(min(6,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            self._carry_particle_carrier=None;self._carry_particle_contents=[];self._carry_filled_systems=[]
            self.high_level_state=candidate
            self._high_level_record()
            return {'primitive':'release','implementation':'high_level_release',
                    'released_object':held.name}
        targets=self._semantic_targets()
        obj=selected_object if selected_object is not None else targets.get(target)
        if obj is None:raise SkillError('unknown_semantic_target','Unknown named object')
        approach=obj
        if primitive=='grasp':
            parent=self.high_level_state.support.get(target)
            if parent in {'cabinet','refrigerator'}:
                approach=targets[parent]
        travel_steps=0
        if obj is not self._get_held():
            lower,upper=approach.aabb
            base=self.robot.get_position_orientation()[0]
            lower=lower.to(base.device);upper=upper.to(base.device)
            point=(lower+upper)/2
            nearest=self.torch.maximum(lower[:2],self.torch.minimum(base[:2],upper[:2]))
            distance=float(self.torch.linalg.norm(base[:2]-nearest))
            if not auto_navigate and distance>1.5:
                raise SkillError('out_of_reach','Select a visible navigation point before manipulating this object')
            if auto_navigate and distance>1.3:
                anchor=SimpleNamespace(aabb=approach.aabb,
                    get_position_orientation=lambda:(point,None),selected_object=approach)
                before=self.steps
                self._navigate(anchor,max_steps,for_manipulation=True)
                travel_steps=self.steps-before
                max_steps-=travel_steps
                if max_steps<=0:raise SkillError('action_timeout','No steps remain after walking',changed=True)
        if primitive=='grasp':
            payload=self._high_level_payload_snapshot(target,obj,targets)
            result=self._ideal_grasp(obj,max_steps,payload_snapshot=payload)
            self._high_level_placed.pop(obj,None) if hasattr(self,'_high_level_placed') else None
        elif primitive in {'place_on_top','place_near_heating_element','place_inside'}:
            result=self._high_level_place(obj,inside=primitive=='place_inside')
        elif primitive=='boil':
            with self._anchored_operation():
                for _ in range(min(6,max_steps)):
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            result={'implementation':'high_level_boiling',
                    'boiled':'water','water_boiled':True}
        elif primitive in {'open','close','toggle_on','toggle_off'}:
            from omnigibson.object_states import Open,ToggledOn
            state=Open if primitive in {'open','close'} else ToggledOn
            wanted=primitive in {'open','toggle_on'}
            setter_ok=False
            observed=None
            with self._anchored_operation():
                try:
                    if state in obj.states:
                        setter_ok=bool(obj.states[state].set_value(
                            wanted, **({'fully': True} if state is Open else {})))
                except Exception:
                    pass
                for _ in range(min(6,max_steps)):
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if state in obj.states:
                try:observed=bool(obj.states[state].get_value())
                except Exception:pass
            if primitive in {'open','close'} and (not setter_ok or observed is not wanted):
                raise SkillError('postcondition_error',
                                 'Container door did not reach the requested visible state',changed=True)
            result={'implementation':'high_level_device_state','value':wanted,
                    'setter_ok':setter_ok,'observed_value':observed}
        elif primitive=='pour':
            with self._anchored_operation():
                for _ in range(min(6,max_steps)):
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            result={'implementation':'high_level_liquid_transfer',
                    'liquid':candidate.water.get(target)}
        else:
            raise SkillError('unsupported_relation','Unsupported named magic action')
        self.high_level_state=candidate
        self._high_level_record()
        return {'primitive':primitive,**result,'semantic_target':target,
                'target_category':obj.category,'navigation_steps':travel_steps}

    @action_profile
    def execute_semantic(self,primitive,target,max_steps,**kwargs):
        """Named magic actions for a planning-only comparison, with real base travel.

        The target catalog contains task object *categories* already described
        by the human instruction. The model chooses each primitive and target;
        no goal predicate or action order is supplied by this adapter.
        """
        if not self.semantic_magic:
            raise SkillError('semantic_mode_required','Semantic actions are disabled')
        if self.high_level_harness:
            return self._execute_high_level_semantic(primitive,target,max_steps,**kwargs)
        if primitive=='wait':
            return self.execute_visual('wait',None,max_steps,**kwargs)
        if primitive=='release':
            return self._ideal_release(max_steps)
        obj=self._semantic_targets().get(target)
        if obj is None:
            raise SkillError('unknown_semantic_target','Unknown named object')
        held=self._get_held()
        approach_obj=obj
        if primitive=='grasp':
            if obj.fixed_base:raise SkillError('fixed_object','Selected object has a fixed base')
            if held is not None:raise SkillError('hand_occupied','Place or release the carried object first')
            from omnigibson.object_states import Inside,Open
            if Inside in obj.states:
                for parent in self.env.scene.objects:
                    if (parent is not obj and hasattr(parent,'states') and
                            Open in parent.states and obj.states[Inside].get_value(parent)):
                        if not parent.states[Open].get_value():
                            raise SkillError('container_closed','Containing cabinet or refrigerator is closed')
                        # The contained item's center may lie behind the
                        # cabinet wall. Walk to the open container instead.
                        approach_obj=parent
                        break
        if primitive in {'place_on_top','place_inside','place_near_heating_element','pour'} and held is None:
            raise SkillError('empty_hand','Carry the source object first')
        if held is not None and primitive in {'open','close','toggle_on','toggle_off'}:
            if not (held is obj and primitive in {'toggle_on','toggle_off'}):
                raise SkillError('hand_occupied','Place or release the carried object first')
        travel_steps=0
        if obj is not held:
            before=self.steps
            from types import SimpleNamespace
            lower,upper=approach_obj.aabb
            nav_point=(lower+upper)/2
            point=nav_point.clone()
            point[2]=upper[2]
            base=self.robot.get_position_orientation()[0]
            nearest=self.torch.maximum(lower[:2],self.torch.minimum(base[:2],upper[:2]))
            if float(self.torch.linalg.norm(base[:2]-nearest))>1.3:
                anchor=SimpleNamespace(aabb=approach_obj.aabb,
                    get_position_orientation=lambda:(nav_point,None),selected_object=approach_obj)
                self._navigate(anchor,max_steps,for_manipulation=True)
            travel_steps=self.steps-before
            max_steps-=travel_steps
            if max_steps<=0:raise SkillError('action_timeout','No steps remain after walking',changed=True)
        else:
            lower,upper=obj.aabb
            point=(lower+upper)/2
            point[2]=upper[2]
        if primitive=='grasp':
            result=self._ideal_grasp(obj,max_steps)
            result={**result,'grounded_category':obj.category}
        elif primitive=='place_on_top':
            # A named-object command has no user-selected surface pixel. Let
            # the official object-wide sampler find a valid support patch.
            result=self._checked_place_on_top(obj,max_steps)
        elif primitive=='place_inside':
            result=self._checked_place_inside(obj,max_steps)
        elif primitive=='place_near_heating_element':
            result=self._checked_place_near_heating_element(obj,max_steps,None)
        elif primitive=='pour':
            result=self._pour_into(obj,max_steps)
        elif primitive in {'open','close','toggle_on','toggle_off'}:
            result=self._ideal_state_action(primitive,obj,max_steps)
        else:
            raise SkillError('unsupported_relation','Unsupported named magic action')
        return {**result,'semantic_target':target,'target_category':obj.category,
                'navigation_steps':travel_steps}

    def image_bytes(self,ref):
        if ref not in self.image_files: raise KeyError(ref)
        return self.image_files[ref].read_bytes(),'image/jpeg'

    def _ground(self,target, *, require_object=True):
        """Use the chosen pixel's depth backprojection directly as the target.

        Resolve the first visual object on the same ray for its simulator handle
        and navigation intent: a low item is not a floor waypoint. Navigation
        and manipulation both keep the original depth-backprojected point.
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
        if os.environ.get("OG_KINEMATIC_CAMERA_TURNS"):
            # Diagnostic path for GPU PhysX articulation-view failures: turn
            # only the virtual camera rig; leave the robot and physics state
            # untouched so the read-only observation boundary remains valid.
            self._camera_yaw_offset += math.radians(degrees)
            return {'motor': 'kinematic_camera_turn', 'motion_steps': 0,
                    'steps': 0, 'degrees': degrees, 'physical_controller': False}
        return self._execute_base_path([pos[:2].cpu().tolist()],yaw+math.radians(degrees),max_steps)

    def _navigation_self_hulls(self):
        """Current visible robot and carried geometry in yaw-local base coordinates."""
        import numpy as np
        import omnigibson.utils.transform_utils as T
        from .self_depth_filter import prepare_self_hulls
        position,orientation=self.robot.get_position_orientation()
        rotation=T.quat2mat(orientation)
        yaw=math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        c,s=math.cos(yaw),math.sin(yaw)
        inverse=np.array([[c,s,0.],[-s,c,0.],[0.,0.,1.]])
        base=position.detach().cpu().numpy()
        objects=[self.robot]
        held=self._get_held()
        if held is not None:objects.append(held)
        objects.extend(obj for obj,_ in self._carry_contents)
        vertices={}
        for obj in objects:
            for link in obj.links.values():
                points=link.visual_boundary_points_world
                if points is not None:
                    vertices[link.prim_path]=(points.detach().cpu().numpy()-base)@inverse.T
        hulls,report=prepare_self_hulls(vertices)
        return hulls,report

    def _approach_visible(self, xy, point, selected_object, *, margin=.04, max_distance=1.4,
                          self_hulls=()):
        """Check candidate RGB frusta, own-body silhouette and scene rays."""
        from omnigibson.utils.sampling_utils import raytest
        from ..observations.rig import visible_rig_rays
        from .self_depth_filter import segment_hits_self_hull
        torch=self.torch
        if math.dist(xy,point[:2].cpu().tolist())>max_distance:return False
        ignore=[l.prim_path for l in self.robot.links.values()]
        held=self._get_held()
        if held is not None:ignore.extend(l.prim_path for l in held.links.values())
        ignore.extend(l.prim_path for obj,_ in self._carry_contents for l in obj.links.values())
        yaw=math.atan2(float(point[1])-xy[1],float(point[0])-xy[0])
        rays=visible_rig_rays(xy,yaw,float(self.robot.get_position_orientation()[0][2]),self.rig_height,
                              point.cpu().tolist(),margin=margin,radius=self.rig_radius,
                              pitch_degrees=self.rig_pitch_degrees)
        c,s=math.cos(yaw),math.sin(yaw)
        base_z=float(self.robot.get_position_orientation()[0][2])
        def local(world):
            dx,dy,dz=world[0]-xy[0],world[1]-xy[1],world[2]-base_z
            return (c*dx+s*dy,-s*dx+c*dy,dz)
        for _,origin,_ in rays:
            if segment_hits_self_hull(local(origin),local(point.cpu().tolist()),self_hulls):
                continue
            hit=raytest(torch.tensor(origin),point.cpu(),ignore_bodies=ignore)
            if (not hit['hit'] or ray_hits_selected_object(hit,selected_object) or
                    float(torch.linalg.norm(hit['position'].cpu()-point.cpu()))<.10):return True
        return False

    def _dynamic_floor_obstacles(self, base_z):
        """Current movable floor furniture, for private base-path clearance."""
        held=self._get_held()
        carried={id(held),*(id(obj) for obj,_ in self._carry_contents)}
        boxes=[];names=[]
        for obj in self.env.scene.objects:
            if obj is self.robot or id(obj) in carried or getattr(obj,'fixed_base',True):
                continue
            category=str(getattr(obj,'category','')).lower()
            if 'tree' in category or 'plant' in category:
                # Branch AABBs cover navigable floor; these sparse objects
                # need the simulator's actual route and visual-ray checks.
                continue
            lo,hi=obj.aabb
            width=float(hi[0]-lo[0]);depth=float(hi[1]-lo[1])
            # Low floor containers still intersect the moving base: the
            # Christmas basket reaches only 0.19 m yet a route through its
            # footprint pushed it over two metres in the simulator.
            if (float(lo[2])>base_z+.12 or float(hi[2])<base_z+.08
                    or max(width,depth)<.35):
                continue
            boxes.append((float(lo[0]),float(lo[1]),float(hi[0]),float(hi[1])))
            names.append(obj.name)
        return boxes,names

    def _nearby_transport_anchors(self, selected):
        """Temporarily stabilize the selected support and adjacent floor furniture."""
        if selected is None:
            return None
        lo,hi=selected.aabb
        base_z=float(self.robot.get_position_orientation()[0][2])
        _,names=self._dynamic_floor_obstacles(base_z)
        furniture={obj.name:obj for obj in self.env.scene.objects if obj.name in names}
        anchors=[selected]
        for name in names:
            obj=furniture[name]
            if obj is selected:
                continue
            other_lo,other_hi=obj.aabb
            dx=max(float(lo[0]-other_hi[0]),float(other_lo[0]-hi[0]),0.)
            dy=max(float(lo[1]-other_hi[1]),float(other_lo[1]-hi[1]),0.)
            if math.hypot(dx,dy)<=.65:
                anchors.append(obj)
        return anchors

    def _floor_map_with_open_garage_door(self, floor):
        """Use the supplied open-door map only around this task's opened door."""
        trav = self.env.scene.trav_map
        original = trav.floor_map[floor]
        if (floor != 0 or getattr(self, 'task_metadata', {}).get('task') != 'carrying_in_groceries'
                or self.env.task.scene_name != 'house_double_floor_lower'):
            return original, None
        door = next((obj for obj in self.env.scene.objects if obj.name == 'door_bexenl_0'), None)
        if door is None:
            return original, None
        from omnigibson import object_states
        state = door.states.get(object_states.Open)
        if state is None or not bool(state.get_value()):
            return original, None
        import cv2
        from .doorway_traversability import merge_open_door_cells
        source = Path(self.env.scene.scene_dir) / 'layout' / 'floor_trav_open_door_0.png'
        if not source.is_file():
            raise SkillError('navigation_map_missing', f'Open-door map is absent: {source}')
        size = (int(original.shape[1]), int(original.shape[0]))
        cached = getattr(self, '_grocery_open_door_map_cache', None)
        if cached is None or cached[0] != (source, size):
            image = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise SkillError('navigation_map_missing', f'Open-door map cannot be read: {source}')
            image = cv2.resize(image, size)
            image[image < 255] = 0
            cached = ((source, size), image, hashlib.sha256(source.read_bytes()).hexdigest())
            self._grocery_open_door_map_cache = cached
        center = door.get_position_orientation()[0][:2].cpu().tolist()
        merged, added = merge_open_door_cells(
            original.cpu().numpy(), cached[1], center_xy=center,
            resolution=float(trav.map_resolution), radius_m=1.0)
        return self.torch.tensor(merged, dtype=original.dtype, device=original.device), {
            'door': door.name, 'door_open': True, 'added_raw_cells': added,
            'local_radius_m': 1.0, 'source_sha256': cached[2],
            'policy': 'supplied_open_door_map_only_near_open_garage_interior_door',
        }

    def _navigate(self, target, max_steps, *, for_manipulation=False):
        """Jinkai visual-point GT strategy with a private OmniGibson substrate."""
        from dataclasses import asdict
        from .gt_navigation import (GridMap, NavigationError, mask_dynamic_obstacles,
                                    plan_navigation, navigation_approach_settings, STRATEGY)
        # A failed placement leaves the hand above the selected surface while
        # the item remains held. Carrying that long lever through a base turn
        # can knock over furniture even on a clear base path.
        selected=getattr(target,'selected_object',None)
        transport_anchors=(self._nearby_transport_anchors(selected)
                           if self.demo_motion and self._get_held() is not None
                           and self._demo_arm is not None else None)
        transport_steps = (self._demo_transport_pose(
            max_steps, required=True, anchor=transport_anchors)
                           if self.demo_motion and self._get_held() is not None
                           and self._demo_arm is not None else 0)
        max_steps -= transport_steps
        trav=self.env.scene.trav_map
        position,_=self.robot.get_position_orientation()
        point=target.get_position_orientation()[0]
        floor_waypoint=(not for_manipulation
                        and str(getattr(selected,'category','')).lower() in {'floor','floors'}
                        and abs(float(point[2]-position[2]))<.12)
        standoff,margin,require_endpoint_visibility=(.7,.04,True) if for_manipulation else navigation_approach_settings(
            self.rig_height,float(position[2]),float(point[2]),floor_waypoint=floor_waypoint,
            pitch_degrees=self.rig_pitch_degrees,radius=self.rig_radius)
        if getattr(self, 'high_level_harness', False) and for_manipulation:
            # Semantic operations have no pixel-sized reach requirement.  The
            # legacy 0.7 m approach can project behind a kitchen wall even
            # though the target is already within a safe named-action range.
            standoff = 1.5
        floor=min(range(len(trav.floor_heights)),key=lambda i:abs(float(position[2])-trav.floor_heights[i]))
        floor_map,doorway_overlay=self._floor_map_with_open_garage_door(floor)
        occupancy=trav._erode_trav_map(floor_map.clone()).cpu().numpy()
        height,width=occupancy.shape
        static_grid=GridMap(width,height,float(trav.map_resolution),
                            (-width*trav.map_resolution/2,-height*trav.map_resolution/2),
                            (occupancy!=0).astype('uint8').tobytes())
        furniture,furniture_names=self._dynamic_floor_obstacles(float(position[2]))
        try:
            grid,masked_cells=mask_dynamic_obstacles(
                static_grid,furniture,margin=.38,start=position[:2].cpu().tolist())
        except NavigationError as exc:
            raise SkillError(exc.code,str(exc)) from exc
        # Public navigation must leave the selected surface observable in the
        # resulting RGB. An in-action reach may move closer after the model has
        # already selected its pixel; its hand is expected to enter that ray.
        self_hulls,self_report=((),{'policy':'selected_pixel_reach_may_occlude'}) if for_manipulation else self._navigation_self_hulls()
        # A named high-level target is already grounded by the scene catalog;
        # no model-selected RGB pixel needs to remain visible at the endpoint.
        # Keep the same collision-filtered grid path and continuous walking.
        named_target_geometry=getattr(self, 'high_level_harness', False) and for_manipulation
        navigation_visibility_relaxed=False
        try:
            with component(self,'navigation_planning'):
                plan=plan_navigation(grid,position[:2].cpu().tolist(),point[:2].cpu().tolist(),
                                     standoff=standoff,
                                     max_target_distance=.5 if floor_waypoint else None,
                                     candidate_filter=(None if named_target_geometry or not require_endpoint_visibility else
                                         lambda xy:self._approach_visible(
                                             xy,point,getattr(target,'selected_object',None),
                                             margin=margin,max_distance=max(1.4,standoff+.35),
                                             self_hulls=self_hulls)))
        except NavigationError as exc:
            # In the frozen-physics diagnostic mode, a visually valid target can
            # be rejected solely because the strict approach-frustum check has
            # no candidate around a cabinet edge. Retry on the same static,
            # eroded grid without that visibility filter; the selected RGB ray
            # and bounded standoff are still unchanged, so this does not invent
            # an object or target and lets the model inspect the next view.
            if not env_flag('MAS_NAV_RELAXED'):
                raise SkillError(exc.code,str(exc)) from exc
            navigation_visibility_relaxed=True
            try:
                with component(self,'navigation_planning_relaxed'):
                    plan=plan_navigation(grid,position[:2].cpu().tolist(),point[:2].cpu().tolist(),
                                         standoff=standoff,candidate_filter=None,
                                         max_target_distance=.5 if floor_waypoint else None)
            except NavigationError:
                raise SkillError(exc.code,str(exc)) from exc
        details={'at':now(),'audience':'executor_private','strategy':STRATEGY,'floor':floor,
                 'plan':asdict(plan),'map_resolution_m':grid.resolution,
                 'visual_standoff_m':standoff,'visual_margin':margin,
                 'floor_waypoint':floor_waypoint,
                 'max_target_distance_m':.5 if floor_waypoint else None,
                 'require_endpoint_visibility':require_endpoint_visibility,
                 'navigation_visibility_relaxed':navigation_visibility_relaxed,
                 'named_target_geometry_only':named_target_geometry,
                 'self_occlusion_geometry':self_report,
                 'map_sha256':hashlib.sha256(grid.free).hexdigest(),
                 'dynamic_collision_check':True,
                 'collision_substrate':'static_eroded_grid_plus_current_movable_floor_aabbs',
                 'dynamic_obstacle_count':len(furniture),
                 'dynamic_masked_cells':masked_cells,
                 'dynamic_obstacle_names':furniture_names,
                 'doorway_overlay':doorway_overlay,
                 'pre_navigation_transport_steps':transport_steps,
                 'pre_navigation_anchored_furniture':
                     [obj.name for obj in transport_anchors] if transport_anchors else [],
                 'precomputed_walkability':True,'online_mapping':False}
        with (self.output/'navigation_plans.jsonl').open('a') as stream:
            stream.write(json.dumps(details)+'\n')
        if env_flag("OG_KINEMATIC_NAV_TELEPORT"):
            # Keep the same RGB-grounded planner and collision-filtered
            # endpoint, but avoid thousands of controller ticks in the
            # particle-scene diagnostic path. This is a bounded ideal motor
            # move; it does not choose a target or bypass the visual ray.
            import omnigibson.utils.transform_utils as T
            start_pos, start_quat = self.robot.get_position_orientation()
            endpoint = self.torch.tensor(plan.goal, device=start_pos.device, dtype=start_pos.dtype)
            new_pos = start_pos.clone()
            new_pos[:2] = endpoint
            yaw = math.atan2(float(point[1] - endpoint[1]), float(point[0] - endpoint[0]))
            new_quat = T.euler2quat(self.torch.tensor([0.0, 0.0, yaw], device=start_quat.device,
                                                       dtype=start_quat.dtype))
            held = self._get_held()
            relative = T.relative_pose_transform(*held.get_position_orientation(), start_pos, start_quat) if held else None
            self.robot.set_position_orientation(new_pos, new_quat)
            self.robot.keep_still()
            if held is not None and relative is not None:
                held.set_position_orientation(*T.pose_transform(new_pos, new_quat, *relative))
                held.keep_still()
            self.steps += 1
            distance = float(plan.geodesic_m)
            self.navigation_distance += distance
            return {'motor': 'kinematic_endpoint_teleport', 'nav_status': 'reached',
                    'planned_path_distance_m': distance, 'path_distance_m': distance,
                    'actual_path_distance_m': distance, 'steps': 1,
                    'settling_steps': 0, 'physical_controller': False,
                    'dynamic_collision_check': True,
                    'candidate_count': plan.candidates_considered,
                    'reachable_candidates': plan.candidates_reachable,
                    'start_grid_offset_m': plan.start_grid_offset_m,
                    'pre_navigation_transport_steps': transport_steps,
                    'strategy': 'kinematic_endpoint_teleport'}
        result=self._execute_gt_plan(grid,plan,max_steps)
        self.navigation_distance+=result['actual_path_distance_m']
        return {**result,'strategy':STRATEGY,'planned_path_distance_m':plan.geodesic_m,
                'candidate_count':plan.candidates_considered,'reachable_candidates':plan.candidates_reachable,
                'start_grid_offset_m':plan.start_grid_offset_m,'dynamic_collision_check':True,
                'pre_navigation_transport_steps':transport_steps,
                'steps':result['steps']+transport_steps}

    def _execute_gt_plan(self, grid, plan, max_steps):
        """Follow the GT path using actual pose feedback and bounded commands."""
        import omnigibson.utils.transform_utils as T
        from .gt_navigation import GreedyGridFollower, NavigationError
        settling=10
        if max_steps<=settling:raise SkillError('action_timeout','Insufficient GT follower step budget')
        pos,quat=self.robot.get_position_orientation()
        walk_speed=1.2 if getattr(self, 'high_level_harness', False) else .5
        turn_speed=2*math.pi/3 if getattr(self, 'high_level_harness', False) else math.pi/3
        follower=GreedyGridFollower(grid,plan,self.og.sim.get_sim_step_dt(),
                                   speed=walk_speed,turn_speed=turn_speed)
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
                with (self.output/'navigation_failures.jsonl').open('a') as stream:
                    stream.write(json.dumps({'at':now(),'audience':'executor_private',
                        'reason':'final_pose_verification','goal':plan.goal,
                        'actual_xy':actual_pos[:2].cpu().tolist(),'final_error_m':final_error})+'\n')
                raise SkillError('navigation_unreachable','GT follower final pose verification failed',changed=self.steps>before)
        except NavigationError as exc:
            with (self.output/'navigation_failures.jsonl').open('a') as stream:
                stream.write(json.dumps({'at':now(),'audience':'executor_private',
                    'reason':str(exc),'code':exc.code,'goal':plan.goal,
                    'actual_xy':self.robot.get_position_orientation()[0][:2].cpu().tolist()})+'\n')
            raise SkillError(exc.code,str(exc),changed=self.steps>before) from exc
        finally:
            self._base_target=None
        return {'motor':'gt_grid_feedback_kinematic','nav_status':'reached','motion_steps':motion_steps,
                'steps':self.steps-before,'final_position_error_m':final_error,'actual_path_distance_m':travelled,
                'heading_tolerance_rad':follower.heading_tolerance,
                'final_yaw_tolerance_rad':follower.final_yaw_tolerance,
                'progress_measure':'directed_path_distance_or_target_yaw_convergence',
                'max_speed_m_s':walk_speed,'max_yaw_speed_deg_s':math.degrees(turn_speed),
                'physical_controller':False}

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
        high_level = getattr(self, 'high_level_harness', False) and not getattr(self, 'semantic_magic', False)
        if high_level and primitive in {'release','wait'}:
            return self._execute_high_level_semantic(primitive,None,max_steps,
                                                      auto_navigate=False,**kwargs)
        if primitive=='release' and self.ideal_carry:return self._ideal_release(max_steps)
        if primitive=='wait':
            count=min(max_steps,int(round(float(kwargs.get('seconds',5.0))/self.og.sim.get_sim_step_dt())))
            with self._anchored_operation():
                for _ in range(count):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            return {'primitive':'wait','steps':count,'sim_seconds':count*self.og.sim.get_sim_step_dt()}
        if primitive=='release':return self.execute(primitive,None,max_steps)
        obj,point,grounding=self._ground(target,require_object=True)
        if primitive=='navigate_to':
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None),selected_object=obj)
            return {**self._navigate(anchor,max_steps),'private_grounding':grounding}
        if obj is None or not hasattr(obj,'states'):
            raise SkillError('invalid_visual_target','No manipulable object at selected pixel')
        if high_level:
            selected_name=next((name for name,candidate in self._semantic_targets().items()
                                if candidate is obj),None)
            if (selected_name is None and primitive=='place_on_top'
                    and str(getattr(obj,'category','')).lower() in {'countertop','bar'}):
                # Several distinct visible kitchen worktops are equivalent as
                # temporary supports. Keep the model-selected physical surface.
                selected_name='countertop'
            if selected_name is None:
                raise SkillError('invalid_visual_target','The selected pixel is not a hot-drink task object')
            result=self._execute_high_level_semantic(primitive,selected_name,max_steps,
                                                      auto_navigate=False,
                                                      selected_object=obj,**kwargs)
            if primitive=='grasp':
                result={**result,'grounded_category':obj.category}
            return {**result,'private_grounding':grounding}
        if primitive=='place_next_to' and str(getattr(obj,'category','')).lower() in {'floor','lawn','ground','ground_plane'}:
            raise SkillError('unsupported_relation','Select the visible tree or fixture itself for place_next_to')
        if primitive=='place_on_top' and str(getattr(obj,'category','')).lower() in {'wall','walls'}:
            raise SkillError('invalid_visual_target','Select a horizontal support patch, not the wall or counter backsplash')
        held=self._get_held()
        if primitive=='grasp' and held is None and getattr(self,'demo_motion',False):
            self._demo_arm=None
        if primitive=='grasp' and obj.fixed_base: raise SkillError('fixed_object','Fixed object')
        if primitive=='grasp' and held is not None: raise SkillError('hand_occupied','Hand occupied')
        if primitive in {'attach','hang','place_inside','place_on_top','place_under','place_next_to','pour',
                         'place_near_heating_element','wipe','sweep','vacuum','spray','spread','soak','cut'} and held is None:
            raise SkillError('empty_hand','Empty hand')
        if held is not None and primitive in {'open','close','toggle_on','toggle_off'}:
            # Controlled carry can operate the switch on the object already
            # being carried. This does not grant an extra hand for a different
            # object, or change articulated-container handling while carrying.
            held_self_toggle=self.ideal_carry and held is obj and primitive in {'toggle_on','toggle_off'}
            if not held_self_toggle:raise SkillError('hand_occupied','Hand occupied')
        selected_point = point
        point = RGBBackend._floor_relation_motor_point(primitive, obj, point)
        if point is not selected_point and self.demo_motion:
            self._demo_record(action=primitive,status='tree_base_approach',
                              selected_surface_point=selected_point.tolist(),
                              motor_point=point.tolist())
        from omnigibson.object_states import Open,Inside
        if primitive=='place_inside' and Open in obj.states and not obj.states[Open].get_value():
            raise SkillError('container_closed','Placement into closed container rejected')
        if primitive=='grasp' and Inside in obj.states:
            for parent in self.env.scene.objects:
                if parent is not obj and hasattr(parent,'states') and Open in parent.states and not parent.states[Open].get_value() and obj.states[Inside].get_value(parent):
                    raise SkillError('container_closed','Grasp through closed container rejected')
        pregrasp_payload=(self._grasp_payload_snapshot(obj)
                          if primitive=='grasp' and self.ideal_carry and getattr(self,'demo_motion',False)
                          else None)
        grasp_support_anchors=(self._grasp_support_anchors(obj)
                               if pregrasp_payload is not None else ())
        grasp_reach_anchor=((obj,*grasp_support_anchors)
                            if grasp_support_anchors else obj)
        # A low sofa surface is reachable by folding all four individually
        # bounded trunk joints. Keep the combined posture upright instead.
        trunk_budget=(.9 if primitive=='place_on_top' and
                      str(getattr(obj,'category','')).lower() in {'sofa','couch'}
                      else None)
        if (pregrasp_payload is not None and not kwargs.get('confirm_bulk_grasp',False)
                and bulk_grasp_reason(obj,pregrasp_payload[0]) is not None):
            raise SkillError('bulk_grasp_confirmation_required',
                             'Selected pixel resolves to a loaded support or furniture-sized object')
        base=self.robot.get_position_orientation()[0]
        # Depth backprojection is kept on CPU for reproducible visual
        # grounding, while the robot pose lives on the simulator device.
        # Normalize before distance checks so GPU scenes do not fail on a
        # mixed-device subtraction.
        if point.device != base.device:
            point = point.to(base.device)
        # A long cabinet/floor AABB can contain the base while the selected
        # visible surface is far away. Reach belongs to that selected point.
        reach_limit=1.4
        if float(self.torch.linalg.norm(base[:2]-point[:2]))>reach_limit:
            raise SkillError('out_of_reach',
                             'Use navigate_to to approach the selected surface, then select it again in fresh RGB')
        shown = (self._demo_reach(point,max_steps,anchor=None if obj is held else grasp_reach_anchor,
                                  arm=self._demo_arm if held is not None else None,
                                  action=primitive,
                                  support_payload=pregrasp_payload[1] if pregrasp_payload else None,
                                  trunk_budget_l1_rad=trunk_budget)
                 if getattr(self,'demo_motion',False) else 0)
        max_steps -= shown
        contact_limit=(.45 if primitive=='spray' else .30 if primitive=='vacuum' else .18)
        if shown and self._demo_last_reach_error>contact_limit:
            if trunk_budget is not None:
                # Leave the carried item in a safe, visible transport pose
                # after rejecting a sofa patch that would require a body fold.
                self._demo_transport_pose(max_steps,
                    anchor=self._nearby_transport_anchors(obj))
                raise SkillError('posture_limited_reach',
                    'Selected sofa patch is beyond upright arm reach',changed=True)
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
        before_effect_steps=self.steps if shown else 0
        if primitive=='grasp' and self.ideal_carry:
            before_grasp=self.steps
            result=self._ideal_grasp(obj,max_steps,payload_snapshot=pregrasp_payload,
                                     support_anchors=grasp_support_anchors)
            if shown and self._demo_arm is not None:
                hand=self.robot.eef_links[self._demo_arm].get_position_orientation()[0]
                lifted=hand.clone();lifted[2]+=.16
                shown+=self._demo_reach(lifted,max_steps-(self.steps-before_grasp),
                                        anchor=grasp_support_anchors or None,
                                        arm=self._demo_arm,action='lift_after_grasp')
                shown+=self._demo_transport_pose(max_steps-(self.steps-before_grasp),
                                                 anchor=grasp_support_anchors or None)
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
        elif primitive=='place_near_heating_element' and self.ideal_carry:
            result=self._checked_place_near_heating_element(obj,max_steps,point)
        elif primitive=='place_under':
            result=self._checked_place_under(obj,max_steps)
        elif primitive=='place_next_to':
            result=self._checked_place_next_to(obj,max_steps,point)
        elif primitive=='place_inside' and self.inside_placement=='official_volume':
            result=self._checked_place_inside(obj,max_steps)
        else:
            result=self.execute(primitive,obj.name,max_steps)
        if shown and primitive in {'place_inside','place_on_top','place_under','place_next_to','pour','place_near_heating_element',
                                   'attach','hang','open','close',
                                   'toggle_on','toggle_off'}:
            # A stretched arm must not sweep the destination over when the
            # model's next navigation action moves the base away.
            post_anchor=(self._nearby_transport_anchors(obj)
                         if primitive=='place_on_top' else None)
            shown+=self._demo_transport_pose(max_steps-(self.steps-before_effect_steps),
                                             anchor=post_anchor)
        if shown:
            result={**result,'demo_motion':{'real_env_steps':shown,'ideal_contact':True}}
        if primitive == 'grasp':
            # Report only the class of the object actually selected by this
            # grasp. Instance names, pose and task bindings stay private.
            result = {**result, 'grounded_category': getattr(obj, 'category', None)}
        return {**result,'private_grounding':grounding}

    def evaluate(self):
        if getattr(self, 'high_level_harness', False):
            result=self.high_level_state.evaluate()
            result['observation_mode']=self.mode
            result['demo_motion']=self.demo_motion
            return result
        result=super().evaluate()
        if env_flag('MAS_CUSTOM_NATIVE_BDDL'):
            result['custom_task_success']=result['official_task_success']
            result['official_task_success']=None
            result['protocol']='custom_native_bddl_rgb_agent_ideal_executor'
        else:
            result['protocol']='rgb_agent_ideal_executor_v10_selected_grasp_category_feedback'
        result['observation_mode']=self.mode
        result['demo_motion']=getattr(self,'demo_motion',False)
        return result

    def provenance(self):
        from .gt_navigation import SOURCE_COMMIT, STRATEGY
        result=super().provenance()
        result.update(executor='controlled_carry_and_checked_placement_plus_jinkai_gt_navigation' if self.ideal_carry else 'symbolic_manipulation_plus_jinkai_gt_grid_navigation',
                      observation_mode=self.mode,image_size=self.image_size,demo_motion=getattr(self,'demo_motion',False),
                      task_definition_origin='custom_bddl_overlay' if env_flag('MAS_CUSTOM_NATIVE_BDDL') else 'pinned_public',
                      grounding='direct_pixel_depth_backprojection_private_executor_only',
                      model_visible_truth={'successful_grasp_category':True,
                          'instance_id':False,'pose':False,'task_binding':False,
                          'goal_state':False,'evaluator':False})
        result['action_feedback_contract']='selected_object_category_after_successful_grasp_only'
        result['visual_navigation_contract'] = {
            'version': 'explicit_navigate_to_only_v2',
            'floor_waypoint_policy': 'selected_visual_floor_only',
            'manipulation_auto_approach': False,
            'far_selection': 'out_of_reach_without_base_motion',
            'scope': 'execute_visual; historical named semantic mode is separate',
        }
        result['grounding_protocol'] = {'position':'same_pixel_depth_linear_backprojection',
            'depth_consistency_check':False, 'navigation_object_lookup':True,
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
        result['camera_rig'] = {'horizontal_fov_degrees':90,'pitch_down_degrees':self.rig_pitch_degrees,
            'mount_radius_m':self.rig_radius,'mount_height_m':self.rig_height,'views':list(DIRECTIONS),
            'height_policy':'initial_visual_body_below_lower_20_percent_at_configured_pitch_and_radius_with_5cm_clearance',
            'stock_wrist_cameras_enabled':False}
        return result

    def close(self):
        try:
            self.finalize_video()
        finally:
            super().close()
