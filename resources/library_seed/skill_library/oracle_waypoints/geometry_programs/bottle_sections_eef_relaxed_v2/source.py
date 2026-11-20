"""Model-authored EEF-only relaxed section sampler, revision 2; bounded diverse candidate set.
Adapted from bottle_section_envelopes; original versions and failures preserved.
Credit: harness_reference bottle_grasps supplies exact mesh-section contacts and
measured contact-offset convention. New orientation sampling, soft envelope ranking,
bidirectional base bounds, and ranking below are model adaptations.
"""
import numpy as np
from manip_reference import bottle_grasps

def quaternion_xyzw(rotation):
    # Eigenvector form remains well-conditioned for half turns.
    r = np.asarray(rotation)
    k = np.array([
        [r[0,0]-r[1,1]-r[2,2], r[1,0]+r[0,1], r[2,0]+r[0,2], r[2,1]-r[1,2]],
        [r[1,0]+r[0,1], r[1,1]-r[0,0]-r[2,2], r[2,1]+r[1,2], r[0,2]-r[2,0]],
        [r[2,0]+r[0,2], r[2,1]+r[1,2], r[2,2]-r[0,0]-r[1,1], r[1,0]-r[0,1]],
        [r[2,1]-r[1,2], r[0,2]-r[2,0], r[1,0]-r[0,1], np.trace(r)]]) / 3
    _, vectors = np.linalg.eigh(k)
    q = vectors[:, -1]
    return (q if q[3] >= 0 else -q).tolist()


def rotation_xyzw(q):
    x, y, z, w = np.asarray(q) / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])




def corners(box):
    lo=np.asarray(box['min_m']); hi=np.asarray(box['max_m'])
    return np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])

def generate(task, oracle, parameters):
    obj=next(o for o in oracle['objects'] if o['id']==parameters['object_id'])
    arm=parameters['arm']; hand=oracle['robot']['arms'][arm]
    g=hand['grasp_geometry']; mesh=obj['collision_mesh']
    pts=np.asarray(mesh['vertices_m']); hp=np.asarray(hand['eef_pose']['position_m'])
    offset=np.asarray(g['contact_offset_eef_m']); aperture=g['aperture_m']
    Rnow=rotation_xyzw(hand['eef_pose']['quaternion_xyzw'])
    ref=bottle_grasps(pts,hp,offset,aperture,faces=mesh['faces'])
    center=(pts.min(0)+pts.max(0))/2
    _,axes=np.linalg.eigh(np.cov((pts-center).T))
    up=axes[:,-1]; up*=1 if up[2]>=0 else -1
    height=np.ptp(pts@up); support=float(pts[:,2].min())
    contacts=[]
    for c in ref:
        if not any(np.linalg.norm(c.contact-p)<.004 for p in contacts): contacts.append(c.contact)
    body=oracle['robot']['body_links']
    base=next(b['aabb'] for b in body if b['link']=='base_link')
    blo=np.asarray(base['min_m']); bhi=np.asarray(base['max_m'])
    shoulder=next(b['aabb']['center_m'] for b in body if b['link']==arm+'_arm_link1')
    opposite='right' if arm=='left' else 'left'
    blockers=[b['aabb'] for b in body if b['link'].startswith(opposite+'_')]
    fingers=np.concatenate([corners(f['aabb_in_eef']) for f in hand['finger_geometry']])
    wristbox=next(b['aabb'] for b in body if b['link']==arm+'_gripper_link')
    wristlocal=(corners(wristbox)-hp)@Rnow
    # Radial directions include both the hand side and the side away from the base.
    radial=center-np.array([(blo[0]+bhi[0])/2,(blo[1]+bhi[1])/2,center[2]])
    radial-=up*np.dot(radial,up); radial/=np.linalg.norm(radial)
    tangent=np.cross(up,radial)
    floor_object=support<.15
    tilts=(.30,.60,.95,1.30,1.50) if floor_object else (0.,.30,.60,.95,1.30)
    results=[]
    for ci,c in enumerate(contacts):
      for ai,az in enumerate(np.arange(0,2*np.pi,np.pi/6)):
        outward=np.cos(az)*radial+np.sin(az)*tangent
        for ti,tilt in enumerate(tilts):
          z=-outward*np.cos(tilt)-up*np.sin(tilt)
          jaw=np.cross(up,-outward); jaw/=np.linalg.norm(jaw)
          for roll in (0.,np.pi/2,np.pi,3*np.pi/2):
            y=jaw*np.cos(roll)+np.cross(z,jaw)*np.sin(roll)
            x=np.cross(y,z); R=np.column_stack((x,y,z))
            # Include a finger-depth band, not a guessed bottle neck diameter.
            band=pts[np.abs((pts-c)@up)<.018]
            if len(band)<4: continue
            width=float(np.ptp(band@y))
            if not .006<width<aperture-.003: continue
            eef=c-R@offset
            pre=eef-z*(.085 if floor_object else .10)
            fp=fingers@R.T+eef; wp=wristlocal@R.T+eef
            if min(fp[:,2].min(),wp[:,2].min())<support+.0025: continue
            # Correct finite box test: behind the base is not automatically inside.
            def overlap(a, box, margin):
                return np.all(a.max(0)>np.asarray(box['min_m'])-margin) and np.all(a.min(0)<np.asarray(box['max_m'])+margin)
            envelope_penalty=.30*int(overlap(wp,base,.0025))+.15*sum(int(overlap(wp,b,.0025)) for b in blockers)
            # Penalize wrist travel and rotation, shoulder extension, and low contact.
            rot=np.arccos(np.clip((np.trace(Rnow.T@R)-1)/2,-1,1))
            score=float(envelope_penalty+np.linalg.norm(pre-hp)+.10*rot+.20*np.linalg.norm(eef-shoulder)+.12*(1-(c[2]-support)/height))
            q=quaternion_xyzw(R); name='section%d_az%d_tilt%d_roll%d'%(ci,ai,ti,round(roll*100))
            def phase(s,p,grip,ct):
                return dict(name=name+'_'+s,arm=arm,frame='robot_base',goal=dict(position_m=p.tolist(),quaternion_xyzw=q),gripper=grip,contact_target=ct)
            results.append(dict(name=name,score=score,phases=[phase('approach',pre,'hold',None),phase('contact',eef,'close',dict(object_id=obj['id'],link_name='base_link'))]))
    if not results: raise ValueError('No mesh-section grasp clears support/base/wrist envelopes at this station')
    # Preserve section/azimuth/jaw diversity under the API's 256-candidate limit.
    ordered=sorted(results,key=lambda x:x['score'])
    groups={}
    for item in ordered:
        parts=item['name'].split('_')
        key=(parts[0],parts[1],parts[3])
        groups.setdefault(key,[]).append(item)
    selected=[]
    for depth in range(5):
        layer=sorted([v[depth] for v in groups.values() if len(v)>depth],key=lambda x:x['score'])
        selected.extend(layer[:256-len(selected)])
        if len(selected)>=256: break
    return dict(candidates=sorted(selected,key=lambda x:x['score']),prepare_gripper=dict(arm=arm,command='open'))
