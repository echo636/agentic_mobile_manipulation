"""CPU-only check for the actual rejected-act audit crash; no simulator."""
from pathlib import Path
import sys,importlib.util
p=Path(__file__).resolve().parents[1]/"runtime/without_long_horizon"
sys.path.insert(0,str(p/"src"))
spec=importlib.util.spec_from_file_location("audit_check",p/"scripts/audit_demo_trial.py")
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
call={"kind":"tool_call","name":"act","arguments":{"outcome":"blocked"}}
failed={"kind":"tool_result","name":"act","result":{"ok":False}}
r=a.replay_high_level_actions([call,failed],[])
assert r["committed_actions"]==0 and not r["evaluation"]["task_success"]
try:a.replay_high_level_actions([call,{**failed,"result":{"ok":True}}],[])
except ValueError:pass
else:raise AssertionError("Missing primitive on successful action was accepted")
print("PASS: rejected malformed action does not commit; malformed successful action is rejected")
