"""Safe release of a supported child must not relax containment or stability."""
import types
import numpy as np
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.carry import ControlledCarry


class SupportedPayloadRelease(unittest.TestCase):
    def setUp(self):
        self.states = types.ModuleType('omnigibson.object_states')
        for name in ('Inside', 'OnTop', 'Touching', 'AttachedTo'):
            setattr(self.states, name, type(name, (), {}))
        self.module = patch.dict('sys.modules', {'omnigibson.object_states': self.states})
        self.module.start()
        self.addCleanup(self.module.stop)
        self.target = types.SimpleNamespace(name='selected_support')
        self.parent = types.SimpleNamespace(name='held_parent')
        self.child = types.SimpleNamespace(name='supported_child', states={})
        for body in (self.parent, self.child):
            body.get_position_orientation = lambda: (np.zeros(3), np.array([0., 0., 0., 1.]))
            body.get_linear_velocity = lambda: np.zeros(3)
        self.backend = ControlledCarry()
        self.backend.env = types.SimpleNamespace(scene=types.SimpleNamespace())
        self.records = []
        self.backend._placement_record = self.records.append
        self.supported = self.touching = True
        self.child.states = {
            self.states.Inside: types.SimpleNamespace(get_value=lambda other: False),
            self.states.OnTop: types.SimpleNamespace(get_value=lambda other: other is self.target and self.supported),
            self.states.Touching: types.SimpleNamespace(get_value=lambda other: other is self.target and self.touching)}

    def verify(self, kind='OnTop', *, stable=True, target=True):
        return self.backend._verify_payload([(self.child,self.parent,kind)],
            support_target=self.target if target else None,
            support_resting={id(self.child): {'stable':stable}})

    def test_stable_child_can_rest_on_the_same_selected_surface(self):
        self.verify()
        self.assertEqual(self.records[0]['status'],'payload_support_transferred')

    def test_contained_child_must_remain_inside_its_original_container(self):
        with self.assertRaises(SkillError):self.verify('Inside')
        self.assertEqual(self.records[-1]['status'],'payload_relation_failed')

    def test_falling_or_unsupported_child_still_rejects_placement(self):
        for stable,supported,touching in ((False,True,True),(True,False,True),(True,True,False)):
            with self.subTest(stable=stable,supported=supported,touching=touching):
                self.supported,self.touching=supported,touching
                with self.assertRaises(SkillError):self.verify(stable=stable)

    def test_other_operations_preserve_the_original_support_requirement(self):
        with self.assertRaises(SkillError):self.verify(target=False)


if __name__=='__main__':unittest.main()
