import importlib.util
from pathlib import Path
import unittest
import json
import tempfile
import threading
from subprocess import CompletedProcess
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('batch_runner',Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)

class BatchEvidenceTests(unittest.TestCase):
    def test_missing_assets_block_before_gpu_wait_or_simulator_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for directory in ('logs','preflight'):(root/directory).mkdir()
            runner=batch.Batch.__new__(batch.Batch)
            runner.root=root;runner.source=Path(__file__).resolve().parents[1]
            runner._worker_local=threading.local()
            runner._config={'base_port':31000,'batch_tag':'test','data_root':'/fixture',
                            'sim_python':'/fixture/python','ssh':['ssh','fixture']}
            runner.update=lambda row,**values:row.update(values)
            runner.journal=Mock();runner.publish=Mock()
            asset={'status':'failed','tasks':[{'runtime_assets':{'missing_model_usds':['missing.usd']}}]}
            runner.ssh=Mock(return_value=CompletedProcess([],0,json.dumps(asset),''))
            row={'index':0,'run_id':'fixture_r1','task':'fixture'}
            runner.run_one(row,0)
            self.assertEqual(row['status'],'blocked')
            self.assertEqual(row['failure_stage'],'asset_preflight')
            self.assertIsNone(row.get('task_success'))
            self.assertEqual(runner.ssh.call_count,1)
            self.assertIn('assets',runner.ssh.call_args.args[0])
            self.assertEqual(json.loads((root/'preflight/fixture_r1_assets.json').read_text()),asset)

    def test_adopted_controller_outside_batch_keeps_stable_hash_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'original/runs/run1';run.mkdir(parents=True)
            ctrl=root/'other/controllers/run1';ctrl.mkdir(parents=True)
            (run/'run.json').write_text('{}');(ctrl/'model_events.jsonl').write_text('{}\n')
            hashes=batch.episode_artifact_hashes('run1',run,ctrl)
            self.assertEqual(set(hashes),{'runs/run1/run.json','controllers/run1/model_events.jsonl'})
            self.assertTrue(all(len(value)==64 for value in hashes.values()))

    def test_model_decision_prevents_infrastructure_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);record={'status':'failed','actions':0,'controller_pid':123}
            stream=path/'model_events.jsonl'
            stream.write_text(json.dumps({'type':'item.completed','item':{'type':'error'}})+'\n')
            self.assertTrue(batch.prepolicy_failure(record,path))
            for kind in ('agent_message','reasoning','mcp_tool_call'):
                stream.write_text(json.dumps({'type':'item.started','item':{'type':kind}})+'\n')
                self.assertFalse(batch.prepolicy_failure(record,path))

    def test_infrastructure_retry_retains_first_attempt_failure(self):
        row={'status':'passed','task_success':True,'previous_attempts':[{'status':'failed','task_success':False}]}
        result=batch.summarize([row])
        self.assertEqual(result['total'],1)
        self.assertEqual(result['task_successes'],1)
        self.assertEqual(result['first_attempt_task_successes'],0)
        self.assertEqual(result['extra_infrastructure_attempts'],1)

    def test_failures_and_unattempted_tasks_remain_in_denominator(self):
        rows=[{'status':'passed','task_success':True,'video_validation':'passed'},
              {'status':'failed','task_success':False}, {'status':'blocked','task_success':None},
              {'status':'running'}, {'status':'planned'}]
        result=batch.summarize(rows)
        self.assertEqual(result['total'],5)
        self.assertEqual(result['completed'],3)
        self.assertEqual(result['success_fraction_all_tasks'],.2)
        self.assertEqual(result['final_evaluations'],2)
        self.assertEqual(result['complete_videos'],1)
        self.assertEqual(result['execution_status'],'running')

    def test_executor_retest_keeps_task_denominator_and_first_result(self):
        rows=[{'status':'passed','task_success':True,'retry_kind':'executor_fix',
               'previous_attempts':[{'status':'failed','task_success':False}]},
              {'status':'planned'}]
        result=batch.summarize(rows)
        self.assertEqual(result['total'],2)
        self.assertEqual(result['task_successes'],1)
        self.assertEqual(result['first_attempt_task_successes'],0)
        self.assertEqual(result['extra_infrastructure_attempts'],0)
        self.assertEqual(result['extra_policy_attempts'],1)

    def test_missing_video_does_not_get_a_fabricated_link(self):
        row={'index':0,'status':'failed','name':'Task <script>bad()</script>',
             'task':'test','instruction':'<unsafe>','instruction_source':'project_translation_of_static_bddl_goal'}
        text=batch.render_dashboard({'summary':batch.summarize([row]),'tasks':[row],'updated_at':'test'})
        self.assertIn('未取得最终评分',text)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('href="None"',text)
        self.assertNotIn('href="episode.mp4"',text)
        self.assertEqual(batch.summarize([row])['execution_status'],'completed')

    def test_subset_reports_actual_denominator_and_separate_baseline(self):
        row={'index':0,'status':'planned','name':'Radio','task':'radio',
             'instruction':'Turn on radio','instruction_source':'official_gallery'}
        text=batch.render_dashboard({'summary':batch.summarize([row]),'tasks':[row],'updated_at':'test',
             'comparison':{'scope':'29 completed + 3 interrupted','baseline_url':'../old.html','report_url':'../fix.html'}})
        self.assertIn('1 项任务测试',text)
        self.assertIn('1 项使用官方原文，0 项按静态',text)
        self.assertIn('href="../old.html"',text)
        self.assertNotIn('共 100 个 episode',text)

if __name__=='__main__': unittest.main()
