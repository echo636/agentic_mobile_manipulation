"""Fault regressions: no model, GPU or simulator; children are CPU fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.clients.base import run_process
from manipulation_agent.clients.types import PreparedProject
from manipulation_agent.exploration_factory import BridgeFactory, BridgeEpisode
from manipulation_agent.exploration_session import ExplorationSession, prepare_session
from manipulation_agent.exploration_frontend import tool_specs
from manipulation_agent.mcp_preflight import check_server

REPO = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.gettempdir()) / "mas-exploration-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
TOOLS = [{'name':'initialize', 'inputSchema':{'type':'object','properties':{},'required':[]}}]


def launcher_module():
    spec = importlib.util.spec_from_file_location('recovery_launcher', REPO/'scripts/run_exploration_pipeline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkerEnvironmentTests(unittest.TestCase):
    def test_long_instruction_round_trips_without_native_command_line_or_shell_expansion(self):
        spec = importlib.util.spec_from_file_location('short_argv_worker', REPO/'scripts/run_exploration_episode.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            source, run = root/'source', root/'run'
            package = source/'src/manipulation_agent'
            package.mkdir(parents=True)
            (package/'__init__.py').touch()
            (package/'vision_cli.py').write_text('import json,os,sys\n'
                'open(os.environ["TEST_ARGV_OUTPUT"],"w").write(json.dumps(sys.argv))\n')
            (source/'scripts').mkdir()
            shutil.copyfile(REPO/'scripts/exploration_simulator_bootstrap.py',
                            source/'scripts/exploration_simulator_bootstrap.py')
            run.mkdir()
            env_sh = root/'env.sh'
            env_sh.write_text('# CPU fixture\n')
            sentinel = root/'must_not_exist'
            instruction = ('long instruction "quoted"\n' * 1000) + '$(touch '+str(sentinel)+')'
            manifest = {'gpu':0,'gpu_uuid':'GPU-fixture','python':'/usr/bin/python3',
                        'env_sh':str(env_sh),'identity':{'task':'fixture','instance':0,'seed':0},
                        'instruction':instruction,'bridge_port':29999}
            cmd, _, _ = module.command(manifest,run,source)
            self.assertNotIn(instruction,cmd)
            self.assertLess(sum(len(a) for a in cmd),5000)
            self.assertFalse((run/'simulator_argv.json').exists())  # --plan stays read-only.
            expected = module.simulator_arguments(manifest,run)
            (run/'simulator_argv.json').write_text(json.dumps(expected))
            output = root/'observed.json'
            subprocess.run(cmd[cmd.index('--')+1:],env={**os.environ,'TEST_ARGV_OUTPUT':str(output)},
                           capture_output=True,text=True,check=True,timeout=10)
            self.assertEqual(json.loads(output.read_text()),['vision_cli',*expected])
            self.assertFalse(sentinel.exists())

    def test_private_caches_override_sourced_shared_defaults_and_proxy_wraps_python(self):
        spec = importlib.util.spec_from_file_location('environment_worker', REPO/'scripts/run_exploration_episode.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            source = root/'env.sh'
            source.write_text('export CUDA_CACHE_PATH=/shared\nexport XDG_DATA_HOME=/shared\n'
                              'export TORCHINDUCTOR_CACHE_DIR=/shared\nexport PATH="'+str(root)+':$PATH"\n')
            proxy = root/'proxy'
            proxy.write_text('#!/bin/sh\nexport TEST_PROXY_USED=yes\nexec "$@"\n')
            proxy.chmod(0o700)
            probe = root/'probe'
            probe.write_text('#!/usr/bin/python3\nimport os,json\nprint(json.dumps({k:v for k,v in os.environ.items() '
                             'if k in ("CUDA_CACHE_PATH","XDG_DATA_HOME","TORCHINDUCTOR_CACHE_DIR","TEST_PROXY_USED")}))\n')
            probe.chmod(0o700)
            run = root/'run'
            manifest = {'gpu':0,'gpu_uuid':'GPU-fixture','python':str(probe),
                        'env_sh':str(source),'identity':{'task':'fixture','instance':0,'seed':0},
                        'instruction':'CPU environment test','bridge_port':29999,'use_proxy':True}
            cmd, _, _ = module.command(manifest,run,REPO)
            child = cmd[cmd.index('--')+1:]
            result = subprocess.run(child,capture_output=True,text=True,check=True,timeout=10)
            actual = json.loads(result.stdout)
            self.assertEqual(actual.pop('TEST_PROXY_USED'),'yes')
            for value in actual.values():
                self.assertTrue(Path(value).is_relative_to(run),value)


def config():
    return {'task':'CPU fixture', 'model':'gpt-6-astra', 'reasoning_effort':'xhigh',
            'source_snapshot':str(REPO), 'robot_tools':TOOLS,
            'exploration':[{'key':'explore', 'identity':{'task':'fixture','instance':0,'seed':0}}],
            'formal':[{'key':'formal', 'identity':{'task':'fixture','instance':1,'seed':0}}]}


class AgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.tmp.name)/'session'
        prepare_session(self.root,config(),TOOLS)
        self.module = launcher_module()
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.tmp.cleanup)

    def read_agent(self):
        return json.loads((self.root/'agent.json').read_text())

    def run_agent(self,adapter,resume=False):
        with patch.object(self.module,'get_adapter',return_value=adapter), patch.object(self.module,'check_server',return_value={}):
            return self.module.main(['run','--session',str(self.root),'--provider','myapi', *(['--resume'] if resume else [])])

    def adapter(self,body):
        owner = self
        class Adapter:
            prepared = 0
            resumes = []
            def prepare_project(self,cfg,*args):
                self.prepared += 1
                self.config = cfg
                self.native = cfg.output/'native_sessions'
                self.native.mkdir()
                return PreparedProject(['codex','exec','-'],None,'fixture',native_sessions=self.native)
            def prepare_resume(self,project,thread,prompt):
                owner.assertEqual(project.native_sessions,self.native)
                self.resumes.append(thread)
                return project
            def run(self,project,output,timeout,*,on_event=None):
                return body(output,on_event)
        return Adapter()

    def test_first_interrupt_persists_thread_before_exit_and_resumes_same_storage(self):
        def body(output,on_event):
            event = {'type':'thread.started','thread_id':'fixture-thread'}
            (output/'model_events.jsonl').write_text(json.dumps(event)+'\n')
            on_event(event)
            self.assertEqual(self.read_agent()['thread_id'],'fixture-thread')
            self.assertEqual(self.read_agent()['status'],'running')
            raise KeyboardInterrupt('CPU fixture')
        adapter = self.adapter(body)
        with self.assertRaises(KeyboardInterrupt):
            self.run_agent(adapter)
        self.assertEqual(self.read_agent()['status'],'paused')
        # Resume failure before another header must keep the original ID.
        with self.assertRaises(KeyboardInterrupt):
            self.run_agent(adapter,True)
        self.assertEqual(adapter.prepared,1)
        self.assertEqual(adapter.resumes,['fixture-thread'])
        self.assertEqual(self.read_agent()['thread_id'],'fixture-thread')

    def test_exception_before_resume_header_keeps_previous_thread(self):
        def first(output,on_event):
            on_event({'type':'thread.started','thread_id':'fixture-thread'})
            return SimpleNamespace(returncode=1)
        adapter = self.adapter(first)
        self.run_agent(adapter)
        def interrupt(*args,**kwargs):
            raise RuntimeError('CPU interruption before header')
        adapter.run = interrupt
        with self.assertRaisesRegex(RuntimeError,'CPU interruption'):
            self.run_agent(adapter,True)
        self.assertEqual(self.read_agent()['thread_id'],'fixture-thread')
        adapter.run = lambda *a,**kw: SimpleNamespace(returncode=1)
        self.run_agent(adapter,True)
        self.assertEqual(adapter.resumes,['fixture-thread','fixture-thread'])

    def test_changed_thread_on_resume_is_rejected_without_losing_original(self):
        adapter = self.adapter(lambda output,callback: (
            callback({'type':'thread.started','thread_id':'fixture-thread'}) or SimpleNamespace(returncode=1)))
        self.run_agent(adapter)
        def changed(project,output,timeout,*,on_event=None):
            on_event({'type':'thread.started','thread_id':'other-thread'})
        adapter.run = changed
        with self.assertRaisesRegex(RuntimeError,'changed model thread'):
            self.run_agent(adapter,True)
        self.assertEqual(self.read_agent()['thread_id'],'fixture-thread')

    def test_recover_killed_launcher_from_saved_event_file_before_resume(self):
        def first(output,on_event):
            (output/'model_events.jsonl').write_text('{"type":"thread.started","thread_id":"fixture-thread"}\n')
            return SimpleNamespace(returncode=1)
        adapter = self.adapter(first)
        self.run_agent(adapter)
        saved = self.read_agent()
        saved.pop('thread_id')
        saved['status'] = 'running'  # State left by SIGKILL, no finally/callback.
        (self.root/'agent.json').write_text(json.dumps(saved))
        adapter.run = lambda *a,**kw: SimpleNamespace(returncode=1)
        self.run_agent(adapter,True)
        self.assertEqual(adapter.resumes,['fixture-thread'])
        self.assertEqual(self.read_agent()['thread_id'],'fixture-thread')

    def test_bound_mcp_command_survives_filtered_parent_environment(self):
        snapshot = Path(self.tmp.name)/'snapshot'
        shutil.copytree(REPO/'src/manipulation_agent', snapshot/'src/manipulation_agent',
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        cfg = config()
        cfg['data_root'] = self.tmp.name
        cfg['source_snapshot'] = str(snapshot)
        # The outer entrypoint is tested separately; capture the actual MCP config.
        (self.root/'config.json').write_text(json.dumps(cfg))
        import hashlib
        state=json.loads((self.root/'state.json').read_text())
        state['config_sha256']=hashlib.sha256((self.root/'config.json').read_bytes()).hexdigest()
        (self.root/'state.json').write_text(json.dumps(state))
        adapter = self.adapter(lambda *args: SimpleNamespace(returncode=1))
        with patch.object(self.module,'enter_snapshot'), patch.object(self.module,'get_adapter',return_value=adapter), \
             patch.object(self.module,'check_server',return_value={}) as preflight:
            self.module.main(['run','--session',str(self.root),'--provider','myapi'])
        command = adapter.config.mcp_command
        args = adapter.config.mcp_args
        self.assertEqual(preflight.call_args.args[:2], (command,args))
        module_flag = args.index('-m')
        probe = [command,*args[:module_flag],'-c',
                 'import manipulation_agent.exploration_frontend as m;print(m.__file__)']
        env = dict(os.environ)
        env.pop('PYTHONPATH',None)
        result = subprocess.run(probe,env=env,cwd=self.tmp.name,capture_output=True,text=True,check=True,timeout=20)
        self.assertEqual(Path(result.stdout.strip()),snapshot/'src/manipulation_agent/exploration_frontend.py')
        # Actual MCP handshake with the SAME argv, no paid model or robot calls.
        output=Path(self.tmp.name)/'preflight'
        output.mkdir()
        with patch.dict(os.environ,{'PYTHONPATH':'/deliberately/wrong'}):
            checked=check_server(command,args,tool_specs(TOOLS),output,timeout=30)
        self.assertEqual(checked['status'],'passed')
        self.assertEqual(checked['tool_calls'],0)

    def test_service_bus_coordinates_survive_filtered_mcp_environment(self):
        values = {'XDG_RUNTIME_DIR':'/run/user/123',
                  'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/123/bus',
                  'UNRELATED_SECRET':'must-not-forward'}
        with patch.dict(os.environ, values, clear=True):
            args = self.module.mcp_host_environment()
        probe = ['/usr/bin/env', *args, sys.executable, '-c',
                 'import os,json;print(json.dumps(dict(os.environ)))']
        result = subprocess.run(probe, env={}, capture_output=True, text=True,
                                check=True, timeout=10)
        actual = json.loads(result.stdout)
        self.assertEqual(actual['XDG_RUNTIME_DIR'], values['XDG_RUNTIME_DIR'])
        self.assertEqual(actual['DBUS_SESSION_BUS_ADDRESS'], values['DBUS_SESSION_BUS_ADDRESS'])
        self.assertNotIn('UNRELATED_SECRET', actual)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.module.mcp_host_environment(), [])

    def test_formal_entry_reexecs_frozen_source_before_loading_session(self):
        snapshot=Path(self.tmp.name)/'snapshot'
        entry=snapshot/'scripts/run_exploration_pipeline.py'
        entry.parent.mkdir(parents=True)
        entry.write_text('# frozen entry fixture\n')
        cfg=config();cfg['source_snapshot']=str(snapshot)
        (self.root/'config.json').write_text(json.dumps(cfg))
        class Reexec(Exception):pass
        with patch.object(self.module.os,'execve',side_effect=Reexec) as execute, \
             patch.object(self.module,'ExplorationSession') as current_session:
            with self.assertRaises(Reexec):
                self.module.main(['evaluate','--session',str(self.root)])
        self.assertEqual(execute.call_args.args[1][1],str(entry))
        self.assertEqual(execute.call_args.args[2]['PYTHONPATH'],str(snapshot/'src'))
        current_session.assert_not_called()

    def test_direct_snapshot_entry_with_wrong_imports_also_reexecs(self):
        snapshot=Path(self.tmp.name)/'snapshot'
        entry=snapshot/'scripts/run_exploration_pipeline.py'
        entry.parent.mkdir(parents=True)
        entry.write_text('# frozen entry fixture\n')
        cfg=config();cfg['source_snapshot']=str(snapshot)
        class Reexec(Exception):pass
        with patch.object(self.module,'__file__',str(entry)), \
             patch.object(self.module.os,'execve',side_effect=Reexec) as execute:
            with self.assertRaises(Reexec):self.module.enter_snapshot(cfg,['evaluate','--session',str(self.root)])
        self.assertEqual(execute.call_args.args[2]['PYTHONPATH'],str(snapshot/'src'))


class ClientEventTests(unittest.TestCase):
    def test_live_event_persisted_and_interrupted_client_is_reaped(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            output=Path(temp)
            code='import json,time; print(json.dumps({"type":"thread.started","thread_id":"fixture"}),flush=True); time.sleep(60)'
            pids=[]
            events=[]
            def callback(event):
                os.kill(pids[0],0)  # callback arrives while the CPU child is alive
                events.append(event)
                raise KeyboardInterrupt('CPU fixture')
            with self.assertRaises(KeyboardInterrupt):
                run_process(PreparedProject([sys.executable,'-c',code],None,None),output,None,
                            on_start=pids.append,on_event=callback)
            self.assertEqual(events[0]['thread_id'],'fixture')
            with self.assertRaises(ProcessLookupError):os.kill(pids[0],0)


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'session'
        cfg=config()
        for i,schedule in enumerate(('exploration','formal')):
            cfg[schedule][0].update(run_dir=str(Path(self.tmp.name)/('env'+str(i))),
                bridge='http://127.0.0.1:'+str(29881+i),startup_timeout=0,
                launch_command=[sys.executable,'-c','import time;time.sleep(60)'],
                launcher_contract='resource_leased_environment_only')
        prepare_session(self.root,cfg,TOOLS)
        self.cfg=cfg
        self.factory=BridgeFactory(self.tmp.name)

    def test_reattached_episode_waits_for_matching_post_release_worker_marker(self):
        spec = self.cfg['exploration'][0]
        run = Path(spec['run_dir'])
        (run / 'env').mkdir(parents=True)
        record = {'status':'running', 'config':spec['identity']}
        (run / 'env/run.json').write_text(json.dumps(record))
        (run / 'worker_manifest.json').write_text('{}')
        (run / 'simulator_command.json').write_text(json.dumps({'unit':'owned.service'}))
        with patch('manipulation_agent.exploration_factory.health',
                   return_value={'ready':True,'closed':False,'tools':TOOLS}):
            episode = BridgeEpisode(spec)  # attach: no Popen handle
        record.update(status='complete', evaluation={'official_task_success':False})
        (run / 'env/run.json').write_text(json.dumps(record))
        markers = iter([
            {'unit':'owned.service','exit_code':0},  # old marker: leases may still be held
            {'unit':'other.service','resource_leases_released':True},
            {'unit':'owned.service','resource_leases_released':True},
        ])
        def advance(_):
            (run / 'worker_closed.json').write_text(json.dumps(next(markers)))
        with patch('manipulation_agent.exploration_factory.time.sleep',side_effect=advance) as sleep:
            result = episode.collect()
        self.assertEqual(sleep.call_count,3)
        self.assertIs(result['official_task_success'],False)

    def test_reattached_episode_does_not_treat_evaluator_finish_as_worker_exit(self):
        spec = dict(self.cfg['exploration'][0],cleanup_timeout=1)
        run = Path(spec['run_dir'])
        (run / 'env').mkdir(parents=True)
        (run / 'env/run.json').write_text(json.dumps({'status':'running','config':spec['identity']}))
        (run / 'worker_manifest.json').write_text('{}')
        with patch('manipulation_agent.exploration_factory.health',
                   return_value={'ready':True,'closed':False,'tools':TOOLS}):
            episode = BridgeEpisode(spec)
        (run / 'env/run.json').write_text(json.dumps({'status':'complete','evaluation':{}}))
        with patch('manipulation_agent.exploration_factory.time.monotonic',side_effect=[0,0,2]), \
             patch('manipulation_agent.exploration_factory.time.sleep'):
            with self.assertRaisesRegex(TimeoutError,'closure/evaluation is incomplete'):
                episode.collect()

    def test_timeout_retains_real_child_identity_and_blocks_new_episode(self):
        owner=ExplorationSession(self.root,self.factory)
        self.addCleanup(owner.close)
        children=[]
        popen=subprocess.Popen
        def start(*args,**kwargs):
            child=popen(*args,**kwargs);children.append(child)
            return child
        try:
            with patch('manipulation_agent.exploration_factory.subprocess.Popen',side_effect=start) as launch:
                with self.assertRaises(TimeoutError):owner.start('exploration')
                active=json.loads((self.root/'state.json').read_text())['active']
                self.assertEqual(active['launcher']['pid'],children[0].pid)
                self.assertIn('start_ticks',active['launcher'])
                self.assertEqual(active['status'],'recovery_required')
                self.assertIsNone(children[0].poll())
                with self.assertRaisesRegex(ValueError,'reconcile'):owner.start('exploration')
                launch.assert_called_once()
            owner.close()
            owner=ExplorationSession(self.root,self.factory)
            # Reopened owner can expose status even before readiness exists.
            self.assertTrue(owner.status()['recovery_required'])
            self.assertEqual(owner.status()['active']['launcher'],active['launcher'])
            with self.assertRaisesRegex(ValueError,'reconcile'):owner.start('formal')
            owner.close()
            # Late readiness attaches this same episode rather than resetting it.
            run=Path(active['spec']['run_dir']);(run/'env').mkdir(parents=True)
            (run/'env/run.json').write_text(json.dumps({'status':'running','config':active['spec']['identity']}))
            with patch('manipulation_agent.exploration_factory.health',return_value={'ready':True,'closed':False,'tools':TOOLS}), \
                 patch('manipulation_agent.exploration_factory.subprocess.Popen') as no_launch:
                owner=ExplorationSession(self.root,self.factory)
                self.assertTrue(owner.status()['episode_live'])
                self.assertFalse(owner.status()['recovery_required'])
                self.assertEqual(owner.status()['active']['launcher']['pid'],children[0].pid)
                no_launch.assert_not_called()
        finally:
            owner.close()
            for child in children:
                if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
                child.wait(timeout=5)

    def test_formal_unknown_startup_stops_schedule_and_never_marks_complete(self):
        class Factory:
            calls=0
            def start(self,spec,*,on_launch=None):
                self.calls+=1
                on_launch({'pid':12345,'host':'fixture'})
                raise TimeoutError('fixture')
            def attach(self,active):raise OSError('not ready')
        factory=Factory()
        owner=ExplorationSession(self.root,factory)
        try:
            p=self.root/'workspace/code/task';p.mkdir()
            (p/'controller.py').write_text('def main(ctx):\n return {"ok": True}\n')
            bundle=owner.dispatch('register',{'project':'task'})['bundle']
            owner.state['rehearsals'].append({'bundle':bundle,'execution':{'clean':True}})
            owner.dispatch('submit',{'bundle':bundle})
            with self.assertRaisesRegex(RuntimeError,'uncertain'):owner.evaluate()
            self.assertEqual(owner.state['phase'],'formal')
            self.assertEqual(owner.state['formal_used'],1)
            self.assertIsNone(owner.state['formal_results'][0]['evaluation']['official_task_success'])
            owner.close()
            owner=ExplorationSession(self.root,factory)
            with self.assertRaisesRegex(ValueError,'Reconcile'):owner.evaluate()
            self.assertEqual(factory.calls,1)
            self.assertEqual(owner.state['phase'],'formal')
        finally:owner.close()

    def test_interruption_before_launch_callback_keeps_scheduled_identity(self):
        class Factory:
            def start(self,spec,*,on_launch=None):raise KeyboardInterrupt('fixture')
        owner=ExplorationSession(self.root,Factory())
        try:
            with self.assertRaises(KeyboardInterrupt):owner.start('exploration')
            self.assertEqual(owner.state['active']['spec'],self.cfg['exploration'][0])
            self.assertEqual(owner.state['active']['status'],'recovery_required')
            with self.assertRaises(ValueError):owner.start('formal')
        finally:owner.close()

if __name__=='__main__':unittest.main()
