"""Protect the new task contracts against false positives, not physics claims."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from manipulation_agent.custom_tasks import load_spec, validate_spec
from manipulation_agent.diverse_task_contract import replacement_complete, robot_spawn_supports
from manipulation_agent.long_horizon import TemporalGoalEvaluator

ROOT=Path(__file__).resolve().parents[1]
def recipe(prefix):
    return json.loads(next((ROOT/'task_designs/constructed_100plus').glob(prefix+'*.json')).read_text())


class ConstructionContracts(unittest.TestCase):
    def test_parked_and_fallen_robot_cannot_pass_construction(self):
        bounds={'floor':([0.,0.,-.3],[10.,10.,0.])}
        self.assertEqual(robot_spawn_supports([2.,2.,0.],bounds),['floor'])
        for pos in ([300.,300.,0.],[2.,2.,-40.],[float('nan'),2.,0.]):
            self.assertEqual(robot_spawn_supports(pos,bounds),[])

    def test_restore_reenables_native_transitions_even_on_error(self):
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        for fail in (False,True):
            gm=SimpleNamespace(ENABLE_TRANSITION_RULES=True,unlocked=nullcontext)
            refresh=Mock()
            def restore(data):
                self.assertFalse(gm.ENABLE_TRANSITION_RULES)
                if fail:raise RuntimeError('restore failed')
            backend=CustomTaskBackend.__new__(CustomTaskBackend)
            backend.custom_spec={'schema_version':3}
            backend.env=SimpleNamespace(scene=SimpleNamespace(restore=restore,
                transition_rule_api=SimpleNamespace(refresh_all_rules=refresh)))
            with patch.dict('sys.modules',{'omnigibson.macros':SimpleNamespace(gm=gm)}):
                if fail:
                    with self.assertRaisesRegex(RuntimeError,'restore failed'):backend._restore_custom_scene({})
                else:backend._restore_custom_scene({})
            self.assertTrue(gm.ENABLE_TRANSITION_RULES)
            refresh.assert_called_once_with()

    def test_recipes_have_public_mapping_and_no_frozen_claim(self):
        paths=list((ROOT/'task_designs/constructed_100plus').glob('*.json'))
        self.assertEqual(len(paths),4)
        for p in paths:
            spec,result=load_spec(p)
            self.assertFalse(result['physical_feasibility_validated'])
            self.assertFalse(spec['benchmark_ready'])
            with self.assertRaisesRegex(ValueError,'no frozen'):load_spec(p,require_frozen=True)

    def test_extended_budget_does_not_relax_historical_pilot(self):
        spec=recipe('garden')
        validate_spec(spec)
        spec['schema_version']=2
        with self.assertRaisesRegex(ValueError,'200-attempt'):validate_spec(spec)
        spec['schema_version']=3;spec['acceptance_cases']['max_reference_action_attempts']=1001
        with self.assertRaisesRegex(ValueError,'1000-attempt'):validate_spec(spec)

    def test_future_entities_cannot_be_fabricated_at_construction(self):
        spec=recipe('garage');child=spec['future_roles'][0]
        spec['initial_layout'].append({'object':child,'position':[0,0,0]})
        with self.assertRaisesRegex(ValueError,'future'):validate_spec(spec)

    def test_background_exclusion_cannot_delete_a_scored_entity(self):
        spec=recipe('garden');spec['scene_exclusions']=[spec['roles']['plant00']]
        with self.assertRaisesRegex(ValueError,'outside task roles'):validate_spec(spec)

    def test_destination_count_cannot_double_count_one_member(self):
        spec=recipe('garden')
        row=spec['evaluation']['predicates']['destination0_count']
        row['members'][1]=row['members'][0]
        with self.assertRaisesRegex(ValueError,'distinct'):validate_spec(spec)

    def test_returning_a_moved_control_does_not_erase_the_violation(self):
        e=TemporalGoalEvaluator(recipe('garden')['evaluation'])
        v=dict.fromkeys(e.spec['predicates'],False)
        for i in range(4):v[f'control{i}_on_controls']=True
        e.observe(v,0,env_step=0,initial=True)
        v['control0_on_controls']=False;e.observe(v,1,env_step=1)
        v['control0_on_controls']=True;e.observe(v,2,env_step=2)
        self.assertFalse(e.result(v)['constraint_conditions']['control0_stays'])

    def test_same_looking_parts_do_not_excuse_lost_parent(self):
        spec=recipe('garage');rule=spec['replacements'][0];roles=spec['roles']
        initially={roles[rule['parent']]}
        live={roles[c['role']]:SimpleNamespace(category=c['category'],model=c['model']) for c in rule['children']}
        self.assertTrue(replacement_complete(rule,roles,live,initially))
        missing=copy.copy(live);missing.pop(roles[rule['children'][0]['role']])
        self.assertFalse(replacement_complete(rule,roles,missing,initially))
        self.assertFalse(replacement_complete(rule,roles,live,set()))
        self.assertFalse(replacement_complete(rule,roles,live,initially|set(live)))
        live[roles[rule['children'][0]['role']]].model='wrong_model'
        self.assertFalse(replacement_complete(rule,roles,live,initially))

    def test_correct_garden_terminal_cannot_replace_treatment_history(self):
        spec=recipe('garden');e=TemporalGoalEvaluator(spec['evaluation'])
        v=dict.fromkeys(spec['evaluation']['predicates'],False)
        for i in range(20):v.update({f'plant{i:02d}_Covered_insectifuge':True,f'plant{i:02d}_on_destination0':True})
        for i in range(4):v[f'control{i}_on_controls']=True
        v.update(tool_on_tool_home=True,hand_empty=True)
        v.update(destination0_count=True,destination1_count=True)
        e.observe(v,0,env_step=0,initial=True);e.observe(v,1,env_step=1)
        result=e.result(v)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertFalse(result['task_success'])
        self.assertTrue(result['public_constraint_violations'])

    def test_control_contamination_is_not_erased_by_later_cleaning(self):
        e=TemporalGoalEvaluator(recipe('garden')['evaluation']);v=dict.fromkeys(e.spec['predicates'],False)
        e.observe(v,0,env_step=0,initial=True)
        v['control0_Covered_insectifuge']=True;e.observe(v,1,env_step=1)
        v['control0_Covered_insectifuge']=False;e.observe(v,2,env_step=2)
        self.assertFalse(e.result(v)['constraint_conditions']['control0_never_spray'])

    def test_per_item_treatment_accepts_both_processing_orders(self):
        for sequence in [list(range(20)),list(reversed(range(20)))]:
            e=TemporalGoalEvaluator(recipe('garden')['evaluation']);v=dict.fromkeys(e.spec['predicates'],False)
            for i in range(4):v[f'control{i}_on_controls']=True
            v.update(tool_on_tool_home=True,hand_empty=True)
            v.update(destination0_count=True,destination1_count=True)
            e.observe(v,0,env_step=0,initial=True)
            def observe():e.observe(v,e.last_sequence+1,env_step=e.last_sequence+1)
            for i in sequence:
                obj=f'plant{i:02d}'
                v[obj+'_on_stage']=True;observe()
                v[obj+'_Covered_insectifuge']=True;observe()
                v[obj+'_on_stage']=False;v[obj+'_on_destination0']=True;observe()
            self.assertTrue(e.result(v)['task_success'])

    def test_floor_cleaning_before_clearance_remains_a_violation(self):
        e=TemporalGoalEvaluator(recipe('bedroom')['evaluation']);v=dict.fromkeys(e.spec['predicates'],False)
        v.update(floor_Covered_dust=True,floor_b_Covered_dust=True)
        for room in ['a','b']:
            for i in range(2):v[f'{room}_lamp{i}_ToggledOn']=True
        e.observe(v,0,env_step=0,initial=True)
        v['floor_Covered_dust']=False;e.observe(v,1,env_step=1)
        for i in range(12):v[f'a_item{i:02d}_on_a_stage']=True
        e.observe(v,2,env_step=2)
        self.assertFalse(e.result(v)['constraint_conditions']['a_clear_before_vacuum'])


if __name__=='__main__':unittest.main()
