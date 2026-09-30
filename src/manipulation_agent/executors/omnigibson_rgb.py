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

    def __init__(self,*args,**kwargs):
        self.image_size=512
        self.capture_index=0
        self.image_files={}
        self.current_frames={}
        super().__init__(*args,**kwargs)

    def _objects(self):
        # PRIVATE routing after an agent selects a pixel; no task-scope candidate discovery.
        return {o.name:o for o in self.env.scene.objects if o is not self.robot and hasattr(o,'states')}

    def observe(self):
        from PIL import Image
        import numpy as np
        self.og.sim.render()
        self.capture_index+=1
        self.current_frames={}
        folder=self.output/'frames';folder.mkdir(exist_ok=True)
        images=[];audit=[]
        for sensor_name,sensor in self.robot.sensors.items():
            if 'Camera' not in sensor_name: continue
            data,_=sensor.get_obs()
            if 'rgb' not in data: continue
            view='head' if 'zed' in sensor_name else 'left_wrist' if 'left' in sensor_name else 'right_wrist'
            ref=f'rgb-{self.capture_index:05d}-{view}'
            pixels=data['rgb'].detach().cpu().numpy()[...,:3].astype(np.uint8)
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
        if max_steps<30: raise SkillError('action_timeout','Turn requires 30 settling steps')
        pos,quat=self.robot.get_position_orientation()
        held=self.primitives._get_obj_in_hand()
        relative=T.relative_pose_transform(*held.get_position_orientation(),pos,quat) if held else None
        delta=T.euler2quat(self.torch.tensor([0.,0.,math.radians(degrees)],device=quat.device))
        new_quat=T.quat_multiply(delta,quat)
        self.robot.set_position_orientation(pos,new_quat)
        if held is not None:
            held.set_position_orientation(*T.pose_transform(pos,new_quat,*relative));held.keep_still()
        self.robot.keep_still()
        for _ in range(30):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        return {'motor':'ideal_in_place_turn','yaw_degrees':degrees}

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
        return result
