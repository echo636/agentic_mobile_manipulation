"""Bounded, read-only collection of experiment code and selected provenance.

Never descends into assets, environments, media, credential homes, upstream trees,
or the publication checkout. Historical omissions remain explicit in the catalog.
"""
from __future__ import annotations
import argparse
from collections import Counter, deque
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from archive import digest, object_file

TEXT_SUFFIX = {'.py','.sh','.md','.txt','.toml','.yaml','.yml','.json','.bddl','.patch','.cpp','.cc','.c','.h','.hpp','.cmake','.js','.css','.html','.xml','.rst','.gdb'}
SKIP_NAMES = {'.git','.svn','.pytest_cache','__pycache__','node_modules','assets','datasets','dataset_overlay','bddl_overlay','frames','executor_frames','review_stills','logs','media','videos','models','checkpoints','package_staging','replicated_trials','remote_evidence','third_party_licenses','scratch','diagnostic_tools','torchinductor_sunyujie','bddl'}
SKIP_PREFIX = ('appdata','tmp','cache','upstream','environment','conda','venv','.venv')
SENSITIVE_NAME = re.compile(r'(^|[_.-])(auth|credentials?|secrets?|tokens?|password|api[_-]?key)([_.-]|$)', re.I)
SECRET_VALUE = re.compile(rb'(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:api[_-]?key|password|access_token|refresh_token)\s*[=:]\s*[\"\x27][^\"\x27\s]{18,}[\"\x27])',re.I)
STATUS_FILES = {'summary.json','run.json','plan.json','config.json','job.json','spec.json','FINAL_STATUS.json','CURRENT_STATE.json','PROGRESS.json','status.json','execution_status.json','active_scope.json','validation.json','controller.json','environment_config.json','render_settings.json','contact_cache_mode.json','mcp_preflight.json'}
SAFE_FIELDS = {'status','task','tasks','task_name','task_variant','instance','seed','model','reasoning_effort','model_reasoning_effort','source','frozen_source','runtime','execution','bddl','bddl_source','bddl_sha256','scene_version','dataset_overlay','upstream','omnigibson_source','interpreter','host','gpu','gpu_uuid','max_actions','max_sim_steps','wall_timeout_seconds','task_success','formal_finish_observed','official_task_success','native_final_evaluation','goal_satisfaction_fraction','model_used','benchmark_attempt','updated_at_unix','active_model_launch','frozen_instance','scoring','budget','termination_contract','public_decision_contract','agent_profile','skill_catalog_enabled','high_level_harness','protocol_version','validation_level','controller_exit_code','video_status','latest_model','latest_regression','not_passed_yet','source_manifest','instance_spec','client_version','observation_mode','tool_names','tool_schemas','public_decision_contract','unfinished_recovery'}
SECRET_KEYS = {'authorization','api_key','apikey','password','access_token','refresh_token','id_token','client_secret','private_key','auth','credentials'}

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')

def cache_root(name):
    return name.startswith('.') or bool(re.search(r'(^|_)(appdata|tmp|cache)(_|$)',name))

def family(name):
    if cache_root(name):return 'runtime_cache_excluded'
    if name.startswith(('wild_party','party_')): return 'native_wild_party'
    if name.startswith(('table_dinner','table_buffet','remaining_four')):return 'native_table_and_buffet'
    if 'hot_drink' in name:return 'hot_drink'
    if name.startswith(('groc','grocer')) or 'groceries' in name:return 'groceries'
    if name.startswith(('clear_dinner','dinner_')):return 'clear_dinner'
    if name.startswith('skill_'):return 'skill_and_protocol_comparisons'
    if any(x in name for x in ['codex_home','opencode_home','kimi_home','auth','credentials']):return 'credential_or_client_state_excluded'
    if name.startswith(('model_', 'atomic_', 'demo_', 'no_demo_', 'behavior100','astra_', 'original_', 'official_')):return 'model_batches_and_demos'
    return 'executor_camera_transport_and_other'

def prune(name):
    return name in SKIP_NAMES or name.startswith(SKIP_PREFIX) or name.startswith('.') or bool(SENSITIVE_NAME.search(name)) or any(x in name for x in ['codex_home','opencode_home','kimi_home'])

def clean(value):
    if isinstance(value, dict):return {k:('[REDACTED]' if k.lower() in SECRET_KEYS else clean(v)) for k,v in value.items()}
    if isinstance(value, list):return [clean(v) for v in value]
    if isinstance(value, str) and SECRET_VALUE.search(value.encode()):return '[REDACTED sensitive literal]'
    return value

