"""Real CPU sandbox/broker checks. No simulator or physical-success claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.exploration_runner import run_controller
from manipulation_agent.exploration_workspace import safe_tree, tree_hash
from manipulation_agent.manip_runtime import RecipeContext
from test_navigate_manip_pipeline import fixture_source


CODE = '''def Manip(ctx, **invocation):
    ctx.call('move_joints',group='arm_right',positions_rad=[0]*7)
    ctx.call('grasp',arm='right',target_object_id='bottle')
    return {'ok':True}
def main(ctx):
    ctx.call('observe_scene')
    return ctx.call('manip',recipe='fixture_pick',targets={'object':{'task_name':'bottle'}},allow_draft=True)
'''


class AtomicManipTests(unittest.TestCase):
    def execute(self, code=CODE, fail=None, wrapped=False):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        bundle = self.root/'project'; fixture_source(bundle)
        (bundle/'controller.py').write_text(code)
        self.sent = []
        self.snapshot = {'revision':0, 'images':[], 'oracle_3d':{'robot':{'arms':{
            'left':{'held_object':None},'right':{'held_object':None}}}}}
        def send(name, args, ident):
            self.sent.append({'name':name,'request_id':ident})
            if name != 'observe_scene':self.snapshot['revision'] += 1
            return {'ok':name != fail, 'observation':copy.deepcopy(self.snapshot)}
        self.output = self.root/'output'
        call = send
        if wrapped:
            call = RecipeContext(send, lambda: self.snapshot,
                                 {'allow_base_motion':False,'allow_trunk_motion':True}).invoke
        result = run_controller(bundle, tree_hash(safe_tree(bundle)), self.output, call,
                                {'observe_scene','move_joints','grasp','release'})
        return result

    def test_one_public_call_runs_frozen_sequence_with_linked_child_ids(self):
        result = self.execute()
        self.assertTrue(result['clean'], (self.output/'stderr.log').read_text())
        self.assertTrue(result['controller_result']['ok'])
        trace = [json.loads(line) for line in (self.output/'procedure_trace.jsonl').read_text().splitlines()]
        self.assertEqual([r['kind'] for r in trace], ['procedure_call','procedure_child','procedure_child','procedure_result'])
        parent = trace[0]['request_id']
        self.assertEqual([c['name'] for c in self.sent], ['observe_scene','move_joints','grasp'])
        self.assertEqual([r['request_id'] for r in trace if r['kind']=='procedure_child'],
                         [c['request_id'] for c in self.sent[1:]])
        self.assertTrue(all(r['parent_request_id']==parent for r in trace if r['kind']=='procedure_child'))
        self.assertEqual(trace[-1]['request_id'], parent)
        self.assertEqual(trace[-1]['result']['observation']['revision'], 2)

    def test_failure_cannot_be_swallowed_to_retry_release_or_claim_success(self):
        code = CODE.replace("    ctx.call('grasp',arm='right',target_object_id='bottle')", """    try:ctx.call('grasp',arm='right',target_object_id='bottle')
    except RuntimeError:pass
    try:ctx.call('release',arm='right')
    except RuntimeError:pass""")
        result = self.execute(code, fail='grasp')
        self.assertFalse(result['clean'])
        self.assertNotIn('release', [c['name'] for c in self.sent])
        self.assertFalse(result['procedures'][-1]['result']['ok'])
        self.assertEqual(result['procedures'][-1]['result']['observation']['revision'], 2)

    def test_wrapped_development_failure_preserves_actual_failed_revision(self):
        result = self.execute(fail='grasp', wrapped=True)
        self.assertFalse(result['clean'])
        self.assertEqual(result['procedures'][-1]['result']['observation']['revision'], 2)

    def test_wrong_recipe_rejected_before_motor_dispatch(self):
        result = self.execute(CODE.replace("recipe='fixture_pick'", "recipe='missing'"))
        self.assertFalse(result['clean'])
        self.assertEqual([c['name'] for c in self.sent], ['observe_scene'])
        self.assertEqual([r['kind'] for r in result['procedures']], ['procedure_call','procedure_result'])
        self.assertFalse(result['procedures'][-1]['result']['ok'])

    def test_stale_revision_is_recorded_without_dispatching_motion(self):
        result = self.execute(CODE.replace("allow_draft=True)", "revision=99,allow_draft=True)"))
        self.assertFalse(result['clean'])
        self.assertEqual([c['name'] for c in self.sent], ['observe_scene'])
        self.assertIn('Stale', result['procedures'][-1]['result']['error'])

    def test_host_rejects_trunk_motion_even_if_controller_ignores_constraints(self):
        code = CODE.replace("group='arm_right'", "group='trunk'").replace(
            "allow_draft=True)", "constraints={'allow_trunk_motion':False},allow_draft=True)")
        result = self.execute(code)
        self.assertFalse(result['clean'])
        self.assertEqual([c['name'] for c in self.sent], ['observe_scene'])

    def test_sandbox_exit_leaves_failed_parent_instead_of_success(self):
        code = CODE.replace("    ctx.call('grasp',arm='right',target_object_id='bottle')", "    raise SystemExit(0)")
        result = self.execute(code)
        self.assertFalse(result['clean'])
        self.assertFalse(result['procedures'][-1]['result']['ok'])
        self.assertIn('before Manip returned', result['procedures'][-1]['result']['error'])


if __name__ == '__main__':unittest.main()
