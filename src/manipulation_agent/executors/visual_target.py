"""Action-local identity and surface attachment, never an agent-visible ID."""
from dataclasses import dataclass
import math
from ..contracts import SkillError


def rotate(vector, quat, inverse=False):
    x,y,z,w = map(float,quat)
    norm = math.sqrt(x*x+y*y+z*z+w*w)
    if not math.isfinite(norm) or norm < 1e-9:
        raise SkillError('target_changed','Invalid target orientation')
    x,y,z,w = (v/norm for v in (x,y,z,w))
    if inverse:x,y,z = -x,-y,-z
    a,b,c = map(float,vector)
    return ((1-2*(y*y+z*z))*a+2*(x*y-z*w)*b+2*(x*z+y*w)*c,
            2*(x*y+z*w)*a+(1-2*(x*x+z*z))*b+2*(y*z-x*w)*c,
            2*(x*z-y*w)*a+2*(y*z+x*w)*b+(1-2*(x*x+y*y))*c)


@dataclass
class LockedTarget:
    obj: object
    anchor: object
    local_point: tuple
    initial_point: tuple
    grounding: dict

    @classmethod
    def bind(cls,obj,point,grounding):
        # A door handle/shelf remains attached to the selected link, not merely
        # to the articulated object's root or its room-sized bounding box.
        mesh = grounding.get('visual_mesh','')
        anchor = next((link for link in obj.links.values()
                       if mesh.startswith(str(link.prim_path)+'/')),obj)
        pos,quat = anchor.get_position_orientation()
        world = tuple(map(float,point))
        local = rotate(tuple(v-float(p) for v,p in zip(world,pos)),quat,inverse=True)
        return cls(obj,anchor,local,world,dict(grounding))

    def resolve(self,objects,max_drift):
        if not any(o is self.obj for o in objects):
            raise SkillError('target_changed','Bound target is no longer in this scene')
        if self.anchor is not self.obj and not any(link is self.anchor for link in self.obj.links.values()):
            raise SkillError('target_changed','Bound target link was replaced')
        try:
            pos,quat = self.anchor.get_position_orientation()
            offset = rotate(self.local_point,quat)
            point = tuple(float(p)+d for p,d in zip(pos,offset))
        except SkillError:raise
        except Exception as exc:
            raise SkillError('target_changed','Cannot resolve the bound surface') from exc
        if not all(math.isfinite(v) for v in point) or math.dist(point,self.initial_point)>max_drift:
            raise SkillError('target_changed','Selected surface moved outside tracking tolerance')
        return self.obj,point

    def describe(self):
        return {'object_prim':str(self.obj.prim_path),'anchor_prim':str(self.anchor.prim_path),
                'local_point':self.local_point,'initial_point':self.initial_point,
                'image_ref':self.grounding.get('image_ref'),'pixel':self.grounding.get('selected_pixel'),
                'scope':'one_act_private_link_relative_target'}
