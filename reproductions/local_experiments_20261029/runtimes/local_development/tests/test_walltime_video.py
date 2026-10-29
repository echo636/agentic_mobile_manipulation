import unittest
from manipulation_agent.walltime_video import timeline

class WalltimeTests(unittest.TestCase):
    def fixture(self):
        controller={'started_at':'2026-09-30T10:00:00+00:00','finished_at':'2026-09-30T10:00:20+00:00','duration_seconds':20}
        events=[{'kind':'tool_call','at':'2026-09-30T10:00:05+00:00','request_id':'x','name':'act','arguments':{'primitive':'navigate_to'}},
                {'kind':'tool_result','at':'2026-09-30T10:00:15+00:00','request_id':'x'}]
        frames=[{'kind':'observation_boundary'},*({'kind':'env_step','request_id':'x'} for _ in range(2)),{'kind':'observation_boundary'}]
        captures=[{'synchronized_capture':{'captured_at':s}} for s in ('2026-09-30T09:59:59+00:00','2026-09-30T10:00:14+00:00')]
        return events,frames,captures,controller

    def test_real_wait_is_retained_and_control_frames_only_retimed(self):
        out=timeline(*self.fixture())
        self.assertEqual(out['duration_seconds'],20)
        self.assertEqual(out['synchronous_tool_seconds'],10)
        self.assertEqual(out['outside_tool_seconds'],10)
        self.assertEqual(out['source_frame_wall_seconds'],[0,8,11,14])
        self.assertEqual(out['segments'][0]['step'],None) # No future model decision shown early.
        self.assertEqual(out['segments'][-1]['phase'],'outside_tool_wait')
        self.assertIn('estimated',out['frame_timing'])

    def test_missing_results_cannot_be_fabricated(self):
        es,fs,cs,ctl=self.fixture()
        with self.assertRaisesRegex(ValueError,'no end timestamp'):timeline(es[:1],fs,cs,ctl)

    def test_bad_cross_host_time_order_rejected(self):
        es,fs,cs,ctl=self.fixture();es[1]['at']='2026-09-30T10:00:04+00:00'
        with self.assertRaisesRegex(ValueError,'Clock alignment'):timeline(es,fs,cs,ctl)

if __name__=='__main__':unittest.main()
