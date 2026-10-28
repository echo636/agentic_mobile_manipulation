import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.contracts import Budget
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.agent.vision_policy import system_prompt
from manipulation_agent.replay import build_replay


def tree(status='active'):
    return {'format':'goal-tree-v1','L1':{'instruction':'fixture','goal':'Turn on the device',
        'criteria':[{'id':'r1','condition':'device on','basis':'instruction'}],'assumptions':[]},
        'L2':[{'id':'g1','goal':'Device on','covers':['r1'],'depends_on':[],
               'status':status,'check':'visible indicator and operation','evidence':[]}],
        'active':{'subgoal_id':'g1','steps':['toggle_on the visible device'],'next_check':'indicator'},
        'uncertainties':[],'change':'initial' if status=='active' else 'observed result'}


def branching_tree():
    """Uneven branches and a dependency across branches, including four levels."""
    goals = [
        ('root', None, 'The workspace is ready'),
        ('work', 'root', 'Work completed'),
        ('supplies', 'root', 'Supplies ready'),
        ('storage', 'root', 'Storage restored'),
        ('prepare', 'work', 'Preparation complete'),
        ('process', 'work', 'Processing complete'),
        ('access', 'prepare', 'Work area accessible'),
        ('closed', 'storage', 'Cabinet closed'),
    ]
    return {'format': 'goal-tree-v2', 'root_id': 'root',
        'requirements': [{'id': 'r1', 'condition': 'Workspace ready', 'basis': 'explicit'}],
        'goals': [dict(id=id, parent_id=parent, goal=goal, covers=['r1'],
            depends_on=['supplies'] if id == 'process' else [], status='pending',
            check='Visible result', evidence=[]) for id, parent, goal in goals],
        'active': {'goal_id': 'access', 'steps': ['Inspect access'], 'next_check': 'Access visible'},
        'assumptions': [], 'uncertainties': [], 'change': 'Initial decomposition'}


