"""CPU generator tests; these do not validate real robot settling."""
from types import SimpleNamespace
import unittest

from manipulation_agent.executors.symbolic_compat import trace_native_settling, native_planning_checkpoints


class NativeSettlingTraceTests(unittest.TestCase):
    def test_delegates_actions_send_throw_return_and_restores_instance(self):
        events=[]
        closed=[]
        def native():
            try:
                value=yield 'first'
                try:
                    yield value
                except ValueError:
                    yield 'handled_by_native'
                return 'native_result'
            finally:
                closed.append(True)
        primitive=SimpleNamespace(_settle_robot=native)
        with trace_native_settling(primitive,lambda *x:events.append(x),interval=2):
            generator=primitive._settle_robot()
            self.assertEqual(next(generator),'first')
            self.assertEqual(generator.send('sent'),'sent')
            self.assertEqual(generator.throw(ValueError('injected')),'handled_by_native')
            with self.assertRaises(StopIteration) as exc:next(generator)
            self.assertEqual(exc.exception.value,'native_result')
        self.assertEqual(events,[('started',0),('yielded',2),('returned',3)])
        self.assertEqual(closed,[True])
        self.assertIs(primitive._settle_robot,native)

    def test_close_forwards_to_native_and_restores_class_method(self):
        events=[]
        closed=[]
        class Primitive:
            def _settle_robot(self):
                try:
                    yield 1
                    yield 2
                finally:closed.append(True)
        primitive=Primitive()
        original=primitive._settle_robot.__func__
        with trace_native_settling(primitive,lambda *x:events.append(x)):
            generator=primitive._settle_robot()
            next(generator)
            generator.close()
        self.assertEqual(closed,[True])
        self.assertEqual(events,[('started',0),('closed',1)])
        self.assertNotIn('_settle_robot',vars(primitive))
        self.assertIs(primitive._settle_robot.__func__,original)

    def test_native_failure_wins_over_recording_failure(self):
        def native():
            yield 1
            raise ValueError('native failure')
        def broken_record(*args):raise OSError('diagnostic unavailable')
        primitive=SimpleNamespace(_settle_robot=native)
        with self.assertRaisesRegex(ValueError,'native failure'):
            with trace_native_settling(primitive,broken_record):
                list(primitive._settle_robot())
        self.assertIs(primitive._settle_robot,native)


class NativePlanningTraceTests(unittest.TestCase):
    def test_private_boolean_results_are_recorded_without_replacing_native_result(self):
        mask=[False,True,False]
        validate=lambda *args,**kwargs:mask
        reachable=lambda *args,**kwargs:False
        update=lambda:None
        primitive=SimpleNamespace(_validate_poses=validate,_target_in_reach_of_robot=reachable,
                                  _motion_generator=SimpleNamespace(update_obstacles=update))
        events=[]
        with native_planning_checkpoints(primitive,lambda:None,lambda *x:events.append(x)):
            self.assertIs(primitive._validate_poses('candidate_batch'),mask)
            self.assertFalse(primitive._target_in_reach_of_robot('target'))
            self.assertIsNone(primitive._motion_generator.update_obstacles())
        summaries={name:summary for name,status,_,summary in events if status=='returned'}
        self.assertEqual(summaries['_validate_poses'],
                         {'valid_mask':[False,True,False],'candidate_count':3,'valid_count':1})
        self.assertEqual(summaries['_target_in_reach_of_robot'],{'reachable':False})
        self.assertIsNone(summaries['update_obstacles'])
        self.assertIs(primitive._validate_poses,validate)
        self.assertIs(primitive._target_in_reach_of_robot,reachable)
        self.assertIs(primitive._motion_generator.update_obstacles,update)


if __name__=='__main__':unittest.main()
