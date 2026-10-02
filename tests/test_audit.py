import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.audit import audit_episode,official_executor_protocol


class ModelEvidenceAudit(unittest.TestCase):
    def test_native_planner_protocol_requires_complete_initialized_planner(self):
        backend={'control_protocol':'rgb_official_symbolic_initialized_navigation_v2',
            'navigation_planner':{'initialized':True,'implementation':'upstream_CuRoboMotionGenerator',
                'embodiments':['DEFAULT','ARM','BASE']}}
        self.assertTrue(official_executor_protocol(backend))
        backend['navigation_planner']['embodiments'].remove('ARM')
        self.assertFalse(official_executor_protocol(backend))
        self.assertFalse(official_executor_protocol({'control_protocol':'rgb_official_symbolic_initialized_navigation_v2'}))
        self.assertFalse(official_executor_protocol({'control_protocol':'unknown'}))
        self.assertTrue(official_executor_protocol({'control_protocol':'rgb_official_symbolic_direct_v1'}))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / 'sim'; self.run_dir.mkdir()
        self.ctrl_dir = Path(self.tmp.name) / 'model'; self.ctrl_dir.mkdir()
        self.run = {'run_id':'test', 'config':{'backend':'omnigibson'}, 'source':{'commit':'abc'},
                    'task_success':True, 'evaluation':{'official_task_success':True, 'protocol':'symbolic_oracle_research'}}
        self.ctrl = {'model':'fixture', 'status':'passed', 'exit_code':0, 'source':{'commit':'abc'}}
        self.call = {'kind':'tool_call', 'name':'finish', 'arguments':{'outcome':'achieved','reason':'observed'}}
        self.item = {'type':'mcp_tool_call','server':'manipulation','tool':'finish','arguments':self.call['arguments'],
                     'result':{'content':[{'type':'text','text':json.dumps({'ok':True,'closed':True})}]}}

    def audit(self, extra=None):
        (self.run_dir/'run.json').write_text(json.dumps(self.run))
        (self.ctrl_dir/'controller.json').write_text(json.dumps(self.ctrl))
        (self.run_dir/'events.jsonl').write_text(json.dumps(self.call)+'\n')
        items = [{'type':'item.completed','item':self.item}]+(extra or [])
        (self.ctrl_dir/'model_events.jsonl').write_text('\n'.join(json.dumps(e) for e in items)+'\n')
        return audit_episode(self.run_dir,self.ctrl_dir)

    def test_matching_evidence_passes(self):
        self.assertEqual(self.audit()['status'],'passed')

    def test_successful_simulator_does_not_hide_controller_mismatch(self):
        self.item['arguments'] = {'outcome':'achieved','reason':'different trace'}
        self.assertEqual(self.audit()['evidence_alignment'],'failed')

    def test_model_claim_cannot_override_failed_task(self):
        self.run['task_success'] = False
        self.run['evaluation']['official_task_success'] = False
        result=self.audit()
        self.assertEqual(result['evidence_alignment'],'passed')
        self.assertEqual(result['status'],'failed')

    def test_non_mcp_execution_invalidates_isolated_policy_claim(self):
        result=self.audit([{'type':'item.completed','item':{'type':'command_execution','id':'bad'}}])
        self.assertFalse(result['checks']['only_allowed_tools'])
        self.assertEqual(result['status'],'failed')
