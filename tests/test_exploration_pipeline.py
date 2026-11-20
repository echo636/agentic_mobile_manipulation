import copy
import json
from pathlib import Path
import tempfile
import unittest
from manipulation_agent.oracle_presentation import OraclePresentation, specs
from manipulation_agent.exploration_workspace import freeze_project, safe_tree
from manipulation_agent.exploration_session import prepare_session, ExplorationSession
from manipulation_agent.exploration_runner import run_controller

TEST_ROOT = Path(tempfile.gettempdir()) / "mas-exploration-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
def packet(revision=0):
    def obj(ident, x):
        return {"id": ident, "category": "bottle", "task_names": [ident], "pose": {"position_m":[x,0,1]},
                "aabb":{"min_m":[x-.1,-.1,.9],"max_m":[x+.1,.1,1.1]},
                "links":[{"name":"base","mesh":"detail"}], "joints":[{"axis":[1,0,0]}]}
    return {"ok":True,"observation":{"revision":revision,"images":[],"capture":{"sim_step":10},
      "oracle_3d":{"frame":"robot_base","units":{"position":"m"},"objects":[obj("bottle",1),obj("bucket",2)],
       "robot":{"base_pose_world":{"position_m":[0,0,0]},
        "body_links":[{"full":"body"}],"trunk":{"joint_positions_rad":[0,0,0,.5]},
        "arms":{"right":{"eef_pose":{"position_m":[.2,.3,.7]},"held_object":None,
                         "finger_geometry":{"long":"detail"},"joint_positions_rad":[0]*7}}}}}}

class PresentationTests(unittest.TestCase):
    def test_default_has_index_but_no_guessed_target(self):
        p=OraclePresentation();full=packet();before=copy.deepcopy(full);out=p.packet(full)
        self.assertEqual(full,before)
        self.assertEqual(out["observation"]["oracle_3d"]["objects"],[])
        self.assertEqual(len(out["observation"]["oracle_3d"]["object_catalog"]),2)
        self.assertNotIn("body_links",out["observation"]["oracle_3d"]["robot"])
    def test_focus_returns_poses_and_no_extra_images(self):
        p=OraclePresentation();p.remember(packet())
        reply=p.query("set_oracle_focus",{"object_ids":["bottle"],"revision":0})
        self.assertNotIn("observation",reply)
        o=p.packet(packet(1))["observation"]["oracle_3d"]
        self.assertEqual(set(o["objects"][0]),{"id","category","task_names","pose"})
        self.assertEqual(o["objects"][0]["id"],"bottle")
    def test_query_exact_fields_no_simulation(self):
        p=OraclePresentation();p.remember(packet())
        q=p.query("get_oracle_geometry",{"object_ids":["bottle"],"object_fields":["joints"],"revision":0})
        self.assertEqual(set(q["objects"][0]),{"id","joints"})
        self.assertEqual(q["capture"]["sim_step"],10)
        self.assertNotIn("observation",q)
        self.assertEqual(p.snapshot,packet()["observation"])
    def test_full_geometry_on_demand(self):
        p=OraclePresentation();p.remember(packet())
        q=p.query("get_oracle_geometry",{"object_ids":[],"robot_sections":["body_links","arms"],"revision":0})
        self.assertIn("finger_geometry",q["robot"]["arms"]["right"])
        self.assertIn("body_links",q["robot"])
    def test_stale_rejected(self):
        p=OraclePresentation();p.remember(packet(3))
        with self.assertRaises(ValueError):p.query("set_oracle_focus",{"object_ids":["bottle"],"revision":2})
    def test_unknown_and_duplicate_rejected(self):
        p=OraclePresentation();p.remember(packet())
        for ids in (["missing"],["bottle","bottle"],["bottle"]*9):
            with self.assertRaises(ValueError):p.query("set_oracle_focus",{"object_ids":ids,"revision":0})
    def test_unknown_fields_rejected(self):
        p=OraclePresentation();p.remember(packet())
        with self.assertRaises(ValueError):
            p.query("get_oracle_geometry",{"object_ids":["bottle"],"object_fields":["private_state"],"revision":0})
    def test_full_mode_preserves_packet(self):
        x=packet();self.assertEqual(OraclePresentation(mode="full").packet(x),x)
    def test_held_object_stays_visible(self):
        x=packet();x["observation"]["oracle_3d"]["robot"]["arms"]["right"]["held_object"]="bottle"
        self.assertEqual(OraclePresentation().packet(x)["observation"]["oracle_3d"]["objects"][0]["id"],"bottle")
    def test_rgb_not_changed(self):
        x={"observation":{"images":[],"observation_mode":"rgb_only"}}
        self.assertEqual(OraclePresentation().packet(x),x)

