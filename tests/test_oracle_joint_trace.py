"""CPU checks for passive measurement and its failure evidence, not simulation."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np

from manipulation_agent.executors.oracle_joint_trace import task_joint_handles, read_joint_positions
from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend


class Position:
    def __init__(self, value): self.value = value
    def flatten(self): return [self.value]


class Joint:
    articulated = True
    def __init__(self, value): self.value = value
    def get_state(self): return Position(self.value), None, None


class JointTraceTests(unittest.TestCase):
    def test_batched_positions_use_dof_offsets_refresh_each_tick_and_preserve_gaps(self):
        class BatchJoint:
            articulated = True
            def __init__(self, *indices):
                self.dof_indices = list(indices)
            def get_state(self):
                raise AssertionError("Position-only logging must not read velocity or effort")

        class Positions:
            def __init__(self, values): self.values = values
            def detach(self): return self
            def cpu(self): return self
            def tolist(self): return list(self.values)

        values = [1., 2., .6, .8, float('nan')]
        owner = SimpleNamespace(name='robot', joints={
            'last': BatchJoint(3), 'multi': BatchJoint(0, 1),
            'hinge': BatchJoint(2), 'bad': BatchJoint(4), 'missing': BatchJoint(9)},
            get_joint_positions=Mock(side_effect=lambda: Positions(values)))
        handles = task_joint_handles({'robot': owner, 'alias': owner})
        first = read_joint_positions(handles)['robot']
        self.assertEqual([first[k]['position'] for k in ('last', 'multi', 'hinge')], [.8, 1., .6])
        self.assertFalse(first['bad']['available'])
        self.assertFalse(first['missing']['available'])
        self.assertEqual(owner.get_joint_positions.call_count, 1)
        values[2] = .4
        self.assertEqual(read_joint_positions(handles)['robot']['hinge']['position'], .4)
        self.assertEqual(owner.get_joint_positions.call_count, 2)
        owner.get_joint_positions.side_effect = RuntimeError('lost articulation')
        self.assertTrue(all(not row['available'] for row in read_joint_positions(handles)['robot'].values()))
        # A same-tick measured snapshot is reusable even if the read API fails.
        cached = read_joint_positions(handles, snapshots={'robot': values})['robot']
        self.assertEqual(cached['hinge']['position'], .4)
        self.assertEqual(owner.get_joint_positions.call_count, 3)
        json.dumps(cached, allow_nan=False)

    def test_task_binding_aliases_and_fixed_objects(self):
        joint = Joint(.6)
        obj = SimpleNamespace(name="microwave", joints={"hinge": joint,
            "fixed": SimpleNamespace(articulated=False)})
        handles = task_joint_handles({"a": obj, "alias": obj, "missing": None})
        self.assertEqual(list(handles), ["microwave"])
        self.assertIs(handles["microwave"]["hinge"], joint)
        self.assertEqual(read_joint_positions(handles)["microwave"]["hinge"]["position"], .6)
        joint.value = .4
        self.assertEqual(read_joint_positions(handles)["microwave"]["hinge"]["position"], .4)

    def test_unavailable_and_nonfinite_values_are_explicit_serializable_gaps(self):
        handles = {"door": {"nan": Joint(float("nan")), "broken": object()}}
        result = read_joint_positions(handles)
        self.assertTrue(all(not r["available"] and r["position"] is None
                            for r in result["door"].values()))
        json.dumps(result, allow_nan=False)

    def test_motor_trace_continues_without_active_primitive(self):
        joint = Joint(.65)
        robot = SimpleNamespace(arm_names=[], get_position_orientation=lambda: (0, 0),
                                get_joint_positions=Mock(return_value=np.array([0., 0., 0.])))
        with tempfile.TemporaryDirectory() as directory:
            backend = SimpleNamespace(robot=robot, output=Path(directory), steps=10,
                _navigation_active=False, _trace_arm=None, _trace_waypoint=None,
                _trace_target=None, _primitive_context=None, ik_backend="omnigibson",
                _traced_object_joints={"microwave": {"hinge": joint}},
                _body_posture_state=lambda: {}, _indices=lambda group: np.array([0]),
                _list=lambda value: value.tolist(), _pose=lambda value: {})
            OracleWaypointBackend._record_motor_step(backend)
            backend.steps += 1
            joint.value = .2
            OracleWaypointBackend._record_motor_step(backend)
            rows = [json.loads(line) for line in (Path(directory)/"waypoint_trace.jsonl").read_text().splitlines()]
            backend._motor_trace_writer.close()
        self.assertEqual([r["sim_step"] for r in rows], [10, 11])
        self.assertEqual([r["object_joint_positions"]["microwave"]["hinge"]["position"] for r in rows], [.65, .2])
        self.assertTrue(all("primitive" not in row for row in rows))
        self.assertEqual(robot.get_joint_positions.call_count, 2)
