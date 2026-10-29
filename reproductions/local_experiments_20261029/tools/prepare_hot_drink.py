"""Prepare a fresh paired workspace; does not start GPU simulation or contact a model."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from archive import ROOT, digest, load_json, restore

def prepare(root, output, upstream, overlay, gpu, gpu_uuid, codex_home, provider_profile, provider_host, lock_dir, disable_tf32=False):
    root=root.resolve();output=output.absolute();upstream=upstream.resolve();overlay=overlay.resolve()
    if output.exists() or output.is_symlink():raise FileExistsError(output)
    original=root/'experiments/hot_drink_pair_20261026';plan=load_json(original/'plan.json')
    for name,expected in load_json(original/'upstream_manifest.json')['files'].items():
        if digest((upstream/name).read_bytes())!=expected:raise ValueError('Upstream mismatch: '+name)
    assets={}
    for name,expected in load_json(original/'asset_input_hashes.json').items():
        relative=Path(name).relative_to(Path(plan['dataset_overlay']));new=overlay/relative
        if digest(new.read_bytes())!=expected:raise ValueError('Scene input mismatch: '+str(relative))
        assets[str(new)]=expected
    # This validates the archived subset, not all external asset bytes or a clean installation.
    output.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.hot-drink-prepare-',dir=output.parent))
    try:
        sid=load_json(root/'selected_runtimes.json')['hot_drink_pair_20261026']
        restore(root,sid,stage/'source')
        (stage/'upstream').symlink_to(upstream,target_is_directory=True)
        plan.update(source=str(output/'source'),bddl=str(output/'source/tasks/prepare_and_serve_hot_drink/high_level_visual.bddl'),
            dataset_overlay=str(overlay),omnigibson_source=str(upstream/'OmniGibson'),gpu=gpu,gpu_uuid=gpu_uuid,
            gpu_selection=f'GPU{gpu} only; recorded UUID; shared lock; minimum free memory unchanged',
            codex_home=str(codex_home.resolve()),provider_profile=provider_profile,provider_host=provider_host,lock_directory=str(lock_dir.resolve()))
        plan['shared_environment']['MAS_TASK_DATA_PATH']=str(overlay)
        if disable_tf32:plan['shared_environment']['NVIDIA_TF32_OVERRIDE']='0'
        plan['reproduction_source_protocol']=plan['protocol_version']
        for name in ['source_manifest.json','upstream_manifest.json']:
            shutil.copy2(original/name,stage/name)
        (stage/'asset_input_hashes.json').write_text(json.dumps(assets,indent=2)+'\n')
        script=(original/'supervisor.py').read_text()
        old="site = ROOT.parent.parent/'review_sites/atomic_actions_20261008'"
        assert old in script;script=script.replace(old,"site = ROOT/'review_site'")
        old='(ROOT.parent/f"sunyujie_simulator_gpu{item[\'gpu\']}.lock")'
        assert old in script;script=script.replace(old,'(Path(PLAN[\'lock_directory\'])/f"sunyujie_simulator_gpu{item[\'gpu\']}.lock")')
        script=script.replace("    verify()\n    gpu, lock = available_gpu()","    verify()\n    Path(PLAN['lock_directory']).mkdir(parents=True, exist_ok=True)\n    gpu, lock = available_gpu()")
        (stage/'supervisor.py').write_text(script)
        (stage/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
        migration={'original_plan_sha256':digest((original/'plan.json').read_bytes()),'original_supervisor_sha256':digest((original/'supervisor.py').read_bytes()),'runtime_snapshot':sid,'runtime_code_changed':False,'changes':['absolute installation/output paths','GPU index and physical UUID','model provider profile and credential-home reference','shared simulator lock directory','publication confined to this fresh output workspace']+(['NVIDIA_TF32_OVERRIDE=0 for recorded Blackwell compatibility'] if disable_tf32 else []),'validation':'source/upstream/two scene template hashes checked; not a GPU run or clean environment installation','model':plan['model'],'reasoning_effort':plan['reasoning_effort']}
        (stage/'migration.json').write_text(json.dumps(migration,indent=2)+'\n')
        (stage/'prepared_manifest.json').write_text(json.dumps({n:digest((stage/n).read_bytes()) for n in ['supervisor.py','plan.json','migration.json']},indent=2)+'\n')
        (stage/'README.md').write_text('Prepared reproduction workspace. No model or simulator has started.\nRun the matching behavior Python interpreter on supervisor.py. Output directories must remain fresh.\nBoth arms retain the archived 120-action/20000-step high-level protocol; review_site stays within this workspace.\nComplete dependency and base asset validation separately using the archived environment evidence.\n')
        if output.exists() or output.is_symlink():raise FileExistsError(output)
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise
    return {'prepared':True,'output':str(output),'gpu_simulation_started':False,'model_contacted':False,'runtime_code_changed':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive-root',type=Path,default=ROOT);p.add_argument('--output',type=Path,required=True);p.add_argument('--upstream',type=Path,required=True);p.add_argument('--dataset-overlay',type=Path,required=True);p.add_argument('--gpu',type=int,required=True);p.add_argument('--gpu-uuid');p.add_argument('--codex-home',type=Path,required=True);p.add_argument('--provider-profile',required=True);p.add_argument('--provider-host',required=True);p.add_argument('--lock-directory',type=Path,required=True);p.add_argument('--disable-tf32',action='store_true');a=p.parse_args()
    uuid=a.gpu_uuid or subprocess.check_output(['nvidia-smi',f'--id={a.gpu}','--query-gpu=uuid','--format=csv,noheader'],text=True).strip()
    print(json.dumps(prepare(a.archive_root,a.output,a.upstream,a.dataset_overlay,a.gpu,uuid,a.codex_home,a.provider_profile,a.provider_host,a.lock_directory,a.disable_tf32),indent=2))
if __name__=='__main__':main()