TOOLS=[{"name":"initialize","inputSchema":{"type":"object","properties":{},"required":[]}},
       {"name":"execute_waypoints","inputSchema":{"type":"object","properties":{"revision":{"type":"integer"}},"required":["revision"]}},
       {"name":"finish","inputSchema":{"type":"object","properties":{},"required":[]}}]
class Episode:
    tools=TOOLS
    def __init__(self,key):self.key=key;self.calls=[];self.revision=0
    def call(self,name,args,ident):
        self.calls.append((name,args))
        if name=="finish":return {"ok":True,"closed":True}
        if name=="execute_waypoints":
            assert args["revision"]==self.revision
            self.revision+=1
        return packet(self.revision)
    def collect(self):return {"official_task_success":False,"key":self.key}
class Factory:
    def __init__(self):self.started=[]
    def start(self,spec,*,on_launch=None):
        ep=Episode(spec["key"]);self.started.append(ep);return ep
    def attach(self,active):raise RuntimeError("test restart cannot silently reset")
def fake_runner(bundle,digest,output,call,allowed):
    assert "finish" not in allowed
    call("execute_waypoints",{}, "program-call")
    return {"clean":True,"exit_code":0,"controller_result":{"ok":False},"unattended":True}
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_ROOT);self.root=Path(self.tmp.name)/"session"
        cfg={"task":"put bottles in bucket", "exploration":[{"key":"train1"},{"key":"train2"},{"key":"train3"}],
             "formal":[{"key":"heldout1"},{"key":"heldout2"}]}
        for index,entry in enumerate(cfg["exploration"]+cfg["formal"]):
            entry["identity"]={"task":"test_task","instance":index,"seed":0}
        prepare_session(self.root,cfg,TOOLS)
        self.factory=Factory();self.session=ExplorationSession(self.root,self.factory,runner=fake_runner)
        p=self.root/"workspace/code/task";p.mkdir()
        (p/"controller.py").write_text("def main(ctx):\n return {'ok':True}\n")
    def tearDown(self):self.session.close();self.tmp.cleanup()

    def test_isolated_controller_queries_cached_geometry_without_extra_robot_calls(self):
        self.session.dispatch("start_episode",{})
        self.session.runner=run_controller
        controller=self.root/'workspace/code/task/controller.py'
        controller.write_text('''def main(ctx):
 ctx.call('initialize')
 q=ctx.call('get_oracle_geometry',object_ids=['bottle'],object_fields=['aabb'],robot_sections=['trunk'])
 ctx.require(q['objects'][0]['aabb']['min_m'][0]==0.9,'wrong geometry')
 ctx.call('set_oracle_focus',object_ids=['bottle'])
 ctx.call('execute_waypoints')
 stale=False
 try:ctx.call('get_oracle_geometry',object_ids=['bottle'],revision=0)
 except RuntimeError as e:stale='Stale geometry revision' in str(e)
 latest=ctx.call('get_oracle_geometry',object_ids=['bottle'])
 return {'ok':stale and latest['revision']==1}
''')
        result=self.session.dispatch('run_development',{'project':'task'})['execution']
        self.assertTrue(result['clean'])
        self.assertTrue(result['controller_result']['ok'])
        self.assertEqual([n for n,a in self.factory.started[0].calls],
                         ['initialize','initialize','execute_waypoints'])
        self.assertEqual(sum(c['ok'] is False for c in result['calls']),1)
        self.assertEqual(result['calls'][-2]['error']['type'],'ValueError')
    def register(self):return self.session.dispatch("register",{"project":"task"})["bundle"]
    def test_complete_lifecycle_fresh_formal_without_task_claim(self):
        bundle=self.register()
        rehearsal=self.session.dispatch("rehearse",{"bundle":bundle})
        self.assertFalse(rehearsal["evaluation"]["official_task_success"])
        self.session.dispatch("submit",{"bundle":bundle})
        results=self.session.evaluate()
        self.assertEqual([e.key for e in self.factory.started],["train1","heldout1","heldout2"])
        self.assertEqual(self.session.state["phase"],"complete")
        self.assertTrue(all(not r["evaluation"]["official_task_success"] for r in results))
    def test_submit_without_rehearsal_fails(self):
        bundle=self.register()
        with self.assertRaises(ValueError):self.session.dispatch("submit",{"bundle":bundle})
    def test_active_episode_cannot_rehearse(self):
        bundle=self.register();self.session.dispatch("start_episode",{})
        with self.assertRaises(ValueError):self.session.dispatch("rehearse",{"bundle":bundle})
    def test_workspace_mutation_does_not_change_bundle(self):
        bundle=self.register()
        (self.root/"workspace/code/task/controller.py").write_text("def main(ctx):\n return {'ok':False}\n")
        self.assertIn("True",(self.root/"bundles"/bundle/"controller.py").read_text())
        self.assertNotEqual(bundle,self.register())
    def test_workspace_traversal_refused(self):
        for path in ("../config.json","/etc/passwd","code/../../state.json"):
            with self.assertRaises(ValueError):self.session.dispatch("read_workspace_file",{"path":path})
    def test_config_and_api_not_writable(self):
        for path in ("api/runtime.py","AGENTS.md","TASK.md"):
            with self.assertRaises(ValueError):self.session.dispatch("write_workspace_file",{"path":path,"content":"bad"})
    def test_write_code_then_register(self):
        self.session.dispatch("write_workspace_file",{"path":"code/new/controller.py",
            "content":"def main(ctx):\n return {'ok':True}\n"})
        self.assertTrue(self.session.dispatch("register",{"project":"new"})["bundle"])
    def test_symlink_bundle_rejected(self):
        (self.root/"workspace/code/task/escape").symlink_to("/etc/passwd")
        with self.assertRaises(ValueError):self.register()
    def test_duplicate_layout_rejected(self):
        with self.assertRaises(ValueError):
            prepare_session(self.root.parent/"bad",{"task":"x","exploration":[{"key":"same"}],"formal":[{"key":"same"}]},TOOLS)
    def test_same_instance_different_seed_is_not_heldout(self):
        cfg={"task":"x","exploration":[{"key":"a","identity":{"task":"x","instance":1,"seed":0}}],
             "formal":[{"key":"b","identity":{"task":"x","instance":1,"seed":1}}]}
        with self.assertRaises(ValueError):prepare_session(self.root.parent/"leak",cfg,TOOLS)
    def test_config_is_immutable(self):
        self.session.close()
        cfg=json.loads((self.root/"config.json").read_text());cfg["task"]="tampered"
        (self.root/"config.json").write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):ExplorationSession(self.root,self.factory)
        # Restore ownership for tearDown.
        state=json.loads((self.root/"state.json").read_text())
        import hashlib
        state["config_sha256"]=hashlib.sha256((self.root/"config.json").read_bytes()).hexdigest()
        (self.root/"state.json").write_text(json.dumps(state))
        self.session=ExplorationSession(self.root,self.factory)
    def test_no_agent_action_after_submit(self):
        b=self.register();self.session.dispatch("rehearse",{"bundle":b});self.session.dispatch("submit",{"bundle":b})
        with self.assertRaises(ValueError):self.session.dispatch("start_episode",{})
    def test_two_owners_refused(self):
        with self.assertRaises(BlockingIOError):ExplorationSession(self.root,self.factory)
    def test_development_not_rehearsal(self):
        self.session.dispatch("start_episode",{})
        self.session.dispatch("run_development",{"project":"task"})
        self.assertFalse(self.session.state["rehearsals"])
        with self.assertRaises(ValueError):self.session.dispatch("submit",{"bundle":self.register()})
    def test_current_revision_injected_for_code(self):
        self.session.dispatch("start_episode",{})
        self.session.call_robot("execute_waypoints",{},"a")
        self.session.call_robot("execute_waypoints",{},"b")
        self.assertEqual(self.factory.started[0].calls[-1][1]["revision"],1)
    def test_no_unsandboxed_fallback(self):
        b=self.register()
        with self.assertRaises(ValueError):
            run_controller(self.root/"bundles"/b,self.session.state["bundles"][b]["code_sha256"],self.root/"forbidden",lambda *x:{},set(),sandbox=False)

