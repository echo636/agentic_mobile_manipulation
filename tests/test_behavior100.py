import importlib.util
from pathlib import Path
import unittest
import json
import tempfile

spec=importlib.util.spec_from_file_location('batch_runner',Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)

class BatchEvidenceTests(unittest.TestCase):
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

    def test_missing_video_does_not_get_a_fabricated_link(self):
        row={'index':0,'status':'failed','name':'Task <script>bad()</script>',
             'task':'test','instruction':'<unsafe>','instruction_source':'project_translation_of_static_bddl_goal'}
        text=batch.render_dashboard({'summary':batch.summarize([row]),'tasks':[row],'updated_at':'test'})
        self.assertIn('未取得最终评分',text)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('href="None"',text)
        self.assertNotIn('href="episode.mp4"',text)
        self.assertEqual(batch.summarize([row])['execution_status'],'completed')

if __name__=='__main__': unittest.main()
