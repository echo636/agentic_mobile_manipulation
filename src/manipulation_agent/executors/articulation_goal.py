"""Construct an opening from native joint metadata without previewing the scene."""


def native_open_goal(backend, obj, *, feedback=None):
    from omnigibson.object_states import Open
    from omnigibson.object_states.open_state import _compute_joint_threshold

    both_sides, joints, directions = obj.states[Open].relevant_joints_info
    binding = getattr(getattr(backend, '_local_operation', None), 'binding', None)
    anchor = getattr(binding, 'anchor', None) if getattr(binding, 'obj', None) is obj else None
    link_name = next((name for name, link in obj.links.items() if link is anchor), None)
    ancestors = set()
    # Native topology includes fixed handles/glass as well as moving leaves.
    # Read their joint ancestry instead of moving the object to discover it.
    while link_name is not None and link_name != obj.root_link_name:
        parent = next(iter(obj.articulation_tree.predecessors(link_name)))
        ancestors.add(obj.articulation_tree[parent][link_name]['joint_name'])
        link_name = parent
    selected = [joint for name, joint in obj.joints.items() if name in ancestors]
    controlled = [(joint, direction) for joint, direction in zip(joints, directions)
                  if joint in selected]
    if not controlled:
        controlled = list(zip(joints, directions))

    goal = obj.get_joint_positions().clone()
    for joint, direction in controlled:
        _, opened, closed = _compute_joint_threshold(joint, direction)
        # A native both-sides object is closed at either extreme and open
        # between them. One-sided doors/drawers use their authored open limit.
        goal[joint.dof_indices] = (opened + closed) / 2 if both_sides else opened
    if feedback is not None:
        feedback['other_panels_not_operated'] = len(controlled) < len(joints)
    backend._placement_record(dict(status='articulation_open_endpoint_selected', target=obj.name,
        goal_joints=goal.cpu().tolist(), opening_policy='native_joint_metadata',
        selected_link=str(anchor.prim_path) if selected else None,
        selected_joint_indices=[index for joint, _ in controlled for index in joint.dof_indices],
        preview_performed=False))
    return goal
