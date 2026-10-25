"""Read original evidence; collect source/configuration inventory without simulator or uploads."""
from pathlib import Path
import json,hashlib,shutil,subprocess,datetime,ast,os,sys,tempfile,collections
W=Path(__file__).resolve().parents[2];O=Path(__file__).resolve().parent;B=W/'experiments/hot_drink_optimization_20261022';A=W/'experiments/hot_drink_long_horizon_ablation_20261024';U=W/'behavior_challenge_sunyujie/BEHAVIOR-1K';P=O/'package_staging'
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1024*1024),b''):h.update(x)
 return h.hexdigest()
def jread(p):return json.loads(p.read_text())
def jwrite(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def copy(src,dst):dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
def git(args):return subprocess.run(['git','-C',str(U),*args],capture_output=True,check=True,timeout=90).stdout
if P.exists():raise RuntimeError('Refuse replacing an existing staged package')
P.mkdir();source_checks=[]
versions={'with_long_horizon':jread(B/'baseline_manifest.json'),'without_long_horizon':jread(A/'manifest.json')}
for arm,m in versions.items():
 src=Path(m['source']);dst=P/'runtime'/arm
 for f,h in m['sha256'].items():
  assert digest(src/f)==h,(arm,f);copy(src/f,dst/f)
 copy(B/'baseline_manifest.json' if arm=='with_long_horizon' else A/'manifest.json',P/'manifests'/f'{arm}_original_manifest.json')
 source_checks.append({'arm':arm,'original_source':str(src),'packaged_source':str(dst.relative_to(P)),'files':len(m['sha256']),'sha256':m['sha256'],'bundle':m['skill_bundle_sha256']})
# Physical inputs: the actual non-cache task text and three scene files, not a symlink to a local dataset.
for f in (B/'task_snapshot').rglob('*'):
 if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc':copy(f,P/'task'/f.relative_to(B/'task_snapshot'))
overlay=Path(versions['with_long_horizon']['task_manifest']['dataset_overlay'])
for f,h in versions['with_long_horizon']['task_manifest']['scene_and_catalog_sha256'].items():
 assert digest(overlay/f)==h;copy(overlay/f,P/'dataset_overlay'/f)
# Small scene assets needed by the navigation and camera configuration; current capture, not historical freeze.
layout=overlay/'behavior-1k-assets/scenes/house_single_floor/layout';layout_files=[]
for f in sorted(layout.glob('*.png')):
 copy(f,P/'asset_inventory/current_scene_layout'/f.name);layout_files.append({'source':str(f.resolve()),'package':str((P/'asset_inventory/current_scene_layout'/f.name).relative_to(P)),'sha256_current':digest(f),'historical_hash_recorded':False})
# Enumerate model directories from both supplied templates (a conservative superset of loaded rooms).
objects={}
for f in (B/'task_snapshot/scene/house_single_floor/json').glob('*template*.json'):
 for name,row in jread(f).get('objects_info',{}).get('init_info',{}).items():
  args=row.get('args',{});category=args.get('category');model=args.get('model')
  if category and model:
   key=category+'/'+model;objects.setdefault(key,{'category':category,'model':model,'object_names':set(),'expected_file_hashes':set()});objects[key]['object_names'].add(name)
   if args.get('expected_file_hash'):objects[key]['expected_file_hashes'].add(args['expected_file_hash'])
asset_models=[]
for key,row in sorted(objects.items()):
 root=overlay/'behavior-1k-assets/objects'/key;asset_models.append({**row,'object_names':sorted(row['object_names']),'expected_file_hashes':sorted(row['expected_file_hashes']),'current_directory':str(root.resolve()),'exists_now':root.is_dir(),'status':'required external assets; not copied; exact transitive USD/texture closure not certified'})
asset_roots=[]
for name in ['behavior-1k-assets','omnigibson-robot-assets','2026-challenge-task-instances']:
 root=overlay/name;v=root/'VERSION';asset_roots.append({'name':name,'source':str(root.resolve()),'version':v.read_text().strip() if v.exists() else None,'large_data_external':True})
jwrite(P/'asset_inventory/required_external_assets.json',{'roots':asset_roots,'template_model_candidates':asset_models,'scene_layout_capture':layout_files,'robot':'r1pro; preserve full model and its USD/texture dependency closure','systems':['water and cooked-water support loaded by hot-drink runtime'],'other_inputs':'Base asset metadata; scene floor maps; OmniGibson eval/r1pro.yaml and gello/utils/og_teleop_cfg.py from pinned upstream; task metadata symlink targets.','excluded_sensitive_file_names':['omnigibson.key'],'limitation':'Do not treat version labels or template expected_file_hash as full cryptographic asset freeze. Historical shared assets were not archived completely.'})
# Preserve upstream changes as current recovery snapshot. A commit alone does not include them.
commit=git(['rev-parse','HEAD']).decode().strip();changed=git(['diff','HEAD','--name-only']).decode().splitlines();patch=git(['diff','HEAD','--binary']);(P/'upstream').mkdir();(P/'upstream/BEHAVIOR-1K_current_worktree.patch').write_bytes(patch)
untracked=git(['ls-files','--others','--exclude-standard','--','OmniGibson/omnigibson','bddl3','joylo']).decode().splitlines();current_files=[]
for f in changed+untracked:
 src=U/f;copy(src,P/'upstream/current_changed_files'/f);current_files.append({'path':f,'tracked_change':f in changed,'sha256_current':digest(src),'mtime_utc':datetime.datetime.fromtimestamp(src.stat().st_mtime,datetime.timezone.utc).isoformat()})
# Test patch application on the exact base bytes of every touched tracked file.
with tempfile.TemporaryDirectory() as td:
 root=Path(td)
 for f in changed:
  p=root/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(git(['show',commit+':'+f]))
 patchpath=P/'upstream/BEHAVIOR-1K_current_worktree.patch'
 for apply_args in [['--check'],[]]:subprocess.run(['git','apply',*apply_args,str(patchpath)],cwd=root,check=True,capture_output=True,timeout=90)
 assert all(digest(root/f)==digest(U/f) for f in changed)
jwrite(P/'upstream/provenance.json',{'repository':'https://github.com/StanfordVL/BEHAVIOR-1K.git','base_commit':commit,'tracked_changes':len(changed),'untracked_code_files':len(untracked),'files':current_files,'patch_apply_and_file_hash_check_passed':True,'historical_exactness':'NOT PROVEN: original episodes recorded commit but no dirty-tree patch/hash. Current file mtimes predate the compared runs, which is supporting metadata only. Do not label this patch historically verified.','dependency_roots':['OmniGibson','bddl3','joylo'],'why_untracked_matters':'tasks/__init__.py imports untracked tasks/partial_order_behavior_task.py at module import; it must be available even though optional evaluator was not the scoring protocol.'})
# Actual six-run records: lightweight reproducibility and scoring metadata copied; large raw logs inventoried.
rows=[];raw_inventory=[];deps=[];prompt_comparison={};model_versions=set();backend_commits=set();source_snapshot_mismatches=[]
for arm,m in versions.items():
 for seed in [0,11,22]:
  trial=(W/'experiments'/f'hot_drink_opt_baseline_gpt61sol_low_20261022_s{seed}') if arm=='with_long_horizon' else A/f'without_long_horizon_s{seed}'
  label=arm+'_s'+str(seed);r=trial/'run';c=trial/'controller';run=jread(r/'run.json');summary=jread(trial/'summary.json');audit=jread(trial/'demo_audit.json');controller=jread(c/'controller.json');dp=jread(r/'dependency_versions.json');deps.append((label,dp));model_versions.add(controller.get('client_version'));backend_commits.add(run['backend'].get('omnigibson_source_commit'))
  # Compare actual episode src snapshots to their frozen execution versions.
  for f,h in m['sha256'].items():
   if f.startswith('src/'):
    for prefix in [r/'source_snapshot',c/'source_snapshot']:
     p=prefix/f[4:]
     if not p.is_file() or digest(p)!=h:source_snapshot_mismatches.append({'trial':label,'file':f,'snapshot':str(prefix)})
  selected=['run.json','environment_config.json','dependency_versions.json','render_settings.json','scene_geometry_repair.json','contact_cache_mode.json','runtime_task_binding_validation.json','video.json','skill_manifest.json','posthoc_semantic_audit.json','replay_audit.json','camera_readiness.jsonl','startup_stages.jsonl']
  for f in selected:
   if (r/f).is_file():copy(r/f,P/'evidence_metadata'/label/'run'/f)
  copy(trial/'summary.json',P/'evidence_metadata'/label/'summary.json');copy(trial/'demo_audit.json',P/'evidence_metadata'/label/'demo_audit.json')
  for f in (r/'skill_snapshot').rglob('*.md'):copy(f,P/'evidence_metadata'/label/'skill_snapshot'/f.relative_to(r/'skill_snapshot'))
  # Extract prompt and tool configuration only, never provider authentication settings.
  command=controller.get('command',[]);prompt=None;tool_names=[]
  for arg in command:
   if arg.startswith('developer_instructions='):prompt=json.loads(arg.split('=',1)[1])
   if arg.startswith('mcp_servers.manipulation.enabled_tools='):tool_names=json.loads(arg.split('=',1)[1])
  assert prompt,label
  prompt_comparison[label]={'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'tools':tool_names};path=P/'configs/actual_prompts'/f'{label}.txt';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(prompt)
  controller_public={k:controller.get(k) for k in ['client_version','client_capability','model','model_reasoning_effort','observation_mode','agent_profile','formal_finish_observed','duration_seconds','status','fresh_thread_restarts','provider_history_restarts']};jwrite(P/'evidence_metadata'/label/'controller_public_metadata.json',controller_public)
  goals=audit['semantic_audit']['evaluation'];rows.append({'arm':arm,'seed':seed,'original_trial':str(trial),'task_success':goals['task_success'],'goals_met':sum(goals['semantic_goals'].values()),'formal_model_finish':summary['formal_finish_observed'],'actions':run['actions'],'skill_bundle':run['skill_bundle_sha256'],'actual_upstream_commit':run['backend'].get('omnigibson_source_commit'),'gpu_uuid':run.get('gpu_uuid'),'model':summary['model'],'reasoning_effort':summary['reasoning_effort'],'model_service_host':summary['provider_host'],'bddl_sha256':summary['bddl_sha256'],'public_decision_every_robot_tool':audit['checks'].get('public_decision_every_robot_tool'),'original_full_alignment':audit['checks']['model_simulator_calls_align']})
  # Inventory required raw evidence, excluding credentials, native sessions, scratch, and duplicate source snapshots.
  for root,dirs,files in os.walk(trial):
   dirs[:]=[d for d in dirs if d not in ['source_snapshot','skill_snapshot','empty_workspace','bddl_overlay','__pycache__']]
   for name in files:
    f=Path(root)/name;rel=f.relative_to(trial)
    if any(part in {'tmp','auth.json','native_sessions'} for part in rel.parts) or name.endswith('.lock'):continue
    raw_inventory.append({'trial':label,'relative_path':str(rel),'source':str(f),'bytes':f.stat().st_size,'evidence_archive_required':str(rel).startswith(('run/','controller/')),'copied_in_metadata_stage':(P/'evidence_metadata'/label/rel).exists()})
assert not source_snapshot_mismatches,source_snapshot_mismatches[:3]
assert len(model_versions)==1 and len(backend_commits)==1
# There should be one prompt per arm, same tool definitions, and exact expected prompt subtraction.
for arm in versions:assert len({d['prompt_sha256'] for k,d in prompt_comparison.items() if k.startswith(arm+'_')})==1
assert len({tuple(v['tools']) for v in prompt_comparison.values()})==1
p1=(P/'configs/actual_prompts/with_long_horizon_s0.txt').read_text();p0=(P/'configs/actual_prompts/without_long_horizon_s0.txt').read_text()
import difflib
(P/'configs/actual_prompt_difference.patch').write_text(''.join(difflib.unified_diff(p1.splitlines(True),p0.splitlines(True),fromfile='with_long_horizon',tofile='without_long_horizon')))
jwrite(P/'configs/prompt_and_tool_checks.json',prompt_comparison)
# Diff recorded dependency versions across all six real runs; no new environment's pip freeze substituted.
first=deps[0][1];dependency_differences={label:{k:{'reference':first.get(k),'recorded':dp.get(k)} for k in set(first)|set(dp) if first.get(k)!=dp.get(k)} for label,dp in deps};jwrite(P/'environment/recorded_dependency_differences.json',dependency_differences);jwrite(P/'environment/recorded_packages.json',first)
(P/'environment/recorded_packages.txt').write_text(''.join(f'{k}=={v}\n' for k,v in sorted(first.items())))
# A recorded package/version list is not automatically a working environment installer.
jwrite(P/'environment/provenance.json',{'python_interpreters':[jread(Path(x['original_trial'])/'run/run.json').get('interpreter') for x in rows],'recorded_client_versions':sorted(model_versions),'upstream_commits':sorted(backend_commits),'versions':{k:first.get(k) for k in ['omnigibson','isaacsim','torch','bddl','mcp','joylo']},'status':'Package records preserved, not tested as a fresh conda/pip installation. Driver/OS and original full inherited environment not fully captured. GPU identity alone is not driver pinning.'})
# Extract environment read sites from the full frozen runtime, not only the top-level launcher.
env_reads=[]
for f in (Path(versions['with_long_horizon']['source'])/'src').rglob('*.py'):
 for i,line in enumerate(f.read_text().splitlines(),1):
  if 'os.environ' in line or 'os.getenv' in line:env_reads.append({'file':str(f.relative_to(Path(versions['with_long_horizon']['source']))),'line':i,'code':line.strip()})
jwrite(P/'configs/environment_read_sites.json',env_reads)
known={'forced_by_runner':{'MAS_DEMO_MOTION':'0','MAS_HIGH_LEVEL_HARNESS':'1','MAS_SEMANTIC_MAGIC':'0','MAS_CUSTOM_NATIVE_BDDL':'0','MAS_CPU_DYNAMICS':'0 at startup; upstream env_base switches physics after water init','OG_SWITCH_TO_CPU_AFTER_WATER':'1','OG_DISABLE_CONTACT_CACHE':'1 during initialization, removed and cache reinitialized after CPU switch','OG_KINEMATIC_NAV_TELEPORT':'0','MAS_NAV_RELAXED':'0','OG_SKIP_PHYSICS_STEPS':'0','MAS_RGB_IMAGE_SIZE':'512','MAS_RGB_PITCH_DEGREES':'35','MAS_VIDEO_RENDER_STRIDE':'3','MAS_LONG_HORIZON_PLANNING_SKILL':'1 in both launchers to retain planning profile; source directory/prompt controls document availability'},'must_supply_as_paths':['OMNIGIBSON_DATA_PATH','OMNIGIBSON_APPDATA_PATH','PYTHONPATH','CODEX_HOME'],'not_fully_historically_recorded':'Runner copies os.environ. OG_DISABLE_FABRIC, OG_DIRECT_SIM_STEP, camera and rendering overrides etc may be inherited; absence cannot be inferred from the manifest alone. Do not claim historical environment is fully frozen.','budget':{'actions':120,'sim_steps':20000,'wall_deadline':None,'initialization_seconds':900,'closure_seconds':180,'unfinished_restarts':3,'rate_limit_resumes':3,'resume_backoff_seconds':120,'provider_history_restarts':2}}
jwrite(P/'configs/runtime_contract_inventory.json',known)
# Original plans and analysis (read-only) plus scripts for rerunning the same audits.
for src,dst in [(A/'plan.json','configs/original_ablation_plan.json'),(B/'plan.json','configs/original_reference_plan.json'),(A/'comparison_summary.json','results/comparison_summary.json'),(A/'comparison_through_cell_3.json','results/comparison_full.json'),(A/'final_evidence_review.json','results/final_evidence_review.json'),(A/'REPORT.md','results/REPORT_original.md')]:copy(src,P/dst)
for name in ['bugfix_journal.md','skill_iteration_20261021.md','long_horizon_benchmark_suite.md','long_horizon_task_workflow.md','behavior_native_vs_hot_drink_20261017.md']:
 src=W/'agentic_mobile_manipulation_7f388e4/docs'/name
 if src.exists():copy(src,P/'documentation_current'/name)
# Generated configured scene bytes are part of the benchmark geometry, not dispensable cache.
configured=[]
for row in rows:
 trial=Path(row['original_trial']);config=jread(trial/'run/environment_config.json');f=Path(config['scene']['scene_file']);expected=jread(trial/'run/run.json')['backend']['input_hashes']['configured_scene'];entry={'trial':row['arm']+'_s'+str(row['seed']),'source':str(f),'expected_sha256':expected,'exists_now':f.exists()}
 if f.exists():assert digest(f)==expected;copy(f,P/'configured_scene'/f.name);entry['copied']=True
 configured.append(entry)
jwrite(P/'configured_scene/manifest.json',configured)
jwrite(O/'SOURCE_LINEAGE.json',{'source_checks':source_checks,'six_actual_src_snapshots_verified':True,'prompt_and_tool_sets_verified':True,'upstream_dirty_history_unverified':True})
jwrite(O/'SIX_TRIALS.json',rows);jwrite(O/'RAW_EVIDENCE_INVENTORY.json',{'files':raw_inventory,'total_bytes':sum(x['bytes'] for x in raw_inventory),'policy':'Archive all six; no failure deletion. This staging copies lightweight metadata only. Raw model events embed image bytes and require privacy review before public upload. Videos/frames stored externally with immutable URLs and SHA256; current private-network URLs are not a public release.'})
components=[
 {'id':'C01','name':'Benchmark runtime, executors and all local imports','status':'staged_verified','path':'package_staging/runtime','why':'The whole frozen src tree and scripts, not just modified executors, plus meaningful regression tests and package metadata.'},
 {'id':'C02','name':'Both skill conditions and actual prompts/tool catalogs','status':'staged_verified','path':'package_staging/runtime + configs/actual_prompts','why':'Other four skill files byte-identical; long-horizon main/reference removal and required-read paragraph are the intended intervention. Planning/memory tools and public decisions identical.'},
 {'id':'C03','name':'Task BDDL, instruction, scene overlay and generated geometry','status':'staged_verified','path':'package_staging/task + dataset_overlay + configured_scene','why':'Preserve partial/full templates, instance states, task catalog, actual repaired scene bytes, placement/evaluator protocol.'},
 {'id':'C04','name':'BEHAVIOR/OmniGibson source base + local patches + untracked imports','status':'current_snapshot_staged_historical_unverified','path':'package_staging/upstream','why':'17 tracked changes and5 untracked files captured; base+patch reproduction tested. Historical exact patch bytes were not recorded.'},
 {'id':'C05','name':'Large model/robot/texture/material/system/scene metadata assets','status':'external_assets_inventory_only','path':'package_staging/asset_inventory','why':'Shared symlinks are not portable archives. Version roots and candidate model directories inventoried; six small scene map PNGs saved. Transitive asset closure not certified; do not ship license key.'},
 {'id':'C06','name':'Installed environment + model controller + renderer','status':'recorded_versions_staged_fresh_install_unverified','path':'package_staging/environment','why':'298 recorded packages, codex CLI0.157.1, model service and versions; package list alone does not pin OS/driver/Kit plugins, wheel URLs or reconstruct editable dependencies.'},
 {'id':'C07','name':'Complete effective launch environment and portable entry point','status':'inventory_staged_relocation_and_env_gaps','path':'package_staging/configs','why':'Original plans use local absolute paths; os.environ inheritance was not completely logged. Need explicit clean environment wrapper, relocatable paths and fresh-host smoke test before runnable release.'},
 {'id':'C08','name':'Independent evaluator, analyzer and audit repair','status':'staged_verified_original_scores_preserved','path':'package_staging/runtime/*/scripts + results','why':'Use ablation analyzer for rejected malformed act; preserve old failure and protocol deviation audit flags. Tests for no commit on rejection and fail-closed successful missing primitive included via preflight source.'},
 {'id':'C09','name':'All six raw successful/failed trial artifacts and media','status':'lightweight_metadata_staged_raw_archive_inventory_only','path':'RAW_EVIDENCE_INVENTORY.json','why':'Both groups3seeds; seed0 supervisor finish and seed11 extra client resource calls preserved. Large raw logs/images/videos not duplicated into code package; need publication/redaction/archive SHA256.'},
 {'id':'C10','name':'Benchmark change journal + source lineage + known limitations','status':'staged_current_documentation','path':'package_staging/documentation_current + SOURCE_LINEAGE.json','why':'Journals include newer unrelated grocery changes; only source manifest defines what these six runs used. Do not apply latest code to historical runs.'}]
jwrite(O/'INVENTORY.json',{'scope':'Hot-drink long-horizon-skill incremental contribution: historical full-skill seeds0/11/22 versus prospective four-common-skill seeds0/11/22. Candidate skill optimization rounds and all-skills-off trial excluded from primary6.','date':'2026-10-25 Asia/Shanghai','components':components,'publication_ready':False,'new_simulator_experiments_run':False,'uploaded':False})
# Package checksum manifest catches accidental omission/mutation of the staged deliverables.
files={str(f.relative_to(P)):{'sha256':digest(f),'bytes':f.stat().st_size} for f in P.rglob('*') if f.is_file()};jwrite(O/'STAGING_MANIFEST.json',{'files':files,'total_files':len(files),'total_bytes':sum(x['bytes'] for x in files.values())})
print(json.dumps({'staged_files':len(files),'staged_bytes':sum(x['bytes'] for x in files.values()),'source_files_per_arm':[(x['arm'],x['files']) for x in source_checks],'upstream_tracked_changes':len(changed),'upstream_untracked_files':len(untracked),'raw_evidence_files':len(raw_inventory),'raw_evidence_bytes':sum(x['bytes'] for x in raw_inventory),'asset_model_candidates':len(asset_models),'configured_scene_exists':[x['exists_now'] for x in configured],'dependency_differences':{k:len(v) for k,v in dependency_differences.items()}},ensure_ascii=False))
