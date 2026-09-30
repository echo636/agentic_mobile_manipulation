"""Offline reference-policy parity and archived navigation regression checks."""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.executors.gt_navigation import (
    GridMap, GreedyGridFollower, candidate_points, candidate_score, plan_navigation,
)


def reference_parity(reference):
    import numpy as np
    path=reference/'tools/habitat_agent/oracle_local_nav/visual_point.py'
    tree=ast.parse(path.read_text())
    names={'_candidate_points_for_hint','_validate_candidate','_horizontal_distance',
           '_approach_plane_margin','_approach_side_margin','_terminal_path_alignment_degrees'}
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(selected)==len(names)
    # Execute only pure geometry helpers, not the Habitat runtime or tool server.
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*selected],type_ignores=[])
    class Path:
        pass
    namespace={'np':np,'math':math,'habitat_sim':SimpleNamespace(ShortestPath=Path)}
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),namespace)
    rng=random.Random(0);max_error=0.;checked=0
    for _ in range(100):
        current=np.array([rng.uniform(-10,10),0.,rng.uniform(-10,10)],dtype=np.float32)
        hint=np.array([rng.uniform(-10,10),1.2,rng.uniform(-10,10)],dtype=np.float32)
        expected=namespace['_candidate_points_for_hint'](hint=hint,current_position=current,standoff_m=.7)
        actual=candidate_points((float(hint[0]),float(hint[2])),(float(current[0]),float(current[2])))
        assert len(actual)==len(expected)
        for a,(point,_) in zip(actual,expected):
            error=math.dist(a,(float(point[0]),float(point[2])));max_error=max(max_error,error)
            assert error<3e-6
            checked+=1
    for distance in (.1,.44,.45,1.,10.):
        point=np.array([1.,0.,2.],dtype=np.float32);hint=np.array([1.6,1.,2.],dtype=np.float32)
        snapped=point+np.array([.03,0.,.02],dtype=np.float32)
        def find_path(p):p.geodesic_distance=distance;p.points=[point,snapped];return True
        pf=SimpleNamespace(snap_point=lambda _:snapped,find_path=find_path)
        expected=namespace['_validate_candidate'](point=point,hint=hint,current_position=np.zeros(3,dtype=np.float32),pathfinder=pf,horizon_m=10000.,standoff_m=.7)
        target_dist=namespace['_horizontal_distance'](snapped,hint)
        snap_dist=namespace['_horizontal_distance'](point,snapped)
        assert candidate_score(target_dist,snap_dist,distance)==expected['score']
    return {'status':'passed','candidate_coordinates_checked':checked,'max_coordinate_difference_m':max_error,
            'score_cases':5,'reference_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def unproject(folder,target):
    import numpy as np
    name=target['image_ref'];meta=json.loads((folder/'executor_frames'/(name+'.json')).read_text())
    with np.load(folder/'executor_frames'/(name+'.npz')) as f:depth=f['depth_linear']
    h,w=depth.shape;px=round(target['point'][0]*(w-1));py=round(target['point'][1]*(h-1))
    k=np.asarray(meta['intrinsic']);x,y,z,wq=meta['orientation']
    rotation=np.array([[1-2*(y*y+z*z),2*(x*y-z*wq),2*(x*z+y*wq)],
                       [2*(x*y+z*wq),1-2*(x*x+z*z),2*(y*z-x*wq)],
                       [2*(x*z-y*wq),2*(y*z+x*wq),1-2*(x*x+y*y)]])
    ray=np.array([(px-k[0,2])/k[0,0],-(py-k[1,2])/k[1,1],-1.])
    return np.asarray(meta['position'])+rotation@ray*float(depth[py,px])


def archived_checks(batch,maps_root):
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    diagnosis=json.loads((batch/'navigation_grid_diagnosis_20260930.json').read_text())
    cache={};rows=[]
    for error in diagnosis['rows']:
        folder=batch/'runs'/error['run_id'];scene=error['scene']
        if scene not in cache:
            cfg=json.loads((folder/'environment_config.json').read_text())['scene'];res=cfg['trav_map_resolution']
            image=cv2.imread(str(maps_root/scene/'layout/floor_trav_0.png'),cv2.IMREAD_GRAYSCALE)
            size=int(image.shape[0]*.01/res);image=cv2.resize(image,(size,size));image[image<255]=0
            pixels=math.ceil(cfg['default_erosion_radius']/res)
            image=cv2.erode(image,np.ones((pixels,pixels),dtype=np.float32))
            cache[scene]=GridMap(size,size,res,(-size*res/2,-size*res/2),(image!=0).astype('uint8').tobytes())
        grid=cache[scene]
        events=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
        call=next(e for e in events if e['id']==error['call_event'])
        target=unproject(folder,call['arguments']['target'])
        row={'run_id':error['run_id'],'event':error['call_event'],'task_index':error['task_index'],
             'old_failure':'invalid_start','start':error['position'],'selected_target_world':target.tolist()}
        try:
            plan=plan_navigation(grid,error['position'][:2],target[:2])
            follower=GreedyGridFollower(grid,plan,1/30)
            pose=(*error['position'][:2],0.);steps=0
            while steps<=5000:
                command=follower.next_pose(pose)
                if command is None:break
                pose=command;steps+=1
            assert steps<=5000 and math.dist(pose[:2],plan.goal)<=.002
            row.update(status='passed',follower_steps=steps,within_700_control_steps=steps+10<=700,
                       planned_path_m=plan.geodesic_m,goal=list(plan.goal),final_position=list(pose),
                       candidate_count=plan.candidates_considered)
        except Exception as exc:row.update(status='failed',error=type(exc).__name__+': '+str(exc))
        rows.append(row)
    return {'status':'passed' if all(r['status']=='passed' for r in rows) else 'failed',
            'counts':dict(Counter(r['status'] for r in rows)),'rows':rows,
            'scope':'Original static maps and archived pixels/depth/poses; perfect pose-update follower only, not simulator or task success'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--batch',type=Path,required=True);p.add_argument('--maps-root',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result={'at':datetime.now(timezone.utc).isoformat(),'host':os.uname().nodename,'pid':os.getpid(),
            'interpreter':sys.executable,'gpu_uuid':None,'validation_level':'offline_reference_parity_and_archived_grid_navigation',
            'reference_parity':reference_parity(a.reference),'archived_regression':archived_checks(a.batch,a.maps_root)}
    result['status']='passed' if all(result[k]['status']=='passed' for k in ('reference_parity','archived_regression')) else 'failed'
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='archived_regression'}))
    print(json.dumps(result['archived_regression']['counts']))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
