import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('audit',Path(__file__).resolve().parents[1]/'scripts/validate_async_observation.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


class ObservationAuditScopeTests(unittest.TestCase):
    def test_sync_episode_does_not_claim_async_validation(self):
        checks,scope=audit.async_checks([{'kind':'tool_call','name':'observe'}],[],require_async=False)
        self.assertEqual(checks,{})
        self.assertEqual(scope,'not_exercised_sync_only')

    def test_explicit_probe_still_requires_real_async_evidence(self):
        checks,_=audit.async_checks([],[],require_async=True)
        self.assertFalse(all(checks.values()))

    def test_missing_job_after_submission_is_not_sync_only(self):
        checks,_=audit.async_checks([{'kind':'tool_call','name':'start_observation'}],[],require_async=False)
        self.assertFalse(all(checks.values()))

    def test_async_ordering_and_completion_remain_required(self):
        jobs=[{'job_id':'j','status':'passed'}]
        ack={'kind':'tool_result','name':'start_observation','result':{'job':{'job_id':'j'}}}
        started={'kind':'observation_started','job':{'job_id':'j'}}
        self.assertTrue(all(audit.async_checks([ack,started],jobs,False)[0].values()))
        self.assertFalse(all(audit.async_checks([started,ack],jobs,False)[0].values()))


if __name__=='__main__':unittest.main()
