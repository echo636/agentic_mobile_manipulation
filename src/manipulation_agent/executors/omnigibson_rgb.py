"""RGB observer + private pixel-ray motor adapter for pinned OmniGibson 3.9.2."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from ..contracts import SkillError
from ..omnigibson_backend import OmniGibsonBackend

class RGBBackend(OmniGibsonBackend):
    mode="rgb_only"

    def __init__(self,*args,record_video=False,**kwargs):
        self.record_video = record_video
        self.video = None
        self.spectator = None
        self._spectator_anchor = None
        self._spectator_choice = None
        self.image_size=512
        self.capture_index=0
        self.image_files={}
        self.current_frames={}
        super().__init__(*args,**kwargs)
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
            self.video = EpisodeVideo(self.output, fps=1.0/self.og.sim.get_sim_step_dt(),size=self.image_size)
            self._position_spectator()
            for _ in range(20): self.og.sim.render()

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
        direction = target-camera; direction /= torch.linalg.norm(direction)
        right = torch.linalg.cross(direction,torch.tensor([0.,0.,1.]));right /= torch.linalg.norm(right)
        up = torch.linalg.cross(right,direction)
        orientation = T.mat2quat(torch.stack((right,up,-direction),dim=1))
        self.spectator.set_position_orientation(camera,orientation)
        self._spectator_anchor = (pos.clone(),yaw)
        self.og.sim.render()
        with (self.output/'spectator_poses.jsonl').open('a') as stream:
            stream.write(json.dumps({'env_step':self.steps,'position':camera.tolist(),'orientation':orientation.tolist(),
                                     'look_at':target.tolist(),'clearance_m':clearance,'audience':'offline_only'})+'\n')

    def _video_frame(self, kind):
        if self.video is None or self.video.closed: return
        self._position_spectator()
        pixels = self._render_rgb_views(include_spectator=True)
        self.video.append(pixels,self.steps,kind)

    def _render_rgb_views(self, include_spectator=False):
        """Render-product resizing can invalidate all cameras for several frames.

        Wait with render-only ticks; never advance physics to warm up recording.
        """
        import numpy as np
        sensors = {}
        for name,sensor in self.robot.sensors.items():
            if 'Camera' not in name: continue
            view = 'head' if 'zed' in name else 'left_wrist' if 'left' in name else 'right_wrist'
            sensors[view] = sensor
        if include_spectator: sensors['spectator'] = self.spectator
        shapes = {}
        for attempt in range(30):
            self.og.sim.render()
            pixels = {}
            for view,sensor in sensors.items():
                data,_ = sensor.get_obs()
                raw = data.get('rgb')
                if raw is None: continue
                rgb = raw.detach().cpu().numpy()
                shapes[view] = list(rgb.shape)
                if rgb.ndim == 3 and rgb.shape[:2] == (self.image_size,self.image_size) and rgb.shape[2] >= 3:
                    pixels[view] = rgb[...,:3].astype(np.uint8)
            if len(pixels) == len(sensors): return pixels
        raise RuntimeError('RGB render products did not become ready: '+json.dumps(shapes))

    def _step(self, action):
        super()._step(action)
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
        rendered = self._render_rgb_views()
        self.capture_index+=1
        self.current_frames={}
        folder=self.output/'frames';folder.mkdir(exist_ok=True)
        images=[];audit=[]
        for sensor_name,sensor in self.robot.sensors.items():
            if 'Camera' not in sensor_name: continue
            view='head' if 'zed' in sensor_name else 'left_wrist' if 'left' in sensor_name else 'right_wrist'
            ref=f'rgb-{self.capture_index:05d}-{view}'
            pixels=rendered[view]
            path=folder/f'{ref}.jpg'
            Image.fromarray(pixels).save(path,quality=92)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            position,orientation=sensor.get_position_orientation()
            frame={'sensor':sensor,'position':position.clone(),'orientation':orientation.clone(),
                   'intrinsic':sensor.intrinsic_matrix.clone(),'width':pixels.shape[1],'height':pixels.shape[0]}
            self.current_frames[ref]=frame
            self.image_files[ref]=path
            images.append({'image_ref':ref,'view':view,'width':pixels.shape[1],'height':pixels.shape[0],
                           'mime_type':'image/jpeg','sha256':digest})
            audit.append({'image_ref':ref,'file':str(path.relative_to(self.output)),'sha256':digest})
        with (self.output/'captures.jsonl').open('a') as stream:
            stream.write(json.dumps({'capture':self.capture_index,'env_steps':self.steps,'images':audit})+'\n')
        self._video_frame('observation_boundary')
        return {'images':images,'observation_mode':self.mode}

    def image_bytes(self,ref):
        if ref not in self.image_files: raise KeyError(ref)
        return self.image_files[ref].read_bytes(),'image/jpeg'

    def _ground(self,target):
        """Turn an agent-chosen pixel into the FIRST physical surface on its camera ray.

        Uses calibration and collision geometry inside the ideal motor executor.
        No object-name search, task-object filtering, candidate snapping or labels.
        """
        from omnigibson.utils.sampling_utils import raytest
        import omnigibson.utils.transform_utils as T
        frame=self.current_frames.get(target['image_ref'])
        if frame is None: raise SkillError('stale_image_ref','Expired RGB capture')
        x,y=target['point'];K=frame['intrinsic'].cpu()
        px=x*(frame['width']-1);py=y*(frame['height']-1)
        # USD cameras look along -Z; image down corresponds to camera -Y.
        local=self.torch.tensor([(px-float(K[0,2]))/float(K[0,0]),
                                 -(py-float(K[1,2]))/float(K[1,1]),-1.0])
        start=frame['position'].cpu()
        direction=T.quat2mat(frame['orientation'].cpu()) @ local
        direction/=self.torch.linalg.norm(direction)
        hit=raytest(start,start+direction*30.0)
        if not hit['hit']: raise SkillError('no_surface_at_point','No collision surface at selected pixel')
        body=hit.get('rigidBody','')
        obj=next((o for o in self.env.scene.objects if body==o.prim_path or body.startswith(o.prim_path+'/')),None)
        if obj is self.robot: raise SkillError('invalid_visual_target','Pixel hits robot')
        return obj,hit['position'],{'rigid_body':body,'hit_position':hit['position'].tolist(),
                                  'selected_pixel':target['point'],'image_ref':target['image_ref']}

    def _turn(self,degrees,max_steps):
        import omnigibson.utils.transform_utils as T
        pos,quat = self.robot.get_position_orientation()
        rotation = T.quat2mat(quat)
        yaw = math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        return self._execute_base_path([pos[:2].cpu().tolist()],yaw+math.radians(degrees),max_steps)

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
        held=self.primitives._get_obj_in_hand()
        relative=T.relative_pose_transform(*held.get_position_orientation(),pos,quat) if held else None
        before = self.steps
        with (self.output/'base_motion.jsonl').open('a') as stream:
            for x,y,angle in poses:
                new_pos=pos.clone();new_pos[0]=x;new_pos[1]=y
                new_quat=T.euler2quat(self.torch.tensor([0.,0.,angle],device=quat.device))
                self.robot.set_position_orientation(new_pos,new_quat)
                if held is not None:
                    held.set_position_orientation(*T.pose_transform(new_pos,new_quat,*relative));held.keep_still()
                self.robot.keep_still()
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                actual_pos,actual_quat=self.robot.get_position_orientation()
                stream.write(json.dumps({'env_step':self.steps,'commanded_position':new_pos.tolist(),
                    'actual_position':actual_pos.tolist(),'actual_orientation':actual_quat.tolist(),
                    'commanded_yaw':angle,'audience':'executor_private'})+'\n')
        for _ in range(settling):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        return {'motor':'ideal_kinematic_path','motion_steps':len(poses),'settling_steps':settling,
                'steps':self.steps-before,'max_speed_m_s':.5,'max_yaw_speed_deg_s':60,
                'physical_controller':False}

    def execute_visual(self,primitive,target,max_steps,**kwargs):
        if primitive=='look':return self._turn(kwargs['yaw_degrees'],max_steps)
        if primitive in {'release','wait'}:
            return self.execute(primitive,None,max_steps)
        obj,point,grounding=self._ground(target)
        if primitive=='navigate_to':
            anchor=SimpleNamespace(aabb=(point,point),get_position_orientation=lambda:(point,None))
            return {**self._navigate(anchor,max_steps),'private_grounding':grounding}
        if obj is None or not hasattr(obj,'states'):
            raise SkillError('invalid_visual_target','No manipulable object at selected pixel')
        base=self.robot.get_position_orientation()[0]
        lo,hi=obj.aabb; nearest=self.torch.maximum(lo[:2],self.torch.minimum(base[:2],hi[:2]))
        if float(self.torch.linalg.norm(base[:2]-nearest))>1.6:
            raise SkillError('out_of_reach','Selected surface is beyond manipulation radius')
        held=self.primitives._get_obj_in_hand()
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
        return {**self.execute(primitive,obj.name,max_steps),'private_grounding':grounding}

    def evaluate(self):
        result=super().evaluate()
        result['protocol']='rgb_agent_ideal_executor_v1'
        result['observation_mode']=self.mode
        return result

    def provenance(self):
        result=super().provenance()
        result.update(executor='symbolic_manipulation_plus_private_pixel_ray_navigation',
                      observation_mode=self.mode,image_size=self.image_size,
                      grounding='first_collision_on_agent_selected_RGB_pixel_ray',
                      model_visible_truth=False)
        result['record_video'] = self.record_video
        result['base_execution'] = 'live_kinematic_path_0.5m_s_60deg_s; symbolic grasp/place remain instantaneous'
        return result

    def close(self):
        try:
            self.finalize_video()
        finally:
            super().close()
