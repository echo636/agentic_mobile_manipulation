"""Complete-reference safety and independent history regression checks (CPU only)."""
import copy
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from manipulation_agent.tasks.custom_tasks import validate_spec
from manipulation_agent.references.full_workflow_reference import FullWorkflowReference, validate_workflow_plan
from manipulation_agent.tasks.long_horizon import TemporalGoalEvaluator

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

    def test_grasp_uses_1p2m_threshold_while_non_grasp_uses_executor_reach(self):
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([0.,0.,0.],None)))
        flow.ref=SimpleNamespace(action=Mock())
        # Grasp within 1.2 m skips implicit navigate_to.
        flow.surface_point=Mock(return_value=[1.15,0.,.8])
        flow.action(dict(primitive='grasp',role='hotdog1',approach_role='fridge'))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[('grasp','hotdog1',[1.15,0.,.8])])
        # Grasp at 1.25 m (> 1.2 m) still triggers safe close-range approach (preserving openable approach_role).
        flow.ref.action.reset_mock()
        flow.surface_point=Mock(side_effect=[[1.25,0.,.8],[1.22,0.,1.0],[1.25,0.,.8]])
        flow.action(dict(primitive='grasp',role='log_half1',approach_role='kiln'))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','kiln',[1.22,0.,1.0]),('grasp','log_half1',[1.25,0.,.8])])
        # Non-grasp within LocalLimits.reach_m (1.35 m <= 1.4 m) skips redundant implicit navigate_to.
        flow.ref.action.reset_mock()
        flow.surface_point=Mock(return_value=[1.35,0.,.8])
        flow.action(dict(primitive='place_on_top',role='tray'))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[('place_on_top','tray',[1.35,0.,.8])])
        # Non-grasp beyond 1.4 m triggers implicit navigate_to.
        flow.ref.action.reset_mock()
        flow.surface_point=Mock(side_effect=[[1.48,0.,.8],[1.48,0.,.8]])
        flow.action(dict(primitive='place_on_top',role='tray'))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','tray',[1.48,0.,.8]),('place_on_top','tray',[1.48,0.,.8])])

    def test_explicit_floor_navigation_skips_in_place_noop(self):
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([7.2,0.5,0.],None)))
        flow.ref=SimpleNamespace(action=Mock())
        flow.action(dict(primitive='navigate_to',role='kitchen_floor',point=[7.2,0.5,0.02]))
        self.assertEqual(flow.ref.action.call_args_list,[])
        flow.action(dict(primitive='navigate_to',role='kitchen_floor',point=[6.55,0.85,0.02]))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','kitchen_floor',[6.55,0.85,0.02])])

    def test_non_openable_approach_role_does_not_push_farther_while_openable_container_preserved(self):
        Open=type('Open',(),{})
        objs={
            'tool_bench':SimpleNamespace(states={},aabb=([1.,-1.,0.],[3.,1.,0.9])),
            'bulb2':SimpleNamespace(states={},aabb=([1.45,-0.05,0.85],[1.51,0.05,0.95])),
            'dryer':SimpleNamespace(states={Open:object()},aabb=([1.4,-0.5,0.],[2.2,0.5,1.0])),
            'shorts':SimpleNamespace(states={},aabb=([1.5,-0.1,0.4],[1.7,0.1,0.6])),
        }
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([0.,0.,0.],None)))
        flow.ref=SimpleNamespace(action=Mock(),obj=objs.__getitem__)
        # Wide non-openable bench sampling a distant corner falls back to approaching the item itself.
        flow.surface_point=Mock(side_effect=[[1.48,0.,0.9],[2.1,0.6,0.9],[1.48,0.,0.9]])
        flow.action(dict(primitive='grasp',role='bulb2',approach_role='tool_bench'))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','bulb2',[1.48,0.,0.9]),('grasp','bulb2',[1.48,0.,0.9])])
        # Openable container keeps approach_role so opening-stance alignment stays intact.
        flow.ref.action.reset_mock()
        flow.surface_point=Mock(side_effect=[[1.6,0.,0.5],[2.1,0.4,0.8],[1.6,0.,0.5]])
        flow.action(dict(primitive='grasp',role='shorts',approach_role='dryer'))
        self.assertEqual([c.args for c in flow.ref.action.call_args_list],[
            ('navigate_to','dryer',[2.1,0.4,0.8]),('grasp','shorts',[1.6,0.,0.5])])

    def test_nearest_cell_skips_repeated_zero_displacement_nav_until_state_changes_or_retry(self):
        from manipulation_agent.contracts import SkillError
        tray_box=[[1.5,-0.2,0.5],[1.8,0.2,0.6]]
        objs={'tray':SimpleNamespace(states={},aabb=tray_box)}
        pose=[[0.,0.,0.]]
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:(pose[0],None)))
        flow.ref=SimpleNamespace(action=Mock(),obj=objs.__getitem__,log=Mock())
        # First grasp navigates to tray (simulated as already at nearest cell -> 0.00 m move).
        flow.surface_point=Mock(side_effect=[[1.55,0.,0.55],[1.60,0.,0.55],[1.55,0.,0.55]])
        flow.action(dict(primitive='grasp',role='clock',approach_role='tray'))
        self.assertEqual([c.args[0] for c in flow.ref.action.call_args_list],['navigate_to','grasp'])
        # Second grasp on the same stationary tray from the same nearest cell skips redundant navigate_to.
        flow.ref.action.reset_mock()
        flow.surface_point=Mock(side_effect=[[1.62,0.05,0.55],[1.60,0.,0.55]])
        flow.action(dict(primitive='grasp',role='book',approach_role='tray'))
        self.assertEqual([c.args[0] for c in flow.ref.action.call_args_list],['grasp'])
        # If the skipped action fails with out_of_reach, recovery clears cache and forces navigate_to.
        flow.ref.action=Mock(side_effect=[SkillError('out_of_reach','too far'),None,None])
        pts={'camera':[1.64,0.,0.55],'tray':[1.60,0.,0.55]}
        flow.surface_point=Mock(side_effect=lambda role,prim,attempt=0: pts[role])
        flow.action(dict(primitive='grasp',role='camera',approach_role='tray'))
        self.assertEqual([c.args[0] for c in flow.ref.action.call_args_list],['grasp','navigate_to','grasp'])
        # If the tray itself moved to a new location, cached stop no longer applies.
        flow.ref.action=Mock()
        tray_box[:]=[[1.5,1.5,0.5],[1.8,1.9,0.6]]
        flow.surface_point=Mock(side_effect=[[1.60,1.65,0.55],[1.65,1.65,0.55],[1.60,1.65,0.55]])
        flow.action(dict(primitive='grasp',role='tissues',approach_role='tray'))
        self.assertEqual([c.args[0] for c in flow.ref.action.call_args_list],['navigate_to','grasp'])
        # Opening/closing a container clears its cached stop so subsequent >1.4m operations re-align.
        Open=type('Open',(),{})
        objs['kiln']=SimpleNamespace(states={Open:object()},aabb=[[1.5,-0.2,0.],[1.9,0.2,1.0]])
        flow.ref.action=Mock()
        flow.surface_point=Mock(side_effect=[[1.55,0.,0.6],[1.55,0.,0.6]])
        flow.action(dict(primitive='open',role='kiln'))
        self.assertNotIn('kiln',flow._approach_stops)