class RealSandboxTests(SessionTests):
    def setUp(self):
        super().setUp()
        self.session.runner = run_controller
        (self.root/"workspace/code/task/controller.py").write_text(
            "from pathlib import Path\ndef main(ctx):\n"
            " assert not Path('/home/test-user/.codex/auth.json').exists()\n"
            " assert not Path('/dev/nvidia0').exists()\n"
            " try:\n  Path('/bundle/controller.py').write_text('tamper')\n"
            " except OSError:\n  pass\n"
            " else:\n  raise RuntimeError('bundle was writable')\n"
            " ctx.call('initialize')\n ctx.call('execute_waypoints')\n return {'ok':True}\n")
    def test_actual_offline_bundle_to_fake_bridge(self):
        b=self.register()
        record=self.session.dispatch("rehearse",{"bundle":b})
        if not record["execution"]["clean"]:
            logs=self.root/"runs"/record["run_id"]
            self.fail((logs/"stderr.log").read_text())
        self.assertEqual(record["execution"]["exit_code"],0)
        self.assertTrue(record["execution"]["controller_result"]["ok"])
        self.assertFalse(record["evaluation"]["official_task_success"])


class MCPPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_catalog_projection_and_query_without_rpc(self):
        from unittest.mock import patch
        from manipulation_agent.mcp_server import create_server
        calls=[]
        def fake_rpc(url,name,arguments,ident,**kwargs):
            calls.append(name)
            return packet()
        with patch("manipulation_agent.mcp_server.fetch",return_value=json.dumps({"ready":True,"tools":TOOLS}).encode()), \
             patch("manipulation_agent.mcp_server.rpc",side_effect=fake_rpc):
            server=await create_server("http://127.0.0.1:1")
            found={t.name for t in await server.list_tools()}
            self.assertIn("get_oracle_geometry",found)
            result=await server._tool_manager.call_tool("initialize",{})
            reply=json.loads(result[0].text)
            self.assertEqual(reply["observation"]["oracle_3d"]["objects"],[])
            geometry=await server._tool_manager.call_tool("get_oracle_geometry",{
                "object_ids":["bottle"],"revision":0,"object_fields":["pose"]})
            self.assertEqual(json.loads(geometry[0].text)["objects"][0]["id"],"bottle")
            self.assertEqual(calls,["initialize"])
    async def test_full_mode_catalog_stays_legacy(self):
        from unittest.mock import patch
        from manipulation_agent.mcp_server import create_server
        with patch("manipulation_agent.mcp_server.fetch",return_value=json.dumps({"ready":True,"tools":TOOLS}).encode()):
            server=await create_server("http://127.0.0.1:1",oracle_payload="full")
            self.assertEqual({t.name for t in await server.list_tools()},{t["name"] for t in TOOLS})


