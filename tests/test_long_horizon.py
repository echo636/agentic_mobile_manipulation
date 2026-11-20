import copy
import unittest
from types import SimpleNamespace

from manipulation_agent.long_horizon import TemporalGoalEvaluator


SPEC = {
    'predicates': {k:{} for k in ['packed_at_source','delivered','unpacked','returned','clean','closed','contents']},
    'terminal': {'tools_ready':'unpacked','box_returned':'returned','bench_clean':'clean'},
    'milestones': {
        'packed': {'when':'packed_at_source'},
        'delivered': {'when':'delivered','after':['packed']},
        'unpacked': {'when':'unpacked','after':['delivered']},
        'returned': {'when':'returned','after':['unpacked']},
    },
    'motion_guards':[{'id':'closed_loaded_transport','object':'box','since':'packed','until':'delivered',
                      'require':{'all':['closed','contents']}}],
}


class LongHorizonTests(unittest.TestCase):
    def test_unknown_particle_system_fails_before_simulator_start(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from manipulation_agent.executors.custom_task import validate_system_references
        spec={'evaluation':{'predicates':{'cooked':{'system':'cooked_popcorn'}}}}
        with TemporaryDirectory() as folder:
            (Path(folder)/'cooked__popcorn').mkdir()
            with self.assertRaisesRegex(ValueError,'cooked_popcorn'):
                validate_system_references(spec,folder)
            spec['evaluation']['predicates']['cooked']['system']='cooked__popcorn'
            validate_system_references(spec,folder)

    def test_later_cleanup_cannot_erase_premature_serving(self):
        spec={'predicates':{'clean':{},'served':{}},'terminal':{'ready':{'all':['clean','served']}},
              'milestones':{'clean_empty':{'when':{'all':['clean',{'not':'served'}]}},
                            'served':{'after':['clean_empty'],'when':'served'}},
              'state_guards':[{'id':'clean_before_serving','until':'clean_empty','require':{'not':'served'}}]}
        evaluator=TemporalGoalEvaluator(spec)
        evaluator.observe({'clean':False,'served':False},0,env_step=0,initial=True)
        evaluator.observe({'clean':False,'served':True},1,env_step=10)
        evaluator.observe({'clean':True,'served':False},2,env_step=20)
        evaluator.observe({'clean':True,'served':True},3,env_step=30)
        result=evaluator.result({'clean':True,'served':True})
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertTrue(all(result['historical_conditions'].values()))
        self.assertFalse(result['task_success'])
        self.assertFalse(result['constraint_conditions']['clean_before_serving'])
        self.assertEqual(result['public_constraint_violations'][0]['env_step'],10)

    def test_slow_transport_accumulates_before_motion_guard(self):
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        evaluator=TemporalGoalEvaluator(SPEC)
        values=dict.fromkeys(SPEC['predicates'],False)
        values.update(packed_at_source=True,closed=True,contents=True)
        evaluator.observe(values,0,env_step=0)
        position={'box':0.0}
        backend=SimpleNamespace(custom_tracker=evaluator,_custom_motion_positions=position.copy(),steps=1,
            _motion_positions=lambda:position.copy(),_predicate_values=lambda:values,
            torch=SimpleNamespace(linalg=SimpleNamespace(vector_norm=abs)))
        values['closed']=False
        position['box']=.008
        CustomTaskBackend._check_custom_motion(backend)
        self.assertEqual(evaluator.violations,[])
        position['box']=.016
        CustomTaskBackend._check_custom_motion(backend)
        self.assertEqual(evaluator.violations[0]['id'],'closed_loaded_transport')
        self.assertLess(evaluator.result(values)['goal_satisfaction_fraction'],1.0)

    def trajectory(self,clean_first=False,violate=False):
        evaluator=TemporalGoalEvaluator(SPEC);values=dict.fromkeys(SPEC['predicates'],False)
        evaluator.observe(values,0,env_step=0,initial=True)
        sequence=0
        for name in (['clean'] if clean_first else [])+['packed_at_source','delivered','unpacked','returned']+([] if clean_first else ['clean']):
            sequence+=1;values[name]=True
            if name=='packed_at_source': values.update(closed=True,contents=True)
            if name=='delivered':
                evaluator.check_motion({**values,'closed':not violate},{'box'},env_step=sequence)
            if name=='unpacked':values.update(contents=False,closed=False,packed_at_source=False,delivered=False)
            evaluator.observe(values,sequence,env_step=sequence)
        return evaluator,values

    def test_independent_branches_and_consumed_milestones(self):
        for clean_first in (False,True):
            evaluator,values=self.trajectory(clean_first)
            self.assertTrue(evaluator.result(values)['task_success'])
            self.assertFalse(values['delivered'])
            self.assertTrue(evaluator.result(values)['historical_conditions']['delivered'])

    def test_final_state_cannot_forge_missing_transport(self):
        evaluator=TemporalGoalEvaluator(SPEC);values=dict.fromkeys(SPEC['predicates'],False)
        values.update(unpacked=True,returned=True,clean=True)
        evaluator.observe(values,0,env_step=0,initial=True)
        evaluator.observe(values,1,env_step=1)
        result=evaluator.result(values)
        self.assertEqual(result['terminal_satisfaction_fraction'],1)
        self.assertFalse(result['task_success'])

    def test_closed_box_final_snapshot_does_not_erase_transport_violation(self):
        evaluator,values=self.trajectory(violate=True)
        values['closed']=True
        self.assertFalse(evaluator.result(values)['task_success'])
        self.assertEqual(len(evaluator.violations),1)

    def test_all_terminal_conditions_must_hold_together(self):
        evaluator,values=self.trajectory()
        values['clean']=False;evaluator.observe(values,10,env_step=50)
        self.assertFalse(evaluator.result(values)['task_success'])
        self.assertTrue(any(e['id']=='bench_clean' and e.get('satisfied') is False for e in evaluator.events))

    def test_reject_cycles_unknown_predicates_and_invalid_state_values(self):
        spec=copy.deepcopy(SPEC);spec['milestones']['packed']['after']=['returned']
        with self.assertRaises(ValueError):TemporalGoalEvaluator(spec)
        spec=copy.deepcopy(SPEC);spec['terminal']['unknown']='nonexistent'
        with self.assertRaises(KeyError):TemporalGoalEvaluator(spec)
        evaluator,values=self.trajectory();values['clean']=None
        with self.assertRaises(ValueError):evaluator.result(values)
