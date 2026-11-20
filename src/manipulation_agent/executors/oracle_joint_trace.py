"""Passive task-articulation measurements for independent offline review."""
import math


class JointHandles(dict):
    """Keep the owning articulation for a single batched position read."""
    def __init__(self, owner, joints):
        super().__init__(joints)
        self.owner = owner


def task_joint_handles(object_scope):
    """Keep native joint handles, without issuing any simulator command."""
    result = {}
    for obj in object_scope.values():
        if obj is None or obj.name in result:
            continue
        joints = {name: joint for name, joint in getattr(obj, "joints", {}).items()
                  if getattr(joint, "articulated", False)}
        if joints:
            result[obj.name] = JointHandles(obj, joints)
    return result


def read_joint_positions(handles, *, snapshots=None):
    """Measure every registered joint; explicit gaps must fail strict review."""
    result = {}
    for object_id, joints in handles.items():
        owner = getattr(joints, "owner", None)
        positions, read_error = None, None
        try:
            if snapshots is not None and object_id in snapshots:
                positions = snapshots[object_id]
            elif owner is not None and hasattr(owner, "get_joint_positions"):
                positions = owner.get_joint_positions().detach().cpu().tolist()
        except (AssertionError, KeyError, RuntimeError, ValueError, AttributeError,
                TypeError, IndexError) as exc:
            read_error = exc
        result[object_id] = {}
        for joint_id, joint in joints.items():
            try:
                if read_error is not None:
                    raise read_error
                if positions is None:
                    # Compatibility for callers with legacy plain handle maps.
                    position, _, _ = joint.get_state()
                    value = float(position.flatten()[0])
                else:
                    # JointPrim.get_state used the first DOF as well. Public
                    # dof_indices preserves that mapping for multi-DOF joints.
                    value = float(positions[joint.dof_indices[0]])
                if not math.isfinite(value):
                    raise ValueError("Non-finite joint position")
                row = {"available": True, "position": value}
            except (AssertionError, KeyError, RuntimeError, ValueError, AttributeError,
                    TypeError, IndexError) as exc:
                row = {"available": False, "position": None, "reason": type(exc).__name__}
            result[object_id][joint_id] = row
    return result
