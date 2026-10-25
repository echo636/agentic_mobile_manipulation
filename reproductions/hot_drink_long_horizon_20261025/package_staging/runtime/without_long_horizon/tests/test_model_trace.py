import json
from pathlib import Path
import tempfile
import unittest
from manipulation_agent.model_trace import export_summaries
from manipulation_agent.review_video import frame_indices

class ModelTraceTests(unittest.TestCase):
    def test_exact_thread_only_readable_summary_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sessions=root/'sessions';day=sessions/'2026/10/01';day.mkdir(parents=True)
            tid='12345678-1234-1234-1234-123456789abc'
            original='Original summary.\nPreserve English and 中文.'
            entries=[{'type':'response_item','timestamp':'2026-10-01T00:00:01Z','payload':
                {'type':'reasoning','summary':[{'type':'summary_text','text':original}],
                 'encrypted_content':'OPAQUE_MUST_NOT_EXPORT','content':[{'text':'NOT_A_SUMMARY'}]}},
                {'type':'response_item','payload':{'type':'message','content':'SYSTEM_MUST_NOT_EXPORT'}}]
            (day/('rollout-'+tid+'.jsonl')).write_text('\n'.join(json.dumps(e) for e in entries))
            (day/'rollout-unrelated.jsonl').write_text('UNRELATED_MUST_NOT_READ')
            result=export_summaries(root,[{'type':'thread.started','thread_id':tid}],
                '2026-10-01T00:00:00Z','2026-10-01T00:01:00Z',sessions)
            saved=(root/'model_reasoning_summaries.jsonl').read_text()
            self.assertEqual(json.loads(saved)['text'],original)
            self.assertEqual(result['summary_count'],1)
            self.assertEqual(result['encrypted_items'],1)
            for forbidden in ('OPAQUE','NOT_A_SUMMARY','SYSTEM','UNRELATED'):self.assertNotIn(forbidden,saved)

    def test_missing_session_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=export_summaries(Path(tmp),[],'2026-10-01T00:00:00Z','2026-10-01T00:01:00Z',Path(tmp))
            self.assertEqual(result['status'],'unavailable')
            self.assertFalse((Path(tmp)/'model_reasoning_summaries.jsonl').exists())

    def test_review_subsampling_keeps_motion_endpoints_and_order(self):
        self.assertEqual(frame_indices(3,9),[3,5,7,8])
        self.assertEqual(frame_indices(3,9,motor=False),[8])
        self.assertEqual(frame_indices(3,3),[])