class Collector:
    def __init__(self, root, output, max_depth):
        self.root=root;self.out=output;self.max_depth=max_depth
        self.snapshots=[];self.experiments=[];self.records=[];self.environments={};self.omissions=[];self.objects=set();self.visited=0
    def store(self, data):
        sha=digest(data);dest=object_file(self.out,sha)
        if not dest.exists():
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(gzip.compress(data,mtime=0))
        self.objects.add(sha);return sha
    def snapshot(self, path, experiment, kind, files=None):
        mapping={};executable=[];omitted=[];before=time.time()
        if files is None:
            files=[]
            for parent,dirs,names in os.walk(path,followlinks=False):
                dirs[:]=[d for d in dirs if not prune(d) and not (Path(parent)/d).is_symlink()]
                for n in names:
                    p=Path(parent)/n
                    if p.suffix.lower() in TEXT_SUFFIX or n in {'LICENSE','NOTICE','CMakeLists.txt','.gitignore'}:files.append(p)
        for p in sorted(files):
            rel=str(p.relative_to(path))
            if p.is_symlink():
                omitted.append({'path':rel,'reason':'symlink_not_dereferenced','target':os.readlink(p)});continue
            if SENSITIVE_NAME.search(p.name) and p.suffix not in {'.py','.sh'}:omitted.append({'path':rel,'reason':'credential_name'});continue
            try:
                st=p.stat()
                if st.st_size>16*1024*1024:omitted.append({'path':rel,'reason':'over_16MiB_not_code_snapshot'});continue
                data=p.read_bytes()
            except OSError as e:omitted.append({'path':rel,'reason':type(e).__name__});continue
            if SECRET_VALUE.search(data):
                omitted.append({'path':rel,'reason':'sensitive_literal','original_sha256':digest(data)});continue
            if b'\0' in data:omitted.append({'path':rel,'reason':'binary_content'});continue
            mapping[rel]=self.store(data)
            if st.st_mode & stat.S_IXUSR:executable.append(rel)
        if not mapping:return None
        sid=digest(json.dumps(mapping,sort_keys=True,separators=(',',':')).encode())
        dest=self.out/'snapshots'/(sid+'.json')
        if not dest.exists():dump(dest,{'files':mapping,'executable_files':executable})
        record={'experiment':experiment,'path':str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path),'kind':kind,'snapshot_id':sid,'file_count':len(mapping),'omissions':omitted,'captured_at_unix':before}
        # A capture is not retroactively a historical freeze. Compare a recorded manifest when one exists.
        for candidate in [path.parent/(path.name+'_manifest.json'),path.parent/'source_manifest.json']:
            if candidate.is_file():
                try:
                    raw=json.loads(candidate.read_text());original=raw.get('files',raw) if isinstance(raw,dict) else {}
                    common={n:h for n,h in original.items() if isinstance(h,str) and re.fullmatch('[0-9a-f]{64}',h)}
                    relevant={n:h for n,h in common.items() if n in mapping}
                    if relevant:
                        mismatches=[n for n,h in relevant.items() if mapping[n]!=h]
                        record['recorded_manifest_comparison']={'path':str(candidate.relative_to(self.root)),'compared':len(relevant),'manifest_entries':len(common),'mismatches':mismatches,'all_manifest_entries_present':len(relevant)==len(common),'association':('named_snapshot_manifest' if candidate.name==path.name+'_manifest.json' else 'parent_reference_only_not_proof_of_snapshot_identity')}
                        break
                except (OSError,ValueError,AttributeError):pass
        self.snapshots.append(record);return record
    def metadata(self,path,experiment):
        try:
            if path.stat().st_size>4*1024*1024:return
            raw=path.read_bytes();data=json.loads(raw)
        except (OSError,ValueError):return
        if not isinstance(data,dict):return
        fields=clean({k:v for k,v in data.items() if k in SAFE_FIELDS})
        if isinstance(data.get('config'),dict):fields['config']=clean({k:v for k,v in data['config'].items() if k in SAFE_FIELDS})
        if isinstance(data.get('backend'),dict):
            fields['backend']=clean({k:v for k,v in data['backend'].items() if k in {'input_hashes','gpu_uuid','executor','task','instance','seed','observation_mode','placement','dynamics_mode','visual_navigation_contract','high_level_environment_open_close_protocol'}})
        record={'experiment':experiment,'path':str(path.relative_to(self.root)),'original_sha256':digest(raw),'fields':fields,'capture_note':'Selected recorded fields; neither missing terminal evaluation nor failed startup is assigned a fabricated task score.'}
        if path.name in {'environment_config.json','render_settings.json','contact_cache_mode.json','mcp_preflight.json'}:
            record['captured_object_sha256']=self.store(json.dumps(clean(data),sort_keys=True,indent=2).encode())
        self.records.append(record)
    def walk_experiment(self,p):
        entry={'name':p.name,'family':family(p.name),'local_path':str(p),'code_snapshots':[],'metadata_records':0,'external_dependencies':[]}
        if entry['family'] in {'credential_or_client_state_excluded','runtime_cache_excluded'} or (p/'.git').exists():
            entry['scope']=('client-state excluded' if entry['family'].startswith('credential') else 'cache directory indexed only' if entry['family']=='runtime_cache_excluded' else 'Git worktree indexed only; existing branches retain its source')
            self.experiments.append(entry);return
        q=deque([(p,0)]);start=len(self.records);source_start=len(self.snapshots)
        while q:
            folder,depth=q.popleft();self.visited+=1
            try:children=list(folder.iterdir())
            except OSError as e:self.omissions.append({'path':str(folder),'reason':type(e).__name__});continue
            names={c.name:c for c in children}
            if (folder/'src/manipulation_agent').is_dir() or (folder/'manipulation_agent').is_dir():
                self.snapshot(folder,p.name,'runtime_complete_candidate' if (folder/'src').exists() else 'runtime_module_snapshot_partial')
                continue
            if folder.name=='skill_snapshot':self.snapshot(folder,p.name,'episode_skill_snapshot');continue
            scripts=[c for c in children if c.is_file() and not c.is_symlink() and c.suffix in {'.py','.sh','.patch','.bddl'}]
            # Root and per-attempt orchestration are needed in addition to src snapshots.
            if scripts:self.snapshot(folder,p.name,'orchestration_files',files=scripts)
            docs=[c for c in children if c.is_file() and not c.is_symlink() and (c.name in {'README.md','REPORT.md','REPRODUCTION.md','REPRODUCTION_REPORT.md','REPAIR_HISTORY.md','REPRODUCIBILITY.md','instruction.txt'} or c.name in {'plan.json','job.json','spec.json','active_scope.json'})]
            if docs:self.snapshot(folder,p.name,'documentation_and_configuration',files=docs)
            for c in children:
                if c.is_symlink():
                    if c.name.startswith(('upstream','dataset')):entry['external_dependencies'].append({'path':str(c.relative_to(self.root)),'target':os.readlink(c)})
                    continue
                if c.is_file():
                    if c.name in STATUS_FILES or c.name.endswith('_launch.json'):self.metadata(c,p.name)
                    elif c.name=='dependency_versions.json':
                        try:
                            raw=c.read_bytes();d=json.loads(raw)
                            if isinstance(d,dict):
                                sha=self.store(json.dumps(clean(d),sort_keys=True,indent=2).encode());self.environments.setdefault(sha,[]).append(str(c.relative_to(self.root)))
                        except (OSError,ValueError):pass
                    continue
                if not c.is_dir():continue
                if prune(c.name):continue
                if depth<self.max_depth:q.append((c,depth+1))
                else:self.omissions.append({'path':str(c.relative_to(self.root)),'reason':'bounded_metadata_walk_depth_limit'})
        entry['code_snapshots']=[s['snapshot_id'] for s in self.snapshots[source_start:]];entry['metadata_records']=len(self.records)-start
        entry['scope']='bounded code/metadata capture; media, raw sessions, environments and base assets remain external'
        self.experiments.append(entry)
    def finish(self):
        catalog={'schema_version':1,'captured_at':datetime.now(timezone.utc).isoformat(),'local_root':str(self.root),'metadata_walk_max_depth':self.max_depth,'scope':'All direct experiment directories inventoried; source candidates and selected metadata discovered by bounded traversal. Not a declaration that every experiment is reproducible or autonomous.','experiments':self.experiments,'snapshots':self.snapshots,'environment_version_records':[{'object_sha256':h,'recorded_paths':ps} for h,ps in self.environments.items()],'traversal_omissions':self.omissions}
        dump(self.out/'catalog.json',catalog);dump(self.out/'recorded_metadata.json',self.records)
        report={'experiments':len(self.experiments),'families':dict(Counter(e['family'] for e in self.experiments)),'snapshot_references':len(self.snapshots),'unique_snapshot_trees':len({s['snapshot_id'] for s in self.snapshots}),'unique_file_objects':len(self.objects),'environment_version_sets':len(self.environments),'directories_examined':self.visited,'omitted_deeper_directories':len(self.omissions),'snapshot_omissions':sum(len(s['omissions']) for s in self.snapshots)}
        dump(self.out/'collection_summary.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiments',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--working-repo',type=Path,required=True);p.add_argument('--max-depth',type=int,default=3);a=p.parse_args()
    root=a.experiments.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    c=Collector(root,out,a.max_depth)
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.is_symlink() or out.is_relative_to(d):continue
        c.walk_experiment(d)
    repo=a.working_repo.resolve();owned=['src','skills','tasks','scripts','tests','docs','native','third_party','web','pyproject.toml','README.md','AGENTS.md','THIRD_PARTY_NOTICES.md','.gitignore']
    listed=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard','--',*owned],cwd=repo).split(b'\0')
    files=[repo/os.fsdecode(n) for n in listed if n and (repo/os.fsdecode(n)).is_file() and ((repo/os.fsdecode(n)).suffix in TEXT_SUFFIX or (repo/os.fsdecode(n)).name in {'LICENSE','NOTICE','CMakeLists.txt','.gitignore'})]
    c.snapshot(repo,'maintained_working_tree_20261029','current_working_tree_capture_not_historical_runtime',files=files)
    c.finish()
if __name__=='__main__':main()
