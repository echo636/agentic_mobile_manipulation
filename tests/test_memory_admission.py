import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from manipulation_agent.memory_admission import GIB, admission_decision
from manipulation_agent.batch_lifecycle import WorkerLease

spec = importlib.util.spec_from_file_location('memory_batch', Path(__file__).resolve().parents[1] / 'scripts/run_behavior100.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class MemoryAdmissionTests(unittest.TestCase):
    def decide(self, peers, working, cache=0):
        return admission_decision((working + cache) * GIB, {'file': cache * GIB}, 64 * GIB,
                                  peers, host_budget_gib=56, candidate_gib=28)

    def peer(self, current=8, peak=8, stage='ready'):
        return {'runtime_stage': stage, 'working_bytes': current * GIB,
                'peak_bytes': peak * GIB, 'hard_limit_gib': 28}

    def test_third_light_instance_fits_despite_reclaimable_file_cache(self):
        result = self.decide([self.peer(), self.peer()], working=17, cache=30)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['projected_bytes'], 53 * GIB)

    def test_later_peak_and_legacy_peer_keep_full_required_headroom(self):
        self.assertEqual(self.decide([self.peer(8, 24), self.peer()], 17)['status'], 'blocked')
        self.assertEqual(self.decide([self.peer(stage='starting'), self.peer()], 17)['status'], 'blocked')
        self.assertEqual(self.decide([self.peer(stage=None), self.peer()], 17)['status'], 'blocked')

    def test_unrelated_account_memory_and_missing_telemetry_are_not_ignored(self):
        self.assertEqual(self.decide([self.peer(), self.peer()], 25)['status'], 'blocked')
        self.assertEqual(self.decide([{}, {}], 17)['status'], 'blocked')
        result = self.decide([self.peer(current=15), self.peer(current=15)], 17)
        self.assertEqual(result['status'], 'blocked')

    def test_account_hard_limit_wins_over_a_large_configured_budget(self):
        result = admission_decision(45 * GIB, {}, 64 * GIB, [], host_budget_gib=112)
        self.assertEqual(result['status'], 'blocked')

    def test_adaptive_slots_share_legacy_gpu_locks_and_peer_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            def worker(i):
                return {'id': str(i), 'gpu': i, 'ssh': ['ssh', 'host'], 'expected_gpu_uuid': f'gpu{i}'}
            first = WorkerLease(tmp, worker(0), host_budget_gib=56)
            second = WorkerLease(tmp, worker(1), host_budget_gib=56)
            third = WorkerLease(tmp, worker(2), host_budget_gib=56, adaptive_slot_count=4)
            held = [first, second, third]
            try:
                for lease in held:
                    self.assertTrue(lease.acquire())
                    lease.record(unit=f'{lease.worker["id"]}.service', released=False)
                self.assertEqual({p['unit'] for p in third.active_peers()}, {'0.service', '1.service'})
                duplicate = WorkerLease(tmp, worker(0), host_budget_gib=56, adaptive_slot_count=4)
                self.assertFalse(duplicate.acquire())
                first.release(cleared=True)
                self.assertEqual([p['unit'] for p in third.active_peers()], ['1.service'])
            finally:
                for lease in held:
                    lease.release(cleared=True)

    def test_independent_coordinators_cannot_initialize_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            def runner():
                b = batch.Batch.__new__(batch.Batch)
                b.root = Path(tmp)
                b._config = {'adaptive_memory_admission': True, 'shared_lease_dir': tmp}
                b._worker_local = threading.local()
                b._worker_local.config = {'id': 'a', 'ssh': ['ssh', 'host']}
                b.worker_state = Mock()
                return b
            a, b = runner(), runner()
            self.assertTrue(a.acquire_startup_gate())
            try:
                self.assertFalse(b.acquire_startup_gate())
            finally:
                a.release_startup_gate()
            self.assertTrue(b.acquire_startup_gate())
            b.release_startup_gate()


if __name__ == '__main__':
    unittest.main()
