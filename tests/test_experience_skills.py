"""CPU persistence/protocol checks; fixture outcomes are not robot experiments."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from manipulation_agent.contracts import SkillError
from manipulation_agent.experience_skills import ExperienceStore, skill_name
from manipulation_agent.skill_runtime import SkillLibrary
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class ExperienceSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=ExperienceStore(self.root/'library')

    def tearDown(self): self.tmp.cleanup()

    def source(self, name='source', *, task='turning_on_radio', protocol='oracle_waypoints',
               official=False, engineering=None):
        run=self.root/name;env=run/'env';env.mkdir(parents=True)
        (env/'run.json').write_text(json.dumps({'run_id':name,'status':'passed' if official else 'failed',
            'config':{'task':task,'backend':'cpu_fixture','instance':301,'seed':0,
                      'observation_mode':'oracle_3d_waypoints' if protocol=='oracle_waypoints' else 'rgb_only'},
            'task_success':official,'evaluation':{'official_task_success':official}}))
        (run/'engineering_validation.json').write_text(json.dumps({'engineering_success':engineering}))
        (env/'events.jsonl').write_text(json.dumps({'id':'one','kind':'tool_result','name':'grasp',
                'result':{'ok':False,'error':{'code':'contact_not_retained'}}})+'\n')
        return run

    def publish(self, run, *, kind='task', key='turning_on_radio', protocol='oracle_waypoints',
                revision=0, body='Use the current observation; this is a CPU fixture.', claim='partial_or_failure'):
        return self.store.publish(kind,key,protocol,description='Fixture experience for protocol checks',body=body,
                                  runs=[run],claim=claim,expected_revision=revision)

    def library(self, output='snapshot', *, task='turning_on_radio', protocol='oracle_waypoints'):
        return SkillLibrary(self.root/output,include={'oracle-manipulation'} if protocol=='oracle_waypoints' else {'pick-and-place'},
                            task=task,protocol=protocol,experience_root=self.store.root)

    def test_task_and_protocol_selection_blocks_unrelated_experience(self):
        self.publish(self.source('a'))
        self.publish(self.source('b',task='collecting_aluminum_cans'),key='collecting_aluminum_cans')
        self.publish(self.source('c',protocol='rgb_only'),protocol='rgb_only')
        self.publish(self.source('d'),kind='action',key='grasp')
        oracle=self.library();names={s['name'] for s in oracle.catalog()}
        self.assertIn('behavior-turning-on-radio',names);self.assertIn('oracle-grasp',names)
        self.assertNotIn('behavior-collecting-aluminum-cans',names)
        text=oracle.read('behavior-turning-on-radio','SKILL.md')['text']
        self.assertIn('Scope: oracle_waypoints',text)
        rgb=self.library('rgb',protocol='rgb_only')
        self.assertNotIn('oracle-grasp',{s['name'] for s in rgb.catalog()})
        self.assertIn('Scope: rgb_only',rgb.read('behavior-turning-on-radio','SKILL.md')['text'])
        with self.assertRaises(SkillError):oracle.read('behavior-turning-on-radio','provenance.json')
        with self.assertRaises(SkillError):oracle.read('behavior-collecting-aluminum-cans','SKILL.md')

    def test_new_revision_cannot_change_an_active_episode(self):
        run=self.source();self.publish(run,body='Observed version one.')
        frozen=self.library();old=frozen.read('behavior-turning-on-radio','SKILL.md')
        self.publish(run,revision=1,body='Revised after contradictory observations.')
        self.assertEqual(frozen.read('behavior-turning-on-radio','SKILL.md'),old)
        current=self.library('next')
        self.assertNotEqual(current.digest,frozen.digest)
        self.assertIn('contradictory',current.read('behavior-turning-on-radio','SKILL.md')['text'])
        self.assertTrue((self.store.entry('task','turning_on_radio','oracle_waypoints')/'revisions/000001/SKILL.md').exists())

    def test_success_claims_require_the_matching_independent_evidence(self):
        run=self.source(engineering=None)
        for claim in ['whole_task_success','engineering_success']:
            with self.assertRaises(ValueError): self.publish(run,claim=claim)
        engineering=self.source('engineering',engineering=True)
        self.publish(engineering,claim='engineering_success')
        with self.assertRaises(ValueError):self.publish(engineering,revision=1,claim='whole_task_success')
        official=self.source('official',official=True)
        self.publish(official,revision=1,claim='whole_task_success')

    def test_task_and_protocol_mismatch_are_rejected(self):
        run=self.source()
        with self.assertRaises(ValueError):self.publish(run,key='collecting_aluminum_cans')
        with self.assertRaises(ValueError):self.publish(run,protocol='rgb_only')

    def test_stale_and_concurrent_writes_preserve_history(self):
        run=self.source();self.publish(run)
        with self.assertRaises(ValueError):self.publish(run,revision=0)
        def update(i):
            try:return self.publish(run,revision=1,body='Replacement '+str(i))['revision']
            except ValueError:return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(update,[1,2]))
        self.assertCountEqual(results,[2,'stale'])
        self.assertEqual(self.store.current('task','turning_on_radio','oracle_waypoints')['revision'],2)

    def test_rollback_and_next_update_do_not_overwrite_versions(self):
        run=self.source();self.publish(run);self.publish(run,revision=1,body='Second version')
        self.store.activate('task','turning_on_radio','oracle_waypoints',revision=1,expected_revision=2)
        self.assertEqual(self.publish(run,revision=1,body='Third version')['revision'],3)
        saved=self.store.entry('task','turning_on_radio','oracle_waypoints')/'revisions/000002/SKILL.md'
        self.assertIn('Second version',saved.read_text())

    def test_candidates_are_idempotent_and_never_live_skills(self):
        run=self.source();candidate=self.store.archive(run)
        self.assertEqual(candidate,self.store.archive(run))
        data=json.loads(candidate.read_text());self.assertEqual(data['review_status'],'candidate')
        self.assertEqual(data['failures'][0]['code'],'contact_not_retained')
        self.assertIsNone(self.store.current('task','turning_on_radio','oracle_waypoints'))
        self.assertIsNone(self.library().recommendations()['task_skill'])

    def test_hash_validation_and_path_guards(self):
        run=self.source();self.publish(run);entry=self.store.current('task','turning_on_radio','oracle_waypoints')
        (entry['directory']/'SKILL.md').write_text('changed without a revision')
        with self.assertRaisesRegex(ValueError,'integrity'):self.library()
        for key in ['../escape','one/two','']:
            with self.assertRaises(ValueError):self.store.entry('task',key,'oracle_waypoints')
        self.assertLessEqual(len(skill_name('task','a'*120,'oracle_waypoints')),64)

    def test_retrieval_rejects_symlinked_documents(self):
        run=self.source();self.publish(run);entry=self.store.current('task','turning_on_radio','oracle_waypoints')
        doc=entry['directory']/'SKILL.md';doc.unlink();doc.symlink_to(run/'env/run.json')
        with self.assertRaisesRegex(ValueError,'symlink'):self.library()

    def test_initialize_recommends_task_and_finalization_archives_offline(self):
        source=self.source('rgb-source',protocol='rgb_only');self.publish(source,protocol='rgb_only')
        root=self.root/'episode';recorder=Recorder(root,{'task':'turning_on_radio','observation_mode':'rgb_only'})
        backend=MockRGBBackend(root);h=VisionHarness(backend,recorder,experience_root=self.store.root)
        init=h.call('initialize',{},'init')
        self.assertEqual(init['experience']['task_skill'],'behavior-turning-on-radio')
        backend.on=True;h.call('finish',{'outcome':'achieved','reason':'CPU fixture only'},'finish')
        h.finalize_recording()
        status=json.loads((root/'experience_archive.json').read_text())
        self.assertEqual(status['status'],'candidate_saved');self.assertFalse(status['automatically_promoted'])
        self.assertEqual(self.store.current('task','turning_on_radio','rgb_only')['revision'],1)
        self.assertTrue(recorder.run['task_success'])

    def test_oracle_initialize_and_mcp_read_use_the_matching_task_snapshot(self):
        from manipulation_agent.oracle_harness import OracleWaypointHarness
        from manipulation_agent.contracts import Budget
        class Backend:
            steps=0
            def observe(self):
                return {'observation_mode':'oracle_3d_waypoints','images':[],
                        'capture':{'capture_id':'cpu','sim_step':0},
                        'oracle_3d':{'frame':'robot_base','units':{},'robot':{},'objects':[],
                                     'object_scope':'cpu_fixture','omitted_objects':0}}
            def provenance(self):return {'name':'CPU fixture'}
            def control_contract(self):return {'protocol':'cpu_fixture'}
        self.publish(self.source())
        self.publish(self.source('grasp-source'),kind='action',key='grasp')
        root=self.root/'oracle-episode';recorder=Recorder(root,{'task':'turning_on_radio'})
        h=OracleWaypointHarness(Backend(),recorder,Budget(wall_seconds=None),experience_root=self.store.root)
        init=h.call('initialize',{},'init')
        self.assertEqual(init['experience']['task_skill'],'behavior-turning-on-radio')
        self.assertIn('oracle-grasp',init['experience']['action_skills'])
        result=h.call('read_skill',{'name':'behavior-turning-on-radio','resource':'SKILL.md'},'read')
        self.assertIn('Scope: oracle_waypoints',result['text'])
        self.assertEqual(result['bundle_sha256'],h.skills.digest)
        self.assertEqual(h.backend.steps,0)

    def test_archive_failure_cannot_erase_score(self):
        root=self.root/'failed_archive';recorder=Recorder(root,{'task':'turning_on_radio','observation_mode':'rgb_only'})
        backend=MockRGBBackend(root);h=VisionHarness(backend,recorder,experience_root=self.store.root)
        backend.on=True;h.call('finish',{'outcome':'achieved','reason':'fixture'},'finish')
        with patch.object(ExperienceStore,'archive',side_effect=OSError('fixture storage unavailable')):
            h.finalize_recording()
        self.assertTrue(json.loads((root/'run.json').read_text())['task_success'])
        self.assertEqual(json.loads((root/'experience_archive.json').read_text())['status'],'failed')

    def test_untried_task_is_an_index_entry_without_invented_skill(self):
        rows=self.store.inventory(['turning_on_radio','collecting_aluminum_cans'])
        self.assertEqual(len(rows),2)
        self.assertTrue(all(v is None for row in rows for v in row['protocols'].values()))
        self.assertFalse((self.store.root/'tasks').exists())

if __name__=='__main__':unittest.main()
