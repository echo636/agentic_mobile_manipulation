import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.contracts import Budget, SkillError
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

    def test_generated_dag_and_updates_can_exceed_single_input_limit(self):
        from test_action_search import document, op
        keys = [str(i) for i in range(24)]
        operators = [op('prepare-' + k, {k: 'raw'}, {k: 'ready'}) for k in keys]
        for operator in operators:
            operator['description'] = ('Prepare the selected item on the visible work surface; '
                                       'inspect its state before moving on to other work.')
        doc = document({k: 'raw' for k in keys}, {k: {k: 'ready'} for k in keys}, operators)
        with tempfile.TemporaryDirectory() as tmp:
            h = self.harness(Path(tmp)/'episode')
            result = self.write(h, json.dumps(doc), 0, 'plan')
            self.assertTrue(result['ok'])
            advice = result['planning']
            selected = h.call('task_notes', dict(operation='select', expected_version=1,
                content=json.dumps(dict(plan_id=advice['plan_id'],
                    operation_id=advice['next']['operation_id'], steps=['Prepare visible item'],
                    next_check='Item ready'))), 'select')
            self.assertTrue(selected['ok'])
            self.assertGreater(len(h.task_notes.read()['content']), 12000)
            updated = h.call('task_notes', dict(operation='update', expected_version=2,
                content=json.dumps({'uncertainties': ['Inspect the next item']})), 'update')
            self.assertTrue(updated['ok'])
            self.assertEqual(len(json.loads(h.task_notes.read()['content'])['goals']), 25)
            self.assertFalse(self.write(h, 'x' * 12001, 3, 'oversized-input')['ok'])
            self.assertEqual(h.agent_steps, 0)

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

    def test_transport_advice_is_public_and_does_not_execute_or_block_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = self.harness(Path(tmp)/'episode')
            content = json.dumps(dict(goals=[dict(id='g', status='pending', parent_id=None)],
                transport=dict(robot_location='k', items=[dict(goal_id='g', source='k', destination='t')],
                    carriers=[], travel=[dict(source='k', destination='t', seconds=20)],
                    handling_seconds=dict(direct_item=2, load_item=2, unload_item=2, carrier_trip=4, carrier_return=2))))
            result = self.write(h, content, 0, 'transport')
            self.assertTrue(result['ok'])
            recommendation = result['transport_advice']['groups'][0]['recommended']
            self.assertEqual(recommendation['estimated_seconds'], 22)
            self.assertEqual(result['transport_advice']['plan']['steps'][0]['goals'], ['g'])
            self.assertEqual(h.task_notes.read()['content'], content)
            read = h.call('task_notes', dict(operation='read', content=None, expected_version=None), 'read')
            self.assertEqual(read['transport_advice'], result['transport_advice'])
            malformed = self.write(h, '{"transport":{}}', 1, 'incomplete')
            self.assertTrue(malformed['ok'])
            self.assertEqual(malformed['transport_advice']['status'], 'needs_revision')
            self.assertEqual(h.agent_steps, 0)
            self.assertEqual(h.backend.steps, 0)

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
                                {'ok':i != 0,'evidence_id':str(i),'effect':{'primitive':'cut'}},i)
        window=notes.read()['action_feedback_since_write']
        self.assertEqual(window['omitted']+len(window['entries']),70)
        self.assertGreater(window['omitted'],0)
        self.assertEqual(window['entries'][-1]['evidence_id'],'69')
        # Truncating detailed receipts must not erase a failure in this interval.
        self.assertTrue(notes.planning()['failed_since_checkpoint'])
        self.assertEqual(notes.planning()['execution']['failed_actions'], 1)

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

    def test_general_plan_is_in_action_loop_and_reconciles_without_certifying_goals(self):
        from test_task_planning import goal, method, notes
        with tempfile.TemporaryDirectory() as tmp:
            h = self.harness(Path(tmp)/'episode')
            self.assertEqual(h.call('initialize', {}, 'init')['planning']['status'], 'needs_model')
            doc = notes(goal('on', [method('switch-on', 2, effects={'device': 'on'})]),
                        goal('check', [method('check-indicator', 1)], depends=['on']))
            doc['active'] = dict(method_id='switch-on', serves=['on'], steps=['Toggle visible device'])
            planned = self.write(h, json.dumps(doc), 0, 'plan')['planning']
            self.assertEqual(planned['next']['method_id'], 'switch-on')
            args = dict(primitive='toggle_on', revision=h.revision,
                        target=dict(image_ref=h.snapshot['images'][0]['image_ref'], point=[.5, .5]))
            action = h.call('act', args, 'action')
            self.assertEqual(action['planning']['status'], 'reconcile')
            self.assertIsNone(action['planning']['next'])
            self.assertEqual(h.call('act', args, 'action'), action)
            self.assertEqual(h.task_notes.feedback_count, 1)
            self.assertEqual(json.loads(h.task_notes.read()['content']), doc)
            resumed = h.call('initialize', {}, 'resume')['planning']
            self.assertEqual(resumed['status'], 'reconcile')
            doc['goals'][1]['status'] = 'done'
            doc['planning']['state']['device'] = 'on'
            doc['active'] = None
            update = self.write(h, json.dumps(doc), 1, 'update')['planning']
            self.assertEqual(update['next']['method_id'], 'check-indicator')
            self.assertEqual(update['coverage']['unfinished'], 1)
            # Public planning and pending feedback are preserved in raw replay evidence.
            events = [json.loads(x) for x in (h.recorder.output/'events.jsonl').read_text().splitlines()]
            row = next(e for e in events if e.get('kind') == 'tool_result' and e.get('request_id') == 'action')
            self.assertEqual(row['result']['planning'], action['planning'])

    def test_failed_action_invalidates_recommendation_and_ablation_has_no_planner(self):
        from test_task_planning import goal, method, notes
        for mode in ('hierarchical', 'none'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                h = self.harness(Path(tmp)/'episode', mode)
                self.write(h, json.dumps(notes(goal('g', [method('inspect', 1)]))), 0, 'plan')
                h.backend.execute_visual = lambda *a, **kw: dict(private_state='PRIVATE_GROUND_TRUTH')
                args = dict(primitive='toggle_on', revision=999,
                            target=dict(image_ref=h.snapshot['images'][0]['image_ref'], point=[.5, .5]))
                result = h.call('act', args, 'failed')
                self.assertFalse(result['ok'])
                if mode == 'hierarchical':
                    self.assertTrue(result['planning']['failed_since_checkpoint'])
                    self.assertIsNone(result['planning']['next'])
                    self.assertNotIn('PRIVATE_', json.dumps(result['planning']))
                    self.assertEqual(h.task_notes.read()['version'], 1)
                else:
                    self.assertNotIn('planning', result)

    def test_compare_select_execute_reconcile_preserves_graph_and_measures_work(self):
        from test_task_planning import goal, method, notes
        with tempfile.TemporaryDirectory() as tmp:
            h = self.harness(Path(tmp)/'episode')
            doc = notes(goal('on', [method('switch-on', 2)]),
                        goal('verify', [method('check', 1)], depends=['on']))
            doc['active'] = None
            plan = self.write(h, json.dumps(doc), 0, 'plan')['planning']
            args = dict(operation='select', expected_version=1,
                content=json.dumps(dict(method_id=plan['next']['method_id'],
                    steps=['Toggle the visible switch'], next_check='Indicator on')))
            selection = h.call('task_notes', args, 'choose')
            self.assertTrue(selection['planning']['selection']['matches_recommendation'])
            self.assertEqual(h.call('task_notes', args, 'choose'), selection)
            self.assertEqual(h.task_notes.state['version'], 2)
            chosen = json.loads(h.task_notes.state['content'])
            self.assertEqual(chosen['goals'], doc['goals'])
            self.assertEqual(chosen['active']['serves'], ['on'])
            action_args = dict(primitive='toggle_on', revision=h.revision,
                target=dict(image_ref=h.snapshot['images'][0]['image_ref'], point=[.5, .5]))
            action = h.call('act', action_args, 'execute')
            elapsed = action['runtime']['tool_seconds']
            self.assertGreaterEqual(elapsed, 0)
            self.assertEqual(action['planning']['execution']['tool_seconds'], elapsed)
            sim_elapsed = action['runtime']['observed_simulation_seconds']
            self.assertAlmostEqual(sim_elapsed, 1/30)
            self.assertEqual(action['planning']['execution']['observed_simulation_seconds'], sim_elapsed)
            self.assertEqual(action['planning']['selection']['method_id'], 'switch-on')
            self.assertEqual(action['planning']['selection']['estimated_method_seconds'], 2)
            self.assertEqual(action['planning']['selection']['compared_remaining_seconds'], 3)
            self.assertEqual(h.call('act', action_args, 'execute'), action)
            self.assertEqual(h.task_notes.feedback_count, 1)
            blocked = h.call('task_notes', dict(args, expected_version=2), 'premature-choice')
            self.assertEqual(blocked['error']['code'], 'notes_need_reconciliation')
            self.assertEqual(h.task_notes.state['version'], 2)
            # Model reconciliation, not successful primitive execution, advances the DAG.
            self.assertEqual(json.loads(h.task_notes.state['content'])['goals'], doc['goals'])
            patch = dict(goals=[dict(id='on', status='done', evidence=[action['evidence_id']])],
                         planning=dict(state={'device': 'on'}))
            updated = h.call('task_notes', dict(operation='update', content=json.dumps(patch),
                                               expected_version=2), 'reconcile')
            self.assertEqual(updated['planning']['next']['method_id'], 'check')
            reconciled = json.loads(h.task_notes.state['content'])
            self.assertEqual(reconciled['goals'][2], doc['goals'][2])
            self.assertEqual(reconciled['goals'][1]['methods'], doc['goals'][1]['methods'])
            self.assertIsNone(reconciled['active'])
            self.assertEqual(reconciled['planning']['location'], 'bench')
            self.assertEqual(h.call('task_notes', dict(operation='update', content=json.dumps(patch),
                expected_version=2), 'stale-update')['error']['code'], 'notes_version_conflict')
            self.assertEqual(h.task_notes.feedback_seconds, 0)
            self.assertEqual(h.task_notes.feedback_simulation_seconds, 0)
            replay = build_replay(h.recorder.output)
            action_step = next(s for s in replay['steps'] if s['request_id'] == 'execute')
            self.assertEqual(action_step['goal_state']['active'], chosen['active'])
            self.assertEqual(replay['planning_metrics']['method_selections'], 1)
            update_step = next(s for s in replay['steps'] if s['request_id'] == 'reconcile')
            self.assertEqual(update_step['goal_state'], reconciled)
            from manipulation_agent.reporting.goal_replay import goal_replacement_review
            events = [json.loads(line) for line in (h.recorder.output/'events.jsonl').read_text().splitlines()]
            review = goal_replacement_review(events)
            self.assertIn('on', review[-1]['newly_done'])

    def test_observed_process_progress_replans_without_claiming_completion(self):
        from test_task_planning import goal, method, notes
        with tempfile.TemporaryDirectory() as tmp:
            h = self.harness(Path(tmp)/'episode')
            doc = notes(goal('work', [method('work', 2, simulation_seconds=1/30)]),
                        goal('processed', [method('check', 1, awaits=['device'])]),
                        processes={'device': 1/30}, wait_cost_per_sim_second=300)
            doc['goals'][2].update(goal='Processed result ready', check='Visible finished material')
            self.assertEqual(self.write(h, json.dumps(doc), 0, 'plan')['planning']['next']['method_id'], 'work')
            h.call('task_notes', dict(operation='select', expected_version=1,
                content=json.dumps(dict(method_id='work', steps=['Operate visible switch'],
                                        next_check='Switch changed'))), 'choose')
            action = h.call('act', dict(primitive='toggle_on', revision=h.revision,
                target=dict(image_ref=h.snapshot['images'][0]['image_ref'], point=[.5, .5])), 'execute')
            progress = action['planning']['execution']['observed_simulation_seconds']
            read = h.call('task_notes', dict(operation='read', content=None, expected_version=None), 'read')
            self.assertEqual(read['planning']['execution']['observed_simulation_seconds'], progress)
            self.assertEqual(json.loads(h.task_notes.state['content'])['goals'][2]['status'], 'pending')
            # The model reconciles actual elapsed simulation time, then checks the result.
            doc['goals'][1]['status'] = 'done'
            doc['planning']['processes']['device'] = max(0, 1/30 - progress)
            doc['active'] = None
            update = self.write(h, json.dumps(doc), 2, 'update')['planning']
            self.assertEqual(update['next']['method_id'], 'check')
            self.assertEqual(update['estimated_seconds'], 1)
            self.assertEqual(json.loads(h.task_notes.state['content'])['goals'][2]['status'], 'pending')
            selected = h.call('task_notes', dict(operation='select', expected_version=3,
                content=json.dumps(dict(method_id='check', steps=['Inspect the processed material'],
                                        next_check='Visible finished material'))), 'choose-check')
            context = selected['planning']['selected_method']
            self.assertEqual(context['processes'], dict(starts={}, awaits=['device'], wait_simulation_seconds=0))
            self.assertEqual(context['results'], [dict(id='processed', goal='Processed result ready',
                                                      check='Visible finished material')])

    def test_processing_failure_recompares_methods_across_branches(self):
        from test_task_planning import goal, method, notes
        with tempfile.TemporaryDirectory() as tmp:
            h = self.harness(Path(tmp)/'episode')
            doc = notes(goal('a', [method('manual-a', 10),
                                  method('powered-pair', 4, also=['b'], requires={'device': 'available'},
                                         setup={'tool': 'fixture'}, effects={'material': 'processed'},
                                         description='Process both components with the configured fixture')]),
                        goal('b', [method('manual-b', 10)]),
                        goal('check', [method('inspect-result', 1)], depends=['a', 'b']))
            doc['goals'][1].update(goal='First component processed', check='First component surface ready')
            doc['goals'][2].update(goal='Second component processed', check='Second component surface ready')
            doc['planning']['state'] = {'device': 'available', 'tool': 'none'}
            doc['planning']['switches'] = [dict(key='tool', **{'from': 'none', 'to': 'fixture'},
                                               at='bench', seconds=2)]
            plan = self.write(h, json.dumps(doc), 0, 'plan')['planning']
            self.assertEqual(plan['next']['method_id'], 'powered-pair')
            self.assertEqual(plan['estimated_seconds'], 7)
            self.assertEqual(plan['comparison']['distinct_method_sets'], 2)
            self.assertEqual(plan['comparison']['estimated_cost_range_seconds'], [7, 21])

            def select(method_id, request_id):
                return h.call('task_notes', dict(operation='select',
                    expected_version=h.task_notes.state['version'],
                    content=json.dumps(dict(method_id=method_id, steps=['Process observed items'],
                                            next_check='Both results visible'))), request_id)

            selected = select('powered-pair', 'select-powered')
            context = selected['planning']['selected_method']
            self.assertEqual(context['description'], plan['next']['description'])
            self.assertEqual(context['route'], ['bench'])
            self.assertEqual(context['requires'], {'device': 'available'})
            self.assertEqual(context['setup_changes'], [dict(key='tool', before='none', after='fixture')])
            self.assertEqual(context['predicted_effects'], {'material': 'processed'})
            self.assertEqual([r['id'] for r in context['results']], ['a', 'b'])
            self.assertEqual(context['results'][1]['check'], 'Second component surface ready')
            def unavailable(*args, **kwargs):
                raise SkillError('pre_condition_error', 'The device could not be activated.')
            execute_visual = h.backend.execute_visual
            h.backend.execute_visual = unavailable
            result = h.call('act', dict(primitive='toggle_on', revision=h.revision,
                target=dict(image_ref=h.snapshot['images'][0]['image_ref'], point=[.5, .5])), 'activate')
            self.assertTrue(result['planning']['failed_since_checkpoint'])
            self.assertIsNone(result['planning']['next'])
            self.assertEqual(result['planning']['selected_method'], context)
            self.assertEqual(result['planning']['checkpoint_comparison'], plan['comparison'])
            self.assertEqual(h.call('initialize', {}, 'resume')['planning']['selected_method'], context)
            # A caller reconciles the public failure; the harness cannot infer semantic truth.
            plan = h.call('task_notes', dict(operation='update', expected_version=2,
                content=json.dumps(dict(planning=dict(state={'device': 'unavailable'})))),
                'reconcile')['planning']
            self.assertIsNone(plan['selected_method'])
            measured = plan['previous_execution']
            self.assertEqual(measured['method_id'], 'powered-pair')
            self.assertEqual(measured['intended_results'], ['a', 'b'])
            self.assertEqual(measured['estimated_method_seconds'], 6)
            self.assertEqual(measured['observed'], result['planning']['execution'])
            self.assertEqual(measured['observed']['failed_actions'], 1)
            self.assertEqual(measured['action_attempts'], 1)
            self.assertEqual(h.call('initialize', {}, 'resume-after-update')['planning']['previous_execution'], measured)
            self.assertEqual(json.loads((h.recorder.output/'task_notes.json').read_text())['previous_execution'], measured)
            self.assertEqual(plan['estimated_seconds'], 21)
            self.assertEqual(plan['comparison']['distinct_method_sets'], 1)
            self.assertEqual(plan['comparison']['estimated_cost_range_seconds'], [21, 21])
            self.assertEqual({s['method_id'] for s in plan['steps'][:2]}, {'manual-a', 'manual-b'})
            selected = select(plan['next']['method_id'], 'select-manual')
            self.assertTrue(selected['planning']['selection']['matches_recommendation'])
            self.assertEqual(selected['planning']['previous_execution'], measured)
            self.assertEqual(selected['planning']['selected_method']['requires'], {})
            self.assertEqual(selected['planning']['selected_method']['method_id'], plan['next']['method_id'])
            self.assertEqual([g['status'] for g in doc['goals']], ['pending'] * 4)
            # Complete the same MCP loop through both branches and final inspection.
            # This is a scripted CPU contract fixture, not physical task evidence.
            h.backend.execute_visual = execute_visual
            for index in range(3):
                if index:
                    selected = select(plan['next']['method_id'], 'choose-' + str(index))
                served = selected['planning']['selected_method']['results']
                action = h.call('act', dict(primitive='cut', revision=h.revision,
                    target=dict(image_ref=h.snapshot['images'][0]['image_ref'], point=[.5, .5])),
                    'fixture-action-' + str(index)) if index < 2 else h.call('look',
                    dict(yaw_degrees=0, revision=h.revision), 'fixture-inspect')
                self.assertTrue(action['ok'])
                self.assertIsNone(action['planning']['next'])
                plan = h.call('task_notes', dict(operation='update',
                    expected_version=h.task_notes.state['version'], content=json.dumps(dict(goals=[
                        dict(id=g['id'], status='done', evidence=[action['evidence_id']]) for g in served]))),
                    'fixture-reconcile-' + str(index))['planning']
                self.assertEqual(plan['coverage']['unfinished'], 2-index)
                measured = plan['previous_execution']
                self.assertEqual(measured['method_id'], selected['planning']['selection']['method_id'])
                self.assertEqual(measured['observed']['failed_actions'], 0)
                self.assertEqual(measured['observed']['tool_seconds'], action['runtime']['tool_seconds'])
            self.assertIsNone(plan['next'])
            self.assertEqual(plan['verification_goals'], ['root'])
            plan = h.call('task_notes', dict(operation='update',
                expected_version=h.task_notes.state['version'], content=json.dumps(dict(goals=[
                    dict(id='root', status='done', evidence=[action['evidence_id']])]))),
                'fixture-root-check')['planning']
            self.assertEqual(plan['verification_goals'], [])
            h.call('finish', dict(outcome='achieved', reason='CPU fixture claim only'), 'finish')
            self.assertFalse(h.recorder.run['task_success'])  # Notes cannot change evaluation.
            replay = build_replay(h.recorder.output)
            self.assertEqual(replay['planning_metrics']['method_selections'], 4)


if __name__=='__main__':unittest.main()
