import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.gt_navigation import GridMap,plan_navigation,NavigationError
from manipulation_agent.mcp_preflight import check_server
from manipulation_agent.tools.action import act
from manipulation_agent.contracts import SkillError
from manipulation_agent.omnigibson_backend import restore_static_floor_geometry

class V7Contracts(unittest.TestCase):
    def test_restore_only_missing_fixed_floors_without_goal_or_instance_changes(self):
        import copy
        data={'metadata':{'task':{'inst_to_name':{'goal':'cup'}}},'objects_info':{'init_info':{'cup':{'args':{'category':'cup'}}}},'state':{'registry':{'object_registry':{'cup':{'pose':'original'}}}}}
        full={'objects_info':{'init_info':{'floor':{'args':{'category':'floors','fixed_base':True}},'extra_cup':{'args':{'category':'cup','fixed_base':True}}}},'state':{'registry':{'object_registry':{'floor':{'pose':'floor pose'},'extra_cup':{'pose':'not copied'}}}}}
        original=copy.deepcopy(full)
        self.assertEqual(restore_static_floor_geometry(data,full),['floor'])
        self.assertEqual(data['metadata']['task']['inst_to_name'],{'goal':'cup'})
        self.assertNotIn('extra_cup',data['objects_info']['init_info'])
        self.assertEqual(data['state']['registry']['object_registry']['cup'],{'pose':'original'})
        self.assertEqual(restore_static_floor_geometry(data,full),[]);self.assertEqual(full,original)

    def test_approach_filter_excludes_inaccessible_side(self):
        grid=GridMap(60,60,.1,(-3,-3),bytes([1])*3600)
        plan=plan_navigation(grid,(0,0),(1,0),candidate_filter=lambda xy:xy[0]<.7)
        self.assertLess(plan.goal[0],.7)
        with self.assertRaises(NavigationError):plan_navigation(grid,(0,0),(1,0),candidate_filter=lambda xy:False)

    def test_invalid_action_options_do_not_move_robot(self):
        calls=[];ctx=SimpleNamespace(perform=lambda *a,**kw:calls.append((a,kw)))
        for primitive,options in [('grasp',{'wait_seconds':2}),('open',{'placement_yaw_degrees':90})]:
            with self.assertRaises(SkillError):act(ctx,primitive,None,0,**options)
        self.assertEqual(calls,[])
        act(ctx,'wait',None,0,wait_seconds=12)
        self.assertEqual(calls[-1][1],{'seconds':12})

    def test_placement_rollback_restores_controlled_carry_ownership(self):
        obj=object();contents=[(object(),'relative child')];records=[];restored=[];follow=[]
        b=ControlledCarry();b._ideal_held=obj;b._carry_relative='relative';b._carry_contents=contents
        from contextlib import nullcontext
        b._anchored_operation=nullcontext
        b.og=SimpleNamespace(sim=SimpleNamespace(dump_state=lambda **k:'world',load_state=lambda s,**k:restored.append(s)))
        b._placement_record=records.append;b._carry_follow=lambda:follow.append(b._ideal_held)
        with self.assertRaises(SkillError),b._placement_context():
            b._ideal_held=None;b._carry_relative=None;b._carry_contents=[]
            raise SkillError('sampling_error','no pose')
        self.assertIs(b._ideal_held,obj);self.assertEqual(b._carry_contents,contents);self.assertEqual(restored,['world']);self.assertEqual(follow,[obj])

    def test_readonly_mcp_handshake_and_schema_mismatch(self):
        script='''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m:continue
 result={'protocolVersion':'2024-11-05'} if m['method']=='initialize' else {'tools':[{'name':'observe','inputSchema':{'type':'object'}}]}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
'''
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);result=check_server(sys.executable,['-u','-c',script],[{'name':'observe','inputSchema':{'type':'object'}}],out,timeout=5)
            self.assertEqual(result['status'],'passed');self.assertEqual(result['tool_calls'],0)
            with self.assertRaises(RuntimeError):check_server(sys.executable,['-u','-c',script],[{'name':'act','inputSchema':{}}],out,timeout=5)
            self.assertEqual(json.loads((out/'mcp_preflight.json').read_text())['status'],'failed')