class AgentResumeTests(unittest.TestCase):
    def test_failed_provider_is_not_retried_and_resume_reuses_native_storage(self):
        import importlib.util
        from types import SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.clients.types import PreparedProject
        repo=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location("exploration_launcher",repo/"scripts/run_exploration_pipeline.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root=Path(temp)/"session"
            cfg={"task":"fixture","model":"gpt-6-astra","reasoning_effort":"xhigh",
                 "source_snapshot":str(repo),"robot_tools":TOOLS,
                 "exploration":[{"key":"a","identity":{"task":"x","instance":0,"seed":0}}],
                 "formal":[{"key":"b","identity":{"task":"x","instance":1,"seed":0}}]}
            prepare_session(root,cfg,TOOLS)
            class Adapter:
                prepared=0
                calls=0
                original=None
                def prepare_project(self,config,instructions,names):
                    self.prepared+=1
                    native=config.output/"native_sessions";native.mkdir()
                    self.original=native
                    return PreparedProject(["codex","exec","-"],None,"fixture",native_sessions=native)
                def prepare_resume(self,project,thread,prompt):
                    assert project.native_sessions==self.original
                    assert thread=="fixture-thread"
                    return project
                def run(self,project,output,timeout,*,on_event=None):
                    self.calls+=1
                    (output/"model_events.jsonl").write_text(json.dumps({'type':'thread.started','thread_id':'fixture-thread'})+'\n')
                    return SimpleNamespace(returncode=1)
                def parse(self,path):
                    return SimpleNamespace(raw_events=[{"type":"thread.started","thread_id":"fixture-thread"}])
            adapter=Adapter()
            with patch.object(module,"get_adapter",return_value=adapter),patch.object(module,"check_server",return_value={}):
                self.assertEqual(module.main(["run","--session",str(root),"--provider","myapi"]),1)
                self.assertEqual(adapter.calls,1)
                self.assertEqual(module.main(["run","--session",str(root),"--provider","myapi","--resume"]),1)
            self.assertEqual(adapter.prepared,1)
            self.assertEqual(adapter.calls,2)
            self.assertEqual(json.loads((root/"agent.json").read_text())["status"],"paused")

if __name__=="__main__":unittest.main()
