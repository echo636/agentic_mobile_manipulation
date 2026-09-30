from contextlib import nullcontext
from types import SimpleNamespace as NS
import unittest
from manipulation_agent.executors.grasp_contacts import filter_robot_contacts, restore_robot_contacts

class Relation:
    def __init__(self, targets=()): self.targets=set(targets)
    def GetTargets(self): return self.targets
    def AddTarget(self, value): self.targets.add(value)
    def RemoveTarget(self, value): self.targets.remove(value)

class ContactTests(unittest.TestCase):
    def test_release_preserves_preexisting_and_world_filters(self):
        robot_rel=Relation(['/held','/world']); held_rel=Relation(['/unrelated'])
        def link(path, rel):
            return NS(prim_path=path,_collision_filter_api=NS(GetFilteredPairsRel=lambda:rel))
        robot=NS(links={'r':link('/robot',robot_rel)})
        held=NS(links={'h':link('/held',held_rel)})
        sim=NS(editing_usd=nullcontext)
        added=filter_robot_contacts(robot,held,sim)
        self.assertEqual(held_rel.targets,{'/unrelated','/robot'})
        self.assertEqual(len(added),1)
        restore_robot_contacts(added,sim)
        self.assertEqual(robot_rel.targets,{'/held','/world'})
        self.assertEqual(held_rel.targets,{'/unrelated'})
