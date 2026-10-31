"""Private recorded-prefix probe; never a model benchmark attempt.

Prepare-only needs no simulator. Execution requires the archived BDDL/data
and rendering environment already configured, and refuses mismatched inputs.
An action/grounding divergence stops replay rather than reporting a repair.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys


def load_plan(trial, limit):
    trial=Path(trial)
    events=[json.loads(line) for line in (trial/'run/events.jsonl').open()]
    ground=[json.loads(line) for line in (trial/'run/grounding_diagnostics.jsonl').open()]
    run=json.loads((trial/'run/run.json').read_text())
    summary=json.loads((trial/'summary.json').read_text())
    calls=[e for e in events if e['kind']=='tool_call' and e.get('name') in ('act','look')][:limit]
    if len(calls)!=limit:raise ValueError('Requested prefix is longer than recorded motor sequence')
    actions=[]
    for call in calls:
        result=next(e for e in events if e['kind']=='tool_result' and e.get('request_id')==call['request_id'])
        target=call['arguments'].get('target')
        matches=[g for g in ground if target and call['at']<=g['at']<=result['at'] and g.get('image_ref')==target['image_ref'] and g.get('selected_pixel')==target['point']]
        if target and not matches:raise ValueError('Missing recorded grounding: '+call['id'])
        original=result['result']
        actions.append({'original_event':call['id'],'arguments':call['arguments'],
            'expected_ok':original['ok'],'expected_error':original.get('error',{}).get('code'),
            'grounding':matches[0] if matches else None})
    return {'schema_version':1,'policy':'scripted_private_recorded_prefix',
        'model_used':False,'task_success_assessed':False,'not_a_benchmark_attempt':True,
        'historical_trial':str(trial.resolve()),'task':summary['task'],'instance':summary['instance'],'seed':summary['seed'],
        'bddl_sha256':hashlib.sha256((trial/'task_spec.bddl').read_bytes()).hexdigest(),
        'required_input_hashes':run['backend']['input_hashes'],
        'grounding_position_tolerance_m':.05,'actions':actions,
        'limits':['Feedback/grounding gates do not prove equality of full physical state.',
                  'No task-success or repair claim from a completed prefix.']}


def grounding_matches(expected, actual, tolerance):
    if expected['selected_object_prim']!=actual.get('selected_object_prim'):return False
    p=expected['hit_position'];q=actual.get('hit_position')
    return bool(q and len(p)==len(q)==3 and all(math.isfinite(v) for v in [*p,*q]) and math.dist(p,q)<=tolerance)


def execute(plan, output, *, after_action=None, before_action=None):
    # Imports only after prepare/plan validation; environment must pin BDDL overlay.
    from manipulation_agent.contracts import SkillError
    from manipulation_agent.executors.omnigibson_rgb import RGBBackend
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    backend=None;record={'status':'incomplete','model_used':False,'task_success_assessed':False,'actions':[]}
    try:
        backend=RGBBackend(plan['task'],plan['instance'],output,seed=plan['seed'],record_video=True,inside_placement='official_volume')
        provenance=backend.provenance();(output/'backend_provenance.json').write_text(json.dumps(provenance,indent=2))
        required=plan['required_input_hashes']
        if any(provenance['input_hashes'].get(k)!=v for k,v in required.items()):
            raise ValueError('Scene/task input hashes differ from the historical episode')
        if provenance.get('demo_motion') is not False:raise ValueError('Requires frozen no-demo motor protocol')
        for action in plan['actions']:
            if before_action is not None:before_action(backend,action,record)
            args=copy.deepcopy(action['arguments']);target=args.get('target')
            if target:
                obs=backend.observe();view=target['image_ref'].rsplit('-',1)[-1]
                target['image_ref']=next(im['image_ref'] for im in obs['images'] if im['view']==view)
                _,_,ground=backend._ground(target)
                if not grounding_matches(action['grounding'],ground,plan['grounding_position_tolerance_m']):
                    record.update(status='prefix_diverged',diverged_event=action['original_event'],reason='grounding')
                    record['actual_grounding']=ground
                    break
            primitive=args.get('primitive','look');ok=True;error=None
            try:
                backend.execute_visual(primitive,target,700,**({'yaw_degrees':args['yaw_degrees']} if primitive=='look' and 'yaw_degrees' in args else {}))
            except SkillError as exc:ok=False;error=exc.code
            row={'original_event':action['original_event'],'primitive':primitive,'ok':ok,'error':error,'sim_steps':backend.steps}
            record['actions'].append(row)
            if (ok,error)!=(action['expected_ok'],action['expected_error']):
                record.update(status='prefix_diverged',diverged_event=action['original_event'],reason='action_feedback');break
            if after_action is not None and not after_action(backend,row,record):
                record.update(status='private_acceptance_failed',diverged_event=action['original_event']);break
        else:record['status']='recorded_feedback_prefix_reproduced'
        record['video']=backend.finalize_video()
    finally:
        (output/'validation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
        if backend is not None:backend.close()
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=34)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    if args.limit<1:parser.error('limit must be positive')
    plan=load_plan(args.trial,args.limit)
    if args.prepare_only:
        with args.output.open('x') as stream:json.dump(plan,stream,ensure_ascii=False,indent=2)
        print('Prepared private prefix:',len(plan['actions']),'actions; no simulator launched')
        return 0
    record=execute(plan,args.output)
    print(json.dumps({'status':record['status'],'model_used':False}))
    return 0 if record['status']=='recorded_feedback_prefix_reproduced' else 1


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    raise SystemExit(main())
