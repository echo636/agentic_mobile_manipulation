"""CPU-only geometry used by parameterized learned procedures."""
import math


def add(a, b): return [x + y for x, y in zip(a, b)]
def sub(a, b): return [x - y for x, y in zip(a, b)]
def scale(a, value): return [x * value for x in a]
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]

def unit(vector):
    size = math.sqrt(sum(x*x for x in vector))
    if not math.isfinite(size) or size < 1e-8: raise ValueError('Degenerate direction')
    return [x/size for x in vector]

def quaternion(q):
    size = math.sqrt(sum(x*x for x in q))
    if len(q) != 4 or not math.isfinite(size) or size < 1e-8: raise ValueError('Invalid quaternion')
    return [x/size for x in q]

def multiply(a, b):
    x,y,z,w=a; X,Y,Z,W=b
    return quaternion([w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
                       w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z])

def rotate(q, v):
    q=quaternion(q);t=scale(cross(q[:3],v),2)
    return add(v,add(scale(t,q[3]),cross(q[:3],t)))

def point(pose, offset): return add(pose['position_m'], rotate(pose['quaternion_xyzw'],offset))

def pose(position, orientation):
    return {'position_m': list(position), 'quaternion_xyzw': quaternion(orientation), 'gripper': 'hold'}

def linear_path(start, end, orientation, spacing=.12):
    count=max(1,math.ceil(math.dist(start,end)/spacing))
    if count>30: raise ValueError('Path outside supported local-transfer range')
    return [pose(add(start,scale(sub(end,start),i/count)),orientation) for i in range(1,count+1)]

def hinge_path(eef, origin, axis, angle, spacing=.04):
    axis=unit(axis);count=max(1,math.ceil(abs(angle)/spacing));rows=[]
    for i in range(1,count+1):
        a=angle*i/count;q=[*(x*math.sin(a/2) for x in axis),math.cos(a/2)]
        rows.append(pose(add(origin,rotate(q,sub(eef['position_m'],origin))),multiply(q,eef['quaternion_xyzw'])))
    return rows

def object_by_id(observation, ident):
    result=next((o for o in observation['oracle_3d']['objects'] if o['id']==ident),None)
    if result is None: raise ValueError('Target absent from current observation: '+ident)
    return result

def retained(observation, arm, ident, link=None):
    hand=observation['oracle_3d']['robot']['arms'][arm];native=hand.get('native_grasp',{})
    return (hand.get('held_object')==ident and native.get('object_id')==ident and
            (link is None or native.get('target_link')==link) and native.get('mode')=='assisted')