class ReleasedPlacementOutcome(unittest.TestCase):
    """A failed release with an empty hand passes only if that item is in place."""
    def run_release(self,inside_after_release):
        import sys
        from types import ModuleType
        from unittest.mock import patch
        from manipulation_agent.contracts import SkillError
        Inside=type('Inside',(),{});OnTop=type('OnTop',(),{})
        states=ModuleType('omnigibson.object_states');states.Inside=Inside;states.OnTop=OnTop
        target=SimpleNamespace(name='dishwasher_obj')
        mug=SimpleNamespace(name='mug_obj',states={Inside:SimpleNamespace(
            get_value=lambda t:inside_after_release and t is target)})
        hand=[mug]
        def place(*args,**kwargs):
            if args[0]=='place_inside':
                hand.clear();raise SkillError('postcondition_error','Object did not remain inside after release')
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.backend=SimpleNamespace(robot=SimpleNamespace(get_position_orientation=lambda:([0.,0.,0.],None)),
            _get_held=lambda:hand[0] if hand else None,_predicate_values=lambda:{},
            custom_spec={'roles':{'mug':'mug_obj','dishwasher':'dishwasher_obj'}},
            env=SimpleNamespace(scene=SimpleNamespace(objects=[mug,target])))
        flow.ref=SimpleNamespace(action=Mock(side_effect=place),log=Mock())
        with patch.dict(sys.modules,{'omnigibson':ModuleType('omnigibson'),'omnigibson.object_states':states}):
            flow.action(dict(primitive='place_inside',role='dishwasher',point=[.5,0.,.4]))
        return flow

    def test_item_that_fell_out_stops_the_reference(self):
        from manipulation_agent.contracts import SkillError
        with self.assertRaises(SkillError) as caught:self.run_release(False)
        self.assertEqual(caught.exception.code,'reference_placement_lost')

    def test_item_that_stayed_inside_continues(self):
        flow=self.run_release(True)
        check=[c for c in flow.ref.log.call_args_list if c.args[0]=='workflow_released_placement_check']
        self.assertTrue(check[0].kwargs['placed'])

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
