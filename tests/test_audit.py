import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.audit import audit_episode


class ModelEvidenceAudit(unittest.TestCase):
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

    def audit(self, extra=None, sim_extra=None):
        (self.run_dir/'run.json').write_text(json.dumps(self.run))
        (self.ctrl_dir/'controller.json').write_text(json.dumps(self.ctrl))
        (self.run_dir/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in [self.call]+(sim_extra or [])))
        items = [{'type':'item.completed','item':self.item}]+(extra or [])
        (self.ctrl_dir/'model_events.jsonl').write_text('\n'.join(json.dumps(e) for e in items)+'\n')
        return audit_episode(self.run_dir,self.ctrl_dir)

    def test_matching_evidence_passes(self):
        self.assertEqual(self.audit()['status'],'passed')

    def test_custom_score_requires_frozen_scene_and_evaluator_provenance(self):
        self.run['config']['custom_task']='/private/frozen_spec.json'
        self.run['evaluation']={
            'score_type':'custom_joint_terminal_and_public_history_v1',
            'official_submission_eligible':False,'model_notes_used_for_scoring':False,
            'input_hashes':{'custom_task_spec':'spec-hash','custom_initial_scene':'scene-hash'}}
        result=self.audit()
        self.assertEqual(result['status'],'passed')
        self.assertNotIn('official_task_success',result['checks'])
        del self.run['evaluation']['input_hashes']['custom_initial_scene']
        self.assertEqual(self.audit()['evidence_status'],'failed')

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
        self.assertEqual(result['evidence_status'],'failed')
        self.assertEqual(result['status'],'passed')

    def test_forced_client_exit_does_not_claim_trace_misalignment(self):
        self.ctrl.update(status='failed',exit_code=-15)
        result=self.audit()
        self.assertEqual(result['evidence_alignment'],'passed')
        self.assertEqual(result['model_finish'],'passed')
        self.assertEqual(result['controller_exit'],'failed')
        self.assertEqual(result['evidence_status'],'failed')
        self.assertEqual(result['status'],'passed')

    def test_supervisor_finish_is_separate_from_model_trace_and_receipt(self):
        self.call.update(name='act',arguments={})
        self.item.update(tool='act',arguments={},result={'content':[{'type':'text','text':'{"ok":true}'}]})
        finish={'kind':'tool_call','name':'finish','origin':'supervisor',
                'arguments':{'outcome':'aborted','reason':'client ended'}}
        result=self.audit(sim_extra=[finish])
        self.assertEqual(result['evidence_alignment'],'passed')
        self.assertEqual(result['model_finish'],'failed')
        self.assertEqual(result['supervisor_finish_calls'],1)
        # Unattributed extras cannot be silently filtered out of old evidence.
        del finish['origin']
        self.assertEqual(self.audit(sim_extra=[finish])['evidence_alignment'],'failed')
