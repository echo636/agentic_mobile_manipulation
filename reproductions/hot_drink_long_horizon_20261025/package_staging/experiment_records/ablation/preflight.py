import pathlib,json,os,hashlib,tempfile,importlib.util
N=pathlib.Path(__file__).resolve().parent;S=N/'execution';os.environ['MAS_LONG_HORIZON_PLANNING_SKILL']='1';os.environ['MAS_HIGH_LEVEL_HARNESS']='1'
from manipulation_agent.skill_runtime import SkillLibrary
from manipulation_agent.vision_policy import system_prompt
from manipulation_agent.tools import tool_specs
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
p=pathlib.Path(tempfile.mkdtemp())/'episode';h=VisionHarness(MockRGBBackend(p),Recorder(p,{}),profile='planning')
expected={'failure-recovery','pick-and-place','visual-exploration','visual-manipulation'};assert {e['name'] for e in h.skills.catalog()}==expected
specs=h.tool_specs();assert {t['name'] for t in specs}==set(json.load(open(N/'plan.json'))['expected_tools']);assert all('decision' in t['inputSchema']['required'] for t in specs)
assert h.call('initialize',{},'no-intent')['error']['code']=='invalid_arguments'
d={'state':'Initial RGB not yet inspected','active_subgoal':'Inspect prepared scene','reason':'Get current four-camera images','expected':'Four synchronized RGB views'};assert h.call('initialize',{'decision':d},'intent')['ok']
from manipulation_agent.contracts import SkillError
try:h.skills.read('long-horizon-planning','SKILL.md')
except SkillError:pass
else:raise AssertionError('Removed long-horizon document was accessible')
for e in h.skills.catalog():assert h.skills.read(e['name'],'SKILL.md')['text']
parent=json.load(open(N.parent/'hot_drink_optimization_20261022/baseline_manifest.json'));baseline=pathlib.Path(parent['source']);other_files=[]
for file in (S/'skills').rglob('*.md'):
 rel=str(file.relative_to(S));assert hashlib.sha256(file.read_bytes()).hexdigest()==parent['sha256'][rel];other_files.append(rel)
prompt=system_prompt('planning');assert 'long-horizon-planning/' not in prompt;assert 'visual-manipulation/SKILL.md' in prompt;assert 'Every tool call requires a short public decision object' in prompt
(N/'system_prompt.txt').write_text(prompt)
# Auditing regression from the actual prior no-skill rejection.
spec=importlib.util.spec_from_file_location('audit_preflight',S/'scripts/audit_demo_trial.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
call={'kind':'tool_call','name':'act','arguments':{'outcome':'blocked'}};rejected={'kind':'tool_result','name':'act','result':{'ok':False}}
assert a.replay_high_level_actions([call,rejected],[])['evaluation']['task_success'] is False
try:a.replay_high_level_actions([call,{**rejected,'result':{'ok':True}}],[])
except ValueError:pass
else:raise AssertionError('Malformed successful action must be rejected')
f=N/'frozen';f.mkdir();library=SkillLibrary(f)
m=json.load(open(N/'manifest.json'));m['skill_bundle_sha256']=library.digest;m['validation']={'passed':True,'four_skills_byte_identical_files':other_files,'nine_tools_unchanged':True,'all_require_intent':True,'missing_intent_rejected':True,'long_horizon_resource_unavailable':True,'rejected_malformed_action_replay_checked':True,'successful_missing_primitive_rejected':True};(N/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(m['validation'],ensure_ascii=False));print('bundle',library.digest)
