"""Recompute the six-trial table from recorded final evaluations, not a new run."""
from pathlib import Path
import json,statistics
p=Path(__file__).resolve().parents[1];rows=[]
for arm in ["with_long_horizon","without_long_horizon"]:
 for seed in [0,11,22]:
  root=p/"evidence_metadata"/(arm+"_s"+str(seed))
  run=json.loads((root/"run/run.json").read_text());audit=json.loads((root/"demo_audit.json").read_text())
  rows.append(dict(arm=arm,seed=seed,success=audit["semantic_audit"]["evaluation"]["task_success"],actions=run["actions"]))
for arm in ["with_long_horizon","without_long_horizon"]:
 a=[r for r in rows if r["arm"]==arm];print(arm,"success",sum(x["success"] for x in a),"/",len(a),"mean_actions",statistics.mean(x["actions"] for x in a))
assert sum(r["success"] for r in rows if r["arm"]=="with_long_horizon")==3
assert sum(r["success"] for r in rows if r["arm"]=="without_long_horizon")==0