class TaskNotesTests(unittest.TestCase):
    def harness(self,path,mode='hierarchical'):
        r=Recorder(path,{'backend':'mock','observation_mode':'rgb_only','instruction':'fixture'})
        return VisionHarness(MockRGBBackend(path),r,Budget(wall_seconds=None),agent_step_limit=50,
                             goal_skill=mode,require_task_notes=True)

    def write(self,h,text,version,rid):
        return h.call('task_notes',{'operation':'write','content':text,'expected_version':version},rid)

    def test_control_and_treatment_share_tools_prompt_and_other_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.harness(Path(tmp)/'a');b=self.harness(Path(tmp)/'b','none')
            self.assertEqual(a.tool_specs(),b.tool_specs())
            self.assertEqual(set(a.skills.resources)-set(b.skills.resources),
                {('goal-decomposition','SKILL.md'),('goal-decomposition','references/working-state.md')})
            for key,text in b.skills.resources.items():
                self.assertEqual(text,a.skills.resources[key]);self.assertNotIn('goal-decomposition',text)
            self.assertFalse((Path(tmp)/'b/skill_snapshot/goal-decomposition').exists())
            response=b.call('read_skill',{'name':'goal-decomposition','resource':'SKILL.md'},'no-access')
            self.assertEqual(response['error']['code'],'unknown_skill_resource')
            base=system_prompt(goal_skill='none')
            self.assertNotIn('goal-decomposition',base);self.assertNotIn('L1',base)
            self.assertTrue(system_prompt().startswith(base))

    def test_versions_retries_and_conflicts_preserve_previous_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=self.harness(Path(tmp)/'episode')
            first=self.write(h,'My free-form plan',0,'one')
            self.assertEqual(first['notes']['version'],1)
            self.assertNotIn('content',first['notes'])
            self.assertEqual(self.write(h,'My free-form plan',0,'one'),first)
            self.assertEqual(self.write(h,'Accidental overwrite',0,'stale')['error']['code'],'notes_version_conflict')
            self.assertEqual(h.task_notes.read()['content'],'My free-form plan')
            read=h.call('task_notes',{'operation':'read','content':None,'expected_version':None},'read')
            self.assertEqual(read['notes']['content'],'My free-form plan')
            self.assertEqual(self.write(h,'My free-form plan',1,'two')['notes']['version'],2)
            self.assertEqual(h.agent_steps,0);self.assertEqual(h.backend.steps,0)
            self.assertEqual(json.loads((h.recorder.output/'task_notes.json').read_text())['version'],2)

    def test_resume_retains_public_feedback_after_stale_authored_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=self.harness(Path(tmp)/'episode')
            self.write(h,'Food is on the board; next cut it.',0,'plan')
            transitions=iter(['SlicingRule','DicingRule','DicingRule'])
            h.backend.execute_visual=lambda *a,**kw:dict(transition=next(transitions),created_objects=2,
                private_grounding={'selected_object_prim':'PRIVATE_OBJECT'},task_success='PRIVATE_SCORE')
            replies=[]
            for i in range(3):
                args={'primitive':'cut','revision':h.revision,
                      'target':{'image_ref':h.snapshot['images'][0]['image_ref'],'point':[.5,.5]}}
                replies.append(h.call('act',args,'cut-'+str(i)))
            self.assertEqual(h.call('act',args,'cut-2'),replies[-1])
            failed=h.call('act',args,'stale-click')
            self.assertFalse(failed['ok'])
            before_steps=h.backend.steps
            notes=h.call('initialize',{},'resumed')['notes']
            history=notes['action_feedback_since_write']
            self.assertEqual(history['omitted'],0)
            entries=history['entries']
            self.assertEqual([x['evidence_id'] for x in entries],
                             [x['evidence_id'] for x in replies]+[failed['evidence_id']])
            self.assertEqual([x['effect']['cut']['result'] for x in entries[:3]],
                             ['sliced','diced_material','diced_material'])
            self.assertEqual(entries[-1]['error']['code'],'stale_observation')
            self.assertEqual(notes['content'],'Food is on the board; next cut it.')
            self.assertNotIn('PRIVATE_',json.dumps(notes))
            self.assertTrue(all('observation' not in entry for entry in entries))
            self.assertEqual(h.backend.steps,before_steps)
            read=h.call('task_notes',{'operation':'read','content':None,'expected_version':None},'read')
            self.assertEqual(read['notes'],notes)
            updated=self.write(h,'Food diced; transfer next.',1,'updated')
            self.assertEqual(updated['notes']['action_feedback_since_write'],{'entries':[],'omitted':0})

    def test_feedback_window_reports_missing_earlier_actions(self):
        from manipulation_agent.agent.task_notes import TaskNotes
        notes=TaskNotes(Path('/unused'))
        for i in range(70):
            notes.record_action('act',{'primitive':'cut','target':None},
                                {'ok':True,'evidence_id':str(i),'effect':{'primitive':'cut'}},i)
        window=notes.read()['action_feedback_since_write']
        self.assertEqual(window['omitted']+len(window['entries']),70)
        self.assertGreater(window['omitted'],0)
        self.assertEqual(window['entries'][-1]['evidence_id'],'69')

    def test_both_arms_require_an_authored_plan_before_first_action(self):
        for mode in ('hierarchical','none'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                h=self.harness(Path(tmp)/'episode',mode);obs=h.initialize()['observation']
                args={'primitive':'toggle_on','revision':0,'target':{'image_ref':obs['images'][0]['image_ref'],'point':[.5,.5]}}
                self.assertEqual(h.call('act',args,'premature')['error']['code'],'task_notes_required')
                self.assertEqual(h.backend.steps,0)
                self.assertEqual(h.agent_steps,1) # Failed attempts count equally in both arms.
                self.write(h,'Inspect indicator, toggle device, check returned images.',0,'plan')
                self.assertTrue(h.call('act',args,'action')['ok'])
                self.assertEqual(h.agent_steps,2)

    def test_replay_has_initial_plan_current_tree_and_changes_without_future_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=self.harness(Path(tmp)/'episode');h.call('initialize',{},'initialize')
            first=self.write(h,json.dumps(tree()),0,'plan')
            obs=h.snapshot
            h.call('act',{'primitive':'toggle_on','revision':0,'target':{'image_ref':obs['images'][0]['image_ref'],'point':[.5,.5]}},'action')
            final=tree('done');final['L2'][0]['evidence']=['event-actual: indicator changed'];final['active']=None
            self.write(h,json.dumps(final),1,'update')
            h.call('finish',{'outcome':'achieved','reason':'fixture'},'finish')
            replay=build_replay(h.recorder.output)
            action=next(s for s in replay['steps'] if s['tool']=='act')
            self.assertEqual(action['task_notes']['version'],1)
            self.assertEqual(action['task_notes']['content'],json.dumps(tree()))
            self.assertEqual(action['goal_state']['L2'][0]['status'],'active')
            update=next(s for s in replay['steps'] if s['request_id']=='update')
            self.assertEqual(update['task_notes_before']['version'],1)
            self.assertEqual(update['goal_state']['L2'][0]['status'],'done')
            self.assertTrue(replay['planning_metrics']['goal_tree_before_first_action'])
            self.assertEqual(replay['planning_metrics']['goal_tree_versions'],2)
            self.assertEqual([x['agent_step'] for x in replay['task_note_versions']],[0,1])
            self.assertIs(h.recorder.run['task_success'],True)

    def test_false_done_notes_cannot_change_independent_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=self.harness(Path(tmp)/'episode')
            self.write(h,json.dumps(tree('done')),0,'claim')
            h.call('finish',{'outcome':'achieved','reason':'fixture claim'},'finish')
            self.assertIs(h.recorder.run['task_success'],False)

    def test_v2_preserves_uneven_authored_tree_and_independent_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=self.harness(Path(tmp)/'episode')
            initial=branching_tree()
            self.write(h,json.dumps(initial),0,'plan')
            final=branching_tree()
            for goal in final['goals']:
                goal['status']='done'
            final['active']=None
            self.write(h,json.dumps(final),1,'claim')
            h.call('finish',{'outcome':'achieved','reason':'fixture claim'},'finish')
            replay=build_replay(h.recorder.output)
            writes=[s for s in replay['steps'] if s['tool']=='task_notes']
            self.assertEqual(writes[0]['goal_state'],initial)
            self.assertEqual(writes[1]['goal_state'],final)
            self.assertEqual(writes[1]['task_notes_before']['version'],1)
            self.assertEqual(replay['planning_metrics']['goal_tree_versions'],2)
            self.assertIs(h.recorder.run['task_success'],False)

    def test_no_hierarchy_is_fabricated_from_control_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=self.harness(Path(tmp)/'episode','none')
            self.write(h,'Inspect the device and turn it on.',0,'plain')
            bad=self.write(h,'Lost replacement',0,'bad')
            self.assertFalse(bad['ok'])
            replay=build_replay(h.recorder.output)
            self.assertEqual(len(replay['task_note_versions']),1)
            self.assertTrue(all(s['goal_state'] is None for s in replay['steps']))


if __name__=='__main__':unittest.main()
