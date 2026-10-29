"""Validate one private placement checkpoint against its original failing trace."""
import argparse
import json
from pathlib import Path
from probe_navigation_prefix import load_plan,execute


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    plan=load_plan(args.trial,26);plan['actions']=plan['actions'][-1:]
    action=plan['actions'][0]
    if action['original_event']!='event-00099':raise ValueError('Wrong checkpoint case')
    action['expected_ok']=False;action['expected_error']='postcondition_error'
    plan['checkpoint_scope']='Private one-action failure reproduction; not full episode resume or benchmark'
    def restore(backend,action,record):
        state=backend.torch.load(args.checkpoint,weights_only=False)
        backend.og.sim.load_state(state['sim'],serialized=False)
        carry=state['carry']
        backend._ideal_held=backend.env.scene.object_registry('name',carry['held_name'])
        backend._carry_relative=carry['_carry_relative'];backend._carry_orientation=carry['_carry_orientation']
        backend.steps=state['env_steps'];backend.frames_revision=-1
        record['restoration']={'checkpoint':str(args.checkpoint),'env_steps':backend.steps,
            'held':carry['held_name'],'limitations':'Other Python-side episode/task state is not restored; only this diagnostic chain is compared.'}
    def compare(backend,row,record):
        read=lambda p:[json.loads(x) for x in p.read_text().splitlines()]
        actual=[x for x in read(args.output/'placement_diagnostics.jsonl') if x['status']=='placement_egress_check']
        expected=[x for x in read(args.reference/'placement_diagnostics.jsonl') if x['status']=='placement_egress_check']
        same=len(actual)==len(expected)==2
        if same:
            same=all(a['accepted']==b['accepted'] and a['disconnected_cells']==b['disconnected_cells']
                and max(abs(x-y) for aa,bb in zip(a['aabb'],b['aabb']) for x,y in zip(aa,bb))<=1e-4
                for a,b in zip(actual,expected))
        record['checkpoint_comparison']={'same_failure_and_aabb':same,'aabb_tolerance_m':1e-4,
                                         'actual_checks':actual,'expected_checks':expected}
        return same
    execute(plan,args.output,before_action=restore,after_action=compare)


if __name__=='__main__':main()
