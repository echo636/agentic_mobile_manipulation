"""Private GROC-012 settling contacts; no model or benchmark success claim."""
import argparse
import json
from pathlib import Path
from probe_navigation_prefix import load_plan, execute


def inspect_step(backend, item, index, output):
    from omnigibson.utils.usd_utils import RigidContactAPI
    lo,hi=item.aabb
    overlaps=[]
    for obj in backend.env.scene.objects:
        if obj is item:continue
        for name,link in obj.links.items():
            a,b=link.aabb
            if bool(((hi>a+.005)&(lo<b-.005)).all()):
                overlaps.append({'object':obj.name,'link':name,'robot':obj is backend.robot,
                                 'fixed_base':bool(getattr(obj,'fixed_base',False))})
    scene_idx=backend.env.scene.idx
    # Before the first step contacts can be stale after pose sampling. Preserve
    # that distinction; AABB intersections alone do not establish collisions.
    pairs=RigidContactAPI.get_contact_pairs(scene_idx=scene_idx,
        query_set={link.prim_path for link in item.links.values()},with_set=None,current_only=True)
    row={'index':index,'env_step':backend.steps,'aabb':[lo.cpu().tolist(),hi.cpu().tolist()],
         'position':item.get_position_orientation()[0].cpu().tolist(),
         'aabb_intersections':overlaps,'contact_pairs':sorted([list(p) for p in pairs]),
         'contacts_may_be_stale':index==0,
         'scene_contact_matrix_present':scene_idx in RigidContactAPI._CONTACT_MATRIX}
    with (output/'settling_contacts.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--item',required=True)
    args=parser.parse_args();plan=load_plan(args.trial,26)
    if plan['actions'][-1]['original_event']!='event-00099':raise ValueError('Wrong case prefix')
    plan['contact_diagnostic']={'scope':'read-only settling trace and pre-action simulator checkpoint',
        'limitations':'Simulator checkpoint plus selected carry fields is not a validated standalone episode resume.'}
    def before(backend,action,record):
        if action['original_event']!='event-00099':return
        item=backend.env.scene.object_registry('name',args.item)
        carry={k:getattr(backend,k,None) for k in ['_carry_relative','_carry_orientation']}
        carry['held_name']=getattr(getattr(backend,'_ideal_held',None),'name',None)
        backend.torch.save({'sim':backend.og.sim.dump_state(serialized=False),'carry':carry,
                            'env_steps':backend.steps},args.output/'pre_placement_state.pt')
        original=backend._step;count=0
        def step(*pos,**kw):
            nonlocal count
            if count==0:inspect_step(backend,item,0,args.output)
            result=original(*pos,**kw);count+=1
            if count<=5 or count in (10,25,50):inspect_step(backend,item,count,args.output)
            return result
        backend._step=step
    execute(plan,args.output,before_action=before)


if __name__=='__main__':main()
