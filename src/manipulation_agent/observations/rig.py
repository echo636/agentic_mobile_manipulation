"""Four fixed camera extrinsics in the robot base frame (X forward, Z up)."""
import math

DIRECTIONS = ('front', 'back', 'left', 'right')
YAW_DEGREES = {'front': 0, 'back': 180, 'left': 90, 'right': -90}


def camera_mount(direction, height, radius=0.35, pitch_degrees=20):
    yaw, pitch = math.radians(YAW_DEGREES[direction]), math.radians(pitch_degrees)
    c, s, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right = (s, -c, 0)
    up = (c*sp, s*sp, cp)
    forward = (c*cp, s*cp, -sp)
    # USD cameras see along local -Z; columns are right, up and backward.
    rotation = [[right[i], up[i], -forward[i]] for i in range(3)]
    return [radius*c, radius*s, height], rotation


def visible_rig_rays(xy, yaw, base_z, height, point, margin=.04, radius=.35):
    """Candidate camera rays inside the real square 90-degree RGB frusta.

    Uses the same four mount transforms as capture. Does not read scene truth,
    move cameras, or claim that a geometrically in-frame point is unoccluded.
    """
    c,s=math.cos(yaw),math.sin(yaw)
    rotation=((c,-s,0.),(s,c,0.),(0.,0.,1.))
    rays=[]
    for direction in DIRECTIONS:
        offset,basis=camera_mount(direction,height,radius=radius)
        origin=[sum(rotation[i][j]*offset[j] for j in range(3))+(*xy,base_z)[i] for i in range(3)]
        world_basis=[[sum(rotation[i][k]*basis[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        delta=[p-o for p,o in zip(point,origin)]
        local=[sum(world_basis[i][j]*delta[i] for i in range(3)) for j in range(3)]
        depth=-local[2]
        if depth<=.02:continue
        u=.5+.5*local[0]/depth;v=.5-.5*local[1]/depth
        if margin<=u<=1-margin and margin<=v<=1-margin:rays.append((direction,origin,(u,v)))
    return rays


def look_at_orientation(camera, target):
    """Finite, normalized USD camera quaternion (xyzw), without torch compilation."""
    if not all(math.isfinite(v) for v in (*camera, *target)):
        raise ValueError('Nonfinite spectator pose')
    def unit(v):
        n = math.sqrt(sum(x*x for x in v))
        if n < 1e-10:
            raise ValueError('Degenerate spectator direction')
        return [x/n for x in v]
    def cross(a,b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    forward = unit([t-c for t,c in zip(target,camera)])
    reference = [0.,0.,1.] if abs(forward[2]) < .99 else [0.,1.,0.]
    right = unit(cross(forward,reference))
    up = unit(cross(right,forward))
    m = [[right[i],up[i],-forward[i]] for i in range(3)]
    trace = sum(m[i][i] for i in range(3))
    if trace > 0:
        s = 2*math.sqrt(trace+1)
        q = [(m[2][1]-m[1][2])/s,(m[0][2]-m[2][0])/s,(m[1][0]-m[0][1])/s,s/4]
    else:
        i = max(range(3),key=lambda k:m[k][k]); j=(i+1)%3; k=(i+2)%3
        s = 2*math.sqrt(1+m[i][i]-m[j][j]-m[k][k])
        q = [0.,0.,0.,(m[k][j]-m[j][k])/s]
        q[i]=s/4; q[j]=(m[j][i]+m[i][j])/s; q[k]=(m[k][i]+m[i][k])/s
    return unit(q)
