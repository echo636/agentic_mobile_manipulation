import copy
import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('goal_ablation_summary',Path(__file__).resolve().parents[1]/'scripts/goal_ablation_summary.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ComparisonTests(unittest.TestCase):
    def rows(self):
        rows=[{'index':i,'task':'task'+str(i),'instance':301,'seed':0,'instruction_sha256':'same','bddl_sha256':'bddl','status':'failed','task_success':None} for i in (0,50)]
        return {a:{'tasks':copy.deepcopy(rows)} for a in module.ARMS}

    def test_missing_score_is_unknown_and_short_phrase_cohort_is_separate(self):
        data=self.rows();data['hierarchical']['tasks'][0].update(status='passed',task_success=True)
        data['unguided']['tasks'][0]['task_success']=False
        report=module.summarize(data)
        self.assertEqual(report['paired']['scored'],1)
        self.assertEqual(report['paired']['hierarchical_only'],1)
        self.assertEqual(report['cohorts']['short_phrase_last50']['hierarchical']['unknown_score'],1)
        self.assertEqual(report['cohorts']['short_phrase_last50']['hierarchical']['successes'],0)

    def test_unmatched_instructions_fail_instead_of_comparing_different_tasks(self):
        data=self.rows();data['unguided']['tasks'][1]['instruction_sha256']='changed'
        with self.assertRaises(ValueError):module.summarize(data)

    def test_custom_task_pair_requires_same_private_spec_and_joint_initial_state(self):
        data=self.rows()
        for arm in module.ARMS:
            data[arm]['protocol']={'max_agent_steps':200}
            for row in data[arm]['tasks']:
                row.update(custom_task_spec='/private/spec.json',custom_task_spec_sha256='same-spec',
                           custom_instance_sha256='same-scene',instruction_source='custom_public_context',
                           task_success=False,custom_score=.4)
        result=module.summarize(data)
        self.assertTrue(result['custom_tasks'])
        self.assertEqual(set(result['cohorts']),{'all'})
        self.assertEqual(result['max_agent_steps'],200)
        self.assertEqual(result['tasks'][0]['arms']['hierarchical']['custom_score'],.4)
        data['unguided']['tasks'][0]['custom_instance_sha256']='different-scene'
        with self.assertRaises(ValueError):module.summarize(data)


if __name__=='__main__':unittest.main()
