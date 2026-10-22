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
                                  peers, host_budget_gib=56, candidate_gib=28, startup_gib=16)

    def peer(self, current=8, peak=8, stage='ready'):
        return {'runtime_stage': stage, 'working_bytes': current * GIB,
                'working_peak_bytes': peak * GIB, 'peak_bytes': 27 * GIB, 'hard_limit_gib': 28,
                'memory_admission_mode': 'observed_peak'}

    def test_cached_total_peak_does_not_block_s118_second_instance(self):
        result = self.decide([self.peer(current=9, peak=9)], working=10, cache=34)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['projected_bytes'], 29 * GIB)
        self.assertEqual(result['candidate_hard_limit_bytes'], 28 * GIB)

    def test_s115_third_instance_uses_startup_estimate(self):
        result = self.decide([self.peer(), self.peer()], working=17, cache=45)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['projected_bytes'], 41 * GIB)

    def test_genuine_working_peak_remains_reserved_after_usage_falls(self):
        result = self.decide([self.peer(8, 24), self.peer(8, 24)], working=17)
        self.assertEqual(result['status'], 'blocked')

    def test_missing_or_starting_telemetry_does_not_gain_measured_discount(self):
        self.assertEqual(self.decide([self.peer(stage='starting')]*2, 17)['status'], 'blocked')
        self.assertEqual(self.decide([{}, {}], 17)['status'], 'blocked')
        unknown = self.peer(); unknown.pop('working_peak_bytes')
        self.assertEqual(self.decide([unknown]*2, 17)['status'], 'blocked')

    def test_legacy_peers_keep_full_reservation_even_with_live_telemetry(self):
        for mode in (None, 'full_reservation'):
            with self.subTest(mode=mode):
                peer = self.peer()
                if mode is None:
                    peer.pop('memory_admission_mode')
                else:
                    peer['memory_admission_mode'] = mode
                result = self.decide([peer, peer], working=17)
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual([p['reserved_bytes'] for p in result['peers']], [28*GIB]*2)

    def test_omitted_startup_estimate_keeps_full_candidate_reservation(self):
        result = admission_decision(0, {}, 64*GIB, [], host_budget_gib=56)
        self.assertEqual(result['candidate_reserve_bytes'], 28*GIB)

    def test_unrelated_account_memory_is_counted_once(self):
        result = self.decide([self.peer(), self.peer()], working=40)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['projected_bytes'], 64 * GIB)

    def test_account_hard_limit_and_single_margin_win_over_config(self):
        result = admission_decision(47 * GIB, {}, 64 * GIB, [],
                                   host_budget_gib=112, candidate_gib=28, startup_gib=16)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['usable_budget_bytes'], 62 * GIB)

    def test_startup_estimate_cannot_exceed_enforcement_limit(self):
        with self.assertRaises(ValueError):
            admission_decision(0, {}, 64 * GIB, [], host_budget_gib=64,
                               candidate_gib=24, startup_gib=28)

    def test_throughput_admits_alongside_a_legacy_initialization(self):
        peers = [self.peer(15, 15), self.peer(14, 14, stage='starting')]
        for peer in peers:
            peer['memory_admission_mode'] = 'full_reservation'
        result = admission_decision(30*GIB, {}, 64*GIB, peers,
                                   host_budget_gib=None, startup_gib=12, profile='throughput')
        self.assertEqual(result['status'], 'passed')
        self.assertLess(result['projected_bytes'], 48*GIB)
        self.assertEqual(result['usable_budget_bytes'], 62*GIB)

    def test_concurrent_starts_reserve_future_growth_before_readiness(self):
        peers = [self.peer(1, 1, stage='starting') for _ in range(3)]
        for peer in peers:
            peer['startup_memory_gib'] = 12
        result = admission_decision(4*GIB, {}, 64*GIB, peers,
                                   host_budget_gib=None, startup_gib=12, profile='throughput')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['projected_bytes'], 49*GIB)
        self.assertEqual([p['reserved_bytes'] for p in result['peers']], [12*GIB]*3)

    def test_throughput_blocks_real_growth_and_unknown_telemetry(self):
        for peers, working in [([self.peer(25, 25)]*2, 51), ([{}, {}], 0)]:
            with self.subTest(working=working):
                result = admission_decision(working*GIB, {}, 64*GIB, peers,
                    host_budget_gib=None, startup_gib=12, profile='throughput')
                self.assertEqual(result['status'], 'blocked')

    def test_throughput_gate_serializes_admission_without_waiting_on_legacy_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            def runner(profile):
                b = batch.Batch.__new__(batch.Batch)
                b.root = Path(tmp)
                b._config = {'adaptive_memory_admission': True, 'shared_lease_dir': tmp,
                             'memory_admission_profile': profile}
                b._worker_local = threading.local()
                b._worker_local.config = {'id': 'a', 'ssh': ['ssh', 'host']}
                b.worker_state = Mock()
                return b
            old, a, b = runner('observed_peak'), runner('throughput'), runner('throughput')
            try:
                self.assertTrue(old.acquire_startup_gate())
                self.assertTrue(a.acquire_startup_gate())
                self.assertFalse(b.acquire_startup_gate())
                # A published startup can still be initializing when B admits.
                a.release_startup_gate()
                self.assertTrue(b.acquire_startup_gate())
            finally:
                for runner in (old, a, b):runner.release_startup_gate()

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
