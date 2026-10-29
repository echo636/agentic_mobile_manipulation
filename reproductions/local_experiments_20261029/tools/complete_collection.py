from pathlib import Path
from datetime import datetime,timezone
import sys,json,hashlib,gzip,shutil,subprocess,re
import argparse
parser=argparse.ArgumentParser(description="Complete a freshly collected archive using local recorded inputs; not an experiment launcher.")
parser.add_argument('--archive-root',type=Path,required=True);parser.add_argument('--experiments',type=Path,required=True)
parser.add_argument('--working-repo',type=Path,required=True);parser.add_argument('--upstream-checkout',type=Path,required=True)
args=parser.parse_args();R=args.archive_root.resolve();E=args.experiments.resolve()
sys.path.insert(0,str(R/'tools'))
from archive import digest,object_file,restore
from collect_local import Collector,clean,SECRET_VALUE,dump
from recover_upstream import GitObjects
c=json.loads((R/'catalog.json').read_text());records=json.loads((R/'recorded_metadata.json').read_text());helper=Collector(E,R,3)
# Compare exact snapshots to actual bases, not an unrelated parent's generic manifest.
for s in c['snapshots']:
 q=s.get('recorded_manifest_comparison')
 if q and q.get('association')=='parent_reference_only_not_proof_of_snapshot_identity':
  q['interpretation']='Reference differences only; the parent manifest was not proven to describe this snapshot.'
# Readable entry points for current development, successful hot drink, and latest captured native experiment.
paths={'local_development':str(args.working_repo.resolve()),'hot_drink_pair_20261026':'hot_drink_lh_repaired_pair_20261026/source','wild_party_v68':'wild_party_runtime_20261026/execution_source_v68','groceries_groc026':'groc026_boundary_feedback_regression_20261025/execution','table_buffet_20261027':'table_buffet_runtime_20261027/source','remaining_four_20261027':'remaining_four_20261027/source'}
selected={}
for label,path in paths.items():
 s=next(s for s in c['snapshots'] if s['path']==path);selected[label]=s['snapshot_id']
 out=R/'runtimes'/label
 if not out.exists():restore(R,s['snapshot_id'],out)
dump(R/'selected_runtimes.json',selected)
# Preserve full environment/version evidence, not the environment binary or credentials.
env=R/'environment';env.mkdir(exist_ok=True);remote=E/'hot_drink_remote_deployment_20261026'
for name in ['conda_explicit.txt','pip_freeze.txt','client_bundle_manifest.json','environment_cache_correction.json','migration_record.json','REPRODUCTION_REPORT.md','FINAL_STATUS.json','REPRODUCTION_STATUS.remote.json','REPRODUCTION_SUMMARY.remote.md']:
 p=remote/name
 if p.exists():
  data=p.read_bytes();assert not SECRET_VALUE.search(data),name;(env/name).write_bytes(data)
for name in ['base_assets_manifest.json','dataset_overlay_manifest.json']:
 raw=(remote/name).read_bytes();data=json.loads(raw)
 assert not any(re.search(r'(^|/)(auth\.json|omnigibson\.key|credentials)',n,re.I) for n in data['files'])
 (env/(name+'.gz')).write_bytes(gzip.compress(raw,mtime=0))
