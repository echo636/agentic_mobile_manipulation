"""Suppress robot/held-object self contact for the ideal symbolic grasp only."""


def filter_robot_contacts(robot, obj, sim):
    additions=[]
    with sim.editing_usd():
        for a in robot.links.values():
            for b in obj.links.values():
                for source,target in ((a,b),(b,a)):
                    relation=source._collision_filter_api.GetFilteredPairsRel()
                    if target.prim_path not in {str(p) for p in relation.GetTargets()}:
                        relation.AddTarget(target.prim_path)
                        additions.append((relation,target.prim_path))
    return additions


def restore_robot_contacts(additions, sim):
    with sim.editing_usd():
        for relation,path in additions:
            relation.RemoveTarget(path)
