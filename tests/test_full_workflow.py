"""Complete-reference safety and independent history regression checks (CPU only)."""
import copy
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from manipulation_agent.custom_tasks import validate_spec
from manipulation_agent.full_workflow_reference import FullWorkflowReference, validate_workflow_plan
from manipulation_agent.long_horizon import TemporalGoalEvaluator

ROOT=Path(__file__).resolve().parents[1]

class CompleteWorkflowContracts(unittest.TestCase):
    def setUp(self):
        self.specs=[json.loads(p.read_text()) for p in sorted((ROOT/'task_designs/complete_v2').glob('*.json'))]
        self.assertEqual(len(self.specs),4)

    def test_all_four_executable_contracts_have_two_complete_orders(self):
        for spec in self.specs:
            with self.subTest(task=spec['id']):
                result=validate_spec(spec)
                self.assertFalse(result['physical_feasibility_validated'])
                self.assertFalse(spec['benchmark_ready'])
                self.assertEqual(len(spec['reference_plan']['orders']),2)
                self.assertNotEqual(*spec['reference_plan']['orders'].values())
                self.assertGreaterEqual(spec['compiled_manipulation_count'],95)

    def test_missing_branch_cannot_be_called_a_full_reference(self):
        s=copy.deepcopy(self.specs[0]);s['reference_plan']['orders']['order_1'].pop()
        with self.assertRaisesRegex(ValueError,'cover every'):validate_workflow_plan(s)

    def test_wrong_dependency_order_rejected(self):
        s=copy.deepcopy(self.specs[0]);s['reference_plan']['orders']['order_1'].reverse()
        with self.assertRaisesRegex(ValueError,'dependency'):validate_workflow_plan(s)

    def test_negative_must_name_real_phase(self):
        s=copy.deepcopy(self.specs[0]);s['reference_plan']['negative_omissions']['omit_process']='nonexistent'
        with self.assertRaisesRegex(ValueError,'omitted phase'):validate_workflow_plan(s)

    def test_wait_has_bounded_native_process_budget(self):
        s=copy.deepcopy(self.specs[0]);p=next(iter(s['reference_plan']['phases'].values()))
        p['actions'].append({'primitive':'wait_for','condition':next(iter(s['evaluation']['predicates'])),'attempts':100000})
        with self.assertRaisesRegex(ValueError,'wait budget'):validate_workflow_plan(s)

    def test_extraction_approach_names_existing_role_and_preserves_grasp(self):
        s=copy.deepcopy(self.specs[0]);p=next(iter(s['reference_plan']['phases'].values()))
        p['actions'][0]['approach_role']='missing'
        with self.assertRaisesRegex(ValueError,'extraction approach'):validate_workflow_plan(s)
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([0.,0.,0.],None)))
        flow.ref=SimpleNamespace(action=Mock())
        flow.surface_point=Mock(side_effect=[[3.,0.,.5],[2.,0.,1.2],[3.,0.,.5]])
        flow.action(dict(primitive='grasp',role='food',approach_role='fridge'))
        self.assertEqual(flow.ref.action.call_args_list[0].args,('navigate_to','fridge',[2.,0.,1.2]))
        self.assertEqual(flow.ref.action.call_args_list[1].args,('grasp','food',[3.,0.,.5]))

    def test_full_task_does_not_start_with_future_slicing_outputs(self):
        for s in self.specs:
            future=set(s['future_roles'])
            self.assertFalse(future & {r['object'] for r in s['initial_layout']})
            for rule in s['replacements']:
                self.assertEqual(s['initial_assertions']['intact_'+rule['parent']],{'not':rule['parent']+'_replaced'})

    def test_approach_visible_shelf_surface_then_preserve_exact_placement_slot(self):
        from manipulation_agent.contracts import SkillError
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([0.,0.,0.],None)))
        flow.ref=SimpleNamespace(action=Mock(side_effect=[
            SkillError('navigation_search_exhausted','occluded approach'),None,None]),log=Mock())
        flow.surface_point=Mock(side_effect=[[3.,0.,.7],[3.,.4,.7]])
        slot=[3.5,.6,.7]
        flow.action(dict(primitive='place_on_top',role='shelf',point=slot))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','shelf',[3.,0.,.7]),('navigate_to','shelf',[3.,.4,.7]),
            ('place_on_top','shelf',slot)])

    def test_initial_materials_use_supported_filled_setter(self):
        for s in self.specs:
            self.assertFalse(any(r.get('state')=='Contains' and r.get('value') for r in s['initial_layout']))

    def test_large_surface_approach_stays_near_requested_work_area(self):
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([0.,0.,0.],None)))
        flow.ref=SimpleNamespace(action=Mock())
        flow.surface_point=Mock(return_value=[5.,2.,.01])
        slot=[5.,7.,.01]
        flow.action(dict(primitive='place_on_top',role='floor',point=slot))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','floor',slot),('place_on_top','floor',slot)])

    def test_fixed_wall_attachment_points_keep_authored_wall_positions(self):
        for spec in self.specs:
            fixed={row['name'] for row in spec['asset_imports'] if row.get('fixed_base')}
            staged={spec['roles'][row['object']] for row in spec['initial_layout'] if row.get('construction_staging')}
            self.assertFalse(fixed & staged)

    def test_monitor_requirement_cannot_be_unmapped(self):
        s=copy.deepcopy(next(s for s in self.specs if s['id'].startswith('study')))
        s['requirements'][0]['constraints']=[]
        with self.assertRaisesRegex(ValueError,'Hidden/unmapped'):validate_spec(s)

class IndependentTransitionGuards(unittest.TestCase):
    def tracker(self):
        return TemporalGoalEvaluator({'predicates':{'dirty':{},'on':{}},'terminal':{'ready':{'all':[{'not':'dirty'},'on']}},'transition_guards':[{'id':'off_for_clean','predicate':'dirty','from_value':True,'to_value':False,'require':{'not':'on'}}]})

    def test_switching_off_after_cleaning_does_not_erase_violation(self):
        t=self.tracker();t.observe({'dirty':True,'on':True},0,env_step=0,initial=True)
        t.observe({'dirty':False,'on':False},1,env_step=1)
        t.observe({'dirty':False,'on':True},2,env_step=2)
        self.assertFalse(t.result({'dirty':False,'on':True})['task_success'])
        self.assertEqual(t.violations[0]['id'],'off_for_clean')

    def test_safe_maintenance_and_restart_pass(self):
        t=self.tracker();t.observe({'dirty':True,'on':True},0,env_step=0,initial=True)
        for i,v in enumerate([{'dirty':True,'on':False},{'dirty':False,'on':False},{'dirty':False,'on':True}],1):t.observe(v,i,env_step=i)
        self.assertTrue(t.result({'dirty':False,'on':True})['task_success'])

    def test_recontamination_does_not_make_later_unsafe_cleaning_safe(self):
        t=self.tracker();t.observe({'dirty':True,'on':False},0,env_step=0,initial=True)
        for i,v in enumerate([{'dirty':False,'on':False},{'dirty':True,'on':True},{'dirty':False,'on':True}],1):t.observe(v,i,env_step=i)
        self.assertFalse(t.result({'dirty':False,'on':True})['task_success'])

if __name__=='__main__':unittest.main()
