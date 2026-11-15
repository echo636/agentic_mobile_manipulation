"""Bind authored-scene admission to actual fresh reload and complete reference evidence."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve();assert not out.exists()
    read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    source_path=root/'native_separate_return_author_r1/prepared_input/task.json';spec=read(source_path)
    reference=root/'native_separate_returns_r4';record=read(reference/'run_record.json');fresh=read(reference/'fresh_restore.json')
    assert record['status']==record['cleanup_status']=='passed' and not record['model_used']
    assert record['spec_sha256']==fresh['source_spec_sha256']==sha(source_path)
    assert fresh['status']=='passed' and len(fresh['initial_conditions'])==10 and all(fresh['initial_conditions'])
    assert fresh['initial_evaluation']['task_success'] is False
    evaluation=record['evaluation'];assert evaluation['full_task_success'] and evaluation['native_evaluation']['task_success']
    assert all(evaluation['predicates'].values()) and evaluation['predicates']['hand_empty']
    assert read(reference/'process_exit.json')['exit_code']==0
    for key in ['definition','frozen_instance','sampling_record']:
        assert sha(source_path.parent/spec[key]['path'])==spec[key]['sha256']
    out.mkdir()
    updated=copy.deepcopy(spec)
    for key,name in [('definition','problem0.bddl'),('frozen_instance','initial_scene.json')]:
        shutil.copy2(source_path.parent/spec[key]['path'],out/name)
        updated[key]=dict(path=name,sha256=sha(out/name))
    evidence_files=[source_path,source_path.parent/spec['sampling_record']['path'],
                    reference/'run_record.json',reference/'fresh_restore.json',reference/'process_exit.json',
                    root/'native_source_v17/source_freeze.json']
    evidence=dict(status='passed',model_used=False,task=spec['task'],scene=spec['scene'],seed=spec['seed'],
        bddl_sha256=spec['bddl_sha256'],initial_conditions=fresh['initial_conditions'],initial_goal_satisfied=False,
        saved_files={'initial_scene.json':updated['frozen_instance']['sha256']},
        creation_method='native_scene_fixture_derivation',native_online_sampling_used=False,
        validation_level='authored_fixture_export_fresh_reload_and_complete_scripted_reference',
        full_reference_passed=True,reference_model_used=False,autonomous_rgb_success_not_claimed=True,
        source_spec_sha256=sha(source_path),reference_spec_sha256=record['spec_sha256'],
        evidence_files={str(p):sha(p) for p in evidence_files},created_at=time.time())
    (out/'admission_record.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    updated.update(sampling_record=dict(path='admission_record.json',sha256=sha(out/'admission_record.json')),
        preparation_status='passed',additional_terminal_conditions=['hand_empty'],
        evaluation_scope='Native BDDL terminal goals and explicitly declared public empty-hand condition; additional-terminal-conditions protocol v1.',
        admission_scope='Authored fixture validated by fresh native reload and complete private-target reference; not online sampling, all-method feasibility, or autonomous RGB success.')
    (out/'task.json').write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n')
    assert updated['frozen_instance']['sha256']==spec['frozen_instance']['sha256']
    assert updated['definition']['sha256']==spec['definition']['sha256']
    assert updated['environment_config']==spec['environment_config'] and updated['public_instruction']==spec['public_instruction']
    (out/'preparation_manifest.json').write_text(json.dumps(dict(status='passed',preparer_sha256=sha(Path(__file__)),
        source_spec_sha256=sha(source_path),spec_sha256=sha(out/'task.json'),physical_scene_unchanged=True,
        bddl_unchanged=True,public_instruction_unchanged=True,environment_unchanged=True,
        scoring_extension='Previously public and independently scored empty-hand condition now declared for the normal CatalogRGBBackend path.'),indent=2)+'\n')
    print(json.dumps(dict(status='prepared',spec=str(out/'task.json'),spec_sha256=sha(out/'task.json'))))


if __name__=='__main__':main()
