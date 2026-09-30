"""Suppress robot/held-object self contact for the ideal symbolic grasp only."""


def filter_robot_contacts(robot, obj, sim):
    additions=[]
    # API creation opens its own editing_usd context in OG 3.9.2. Resolve
    # all APIs before the single relationship-editing block (no nesting).
    robot_links=[(a,a._collision_filter_api.GetFilteredPairsRel()) for a in robot.links.values()]
    object_links=[(b,b._collision_filter_api.GetFilteredPairsRel()) for b in obj.links.values()]
    with sim.editing_usd():
        for a,ar in robot_links:
            for b,br in object_links:
                for relation,target in ((ar,b),(br,a)):
                    if target.prim_path not in {str(p) for p in relation.GetTargets()}:
                        relation.AddTarget(target.prim_path)
                        additions.append((relation,target.prim_path))
    return additions


def restore_robot_contacts(additions, sim):
    with sim.editing_usd():
        for relation,path in additions:
            relation.RemoveTarget(path)
