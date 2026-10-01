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

class V7Contracts(unittest.TestCase):
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