version=c['environment_version_records'][0]['object_sha256'];(env/'recorded_python_distributions.json').write_bytes(gzip.decompress(object_file(R,version).read_bytes()))
# Two real frozen upstream variants; Git base + exact override bytes avoids vendoring full libraries.
repo=args.upstream_checkout.resolve();upstream=[]
for label,folder in [('hot_drink_20261026','hot_drink_lh_tools_pair_20261026'),('wild_party_20261026','wild_party_feasibility_20261026')]:
 base=E/folder;m=json.loads((base/'upstream_manifest.json').read_text());files=m.get('files',m);recorded_commit=m.get('git_commit') or m.get('commit');commit=recorded_commit or 'b1979916ec1549b10a4e65e630bc6504a9af1b00';git=GitObjects(repo);overrides={}
 try:
  for n,h in files.items():
   raw=(base/'upstream'/n).read_bytes();assert digest(raw)==h,n
   old=git.read(commit+':'+n)
   if old!=raw:overrides[n]=helper.store(raw)
 finally:git.close()
 out={'base_repository':'https://github.com/StanfordVL/BEHAVIOR-1K.git','base_commit':commit,'recorded_base_commit':recorded_commit,'base_selection_note':'Reconstruction base; all final bytes must match the recorded file manifest. A missing recorded commit is not inferred historical evidence.','recorded_source':str(base/'upstream'),'files':files,'overrides':overrides,'scope':'Exact recorded subset; external assets and installed binary dependencies are separate.'}
 dump(R/'upstream'/label/'recovery.json',out);upstream.append({'name':label,'file_count':len(files),'override_count':len(overrides),'base_commit':commit})
 git=GitObjects(repo)
 try:
  for n in ['OmniGibson/LICENSE','bddl3/LICENSE']:
   data=git.read(commit+':'+n)
   if data is not None:
    p=R/'upstream'/label/(n.replace('/','.')+'.txt');p.write_bytes(data)
 finally:git.close()
c['upstream_recoveries']=upstream
# Original hot-drink scripts and plans remain auditable; portable preparation records replacements separately.
hot=E/'hot_drink_lh_repaired_pair_20261026';dest=R/'experiments/hot_drink_pair_20261026';dest.mkdir(parents=True,exist_ok=True)
for n in ['plan.json','supervisor.py','complete_review.py','review_failures.py','finalize_manual_review.py','source_manifest.json','upstream_manifest.json','asset_input_hashes.json','supervisor_manifest.json','REPORT.md','REPAIR_HISTORY.md','manual_reviewed_results.json','pair_configuration_audit.json','runtime_and_scene_equivalence.json','wire_identity_audit.json','without_manual_cost_review.json','with_manual_cost_review.json']:
 data=(hot/n).read_bytes();assert not SECRET_VALUE.search(data),n;(dest/n).write_bytes(data)
# Register workspace metadata for the current source capture, distinct from historical use.
workspace=args.working_repo.resolve()
state={'captured_at':datetime.now(timezone.utc).isoformat(),'path':str(workspace),'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=workspace,text=True).strip(),'branch':subprocess.check_output(['git','branch','--show-current'],cwd=workspace,text=True).strip(),'selected_snapshot':selected['local_development'],'meaning':'Current dirty working-tree capture, not the source identity of all historical experiments.'}
dump(R/'working_tree_provenance.json',state)
dump(R/'catalog.json',c)
# Remove first-pass compiler/cache artifacts that no remaining catalog references.
keep_snapshots={s['snapshot_id'] for s in c['snapshots']};keep_objects=set()
for sid in keep_snapshots:keep_objects.update(json.loads((R/'snapshots'/(sid+'.json')).read_text())['files'].values())
keep_objects.update(e['object_sha256'] for e in c['environment_version_records']);keep_objects.update(e['captured_object_sha256'] for e in records if 'captured_object_sha256' in e)
for u in upstream:keep_objects.update(json.loads((R/'upstream'/u['name']/'recovery.json').read_text())['overrides'].values())
removed={'snapshots':0,'objects':0}
for p in (R/'snapshots').glob('*.json'):
 if p.stem not in keep_snapshots:p.unlink();removed['snapshots']+=1
for p in (R/'objects').glob('*/*.gz'):
 if p.name[:-3] not in keep_objects:p.unlink();removed['objects']+=1
print(json.dumps({'selected_runtimes':selected,'upstream_recoveries':upstream,'unreferenced_generated_artifacts_removed':removed,'final_referenced_objects':len(keep_objects)},indent=2))
