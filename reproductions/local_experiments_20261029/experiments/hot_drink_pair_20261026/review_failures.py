"""Read-only extraction of captured target identities; recovery windows need human review."""
import argparse,json,datetime
from pathlib import Path
from collections import Counter
p=argparse.ArgumentParser();p.add_argument('trial',type=Path);a=p.parse_args();t=a.trial;r=t/'run'
def rows(path):return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
es=rows(r/'events.jsonl');grounds=rows(r/'grounding_diagnostics.jsonl');calls={};actions=[];private=None;laststep=0
for e in es:
 if e.get('kind')=='tool_call' and e.get('name') in ['act','look']:
  calls[e.get('request_id')]=e;private=None
 elif e.get('kind') in ['private_executor_error','private_executor_result']:private=e
 elif e.get('kind')=='tool_result' and e.get('request_id') in calls:
  c=calls[e['request_id']];args=c.get('arguments',{});target=args.get('target') or {};res=e.get('result',{});matches=[g for g in grounds if c['at']<=g.get('at','')<=e['at'] and g.get('image_ref')==target.get('image_ref') and g.get('selected_pixel')==target.get('point')]
  g=matches[-1] if matches else {};step=res.get('observation',{}).get('capture',{}).get('sim_step',laststep)
  actions.append({'action':len(actions)+1,'request_id':e['request_id'],'name':c['name'],'primitive':args.get('primitive','look'),'ok':res.get('ok'),'error':res.get('error'),'decision':args.get('decision'),'target':target,'selected_object_prim':g.get('selected_object_prim'),'sim_step':step,'sim_steps_delta':step-laststep,'motor_seconds':(datetime.datetime.fromisoformat(e['at'])-datetime.datetime.fromisoformat(c['at'])).total_seconds(),'private_executor_evidence':private})
  laststep=step
failures=[]
for i,c in enumerate(actions):
 if c['ok'] is not False:continue
 same=[n for n in actions[i+1:] if c['selected_object_prim'] and n['primitive']==c['primitive'] and n['selected_object_prim']==c['selected_object_prim'] and n['ok'] is True]
 end=same[0] if same else None;window=actions[i+1:end['action']] if end else actions[i+1:]
 failures.append({**c,'same_primitive_same_object_later_success':end['action'] if end else None,'candidate_window_actions':len(window),'candidate_window_motor_seconds':round(sum(n['motor_seconds'] for n in window),3),'candidate_window_sim_steps':sum(n['sim_steps_delta'] for n in window),'caution':'Identity-matched later success is a candidate, not proof of causal recovery. Windows overlap and must not be summed.'})
out={'trial':str(t.resolve()),'motor_actions_including_look':len(actions),'failed_motor_actions':len(failures),'error_counts':dict(Counter((x['error'] or {}).get('code') for x in failures)),'failures':failures,'actions':actions}
(t/'failure_target_inventory.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:out[k] for k in ['motor_actions_including_look','failed_motor_actions','error_counts']}))
