"""Environment-only exploration worker. No model calls; shared leases + admission.

Manifest fields: run_dir, source_path, data_root, lease_dir, python, env_sh,
gpu, gpu_uuid, bridge_port, identity={task,instance,seed}, instruction.
New prepared workers skip GPU-memory admission by explicit user policy.
Historical manifests retain their recorded memory threshold.
All writable runtime directories are private descendants of run_dir.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from manipulation_agent.batch_lifecycle import WorkerLease, FileLease
from manipulation_agent.exploration_factory import health
from manipulation_agent.records import write_json

def configuration(path):
    m=json.loads(Path(path).read_text())
    root=Path(m["data_root"]).resolve()
    run=Path(m["run_dir"]).resolve()
    source=Path(m["source_path"]).resolve()
    if not run.is_relative_to(root) or source==run or run.is_relative_to(source):
        raise ValueError("Use a private NAS run outside the source checkout")
    hashes=json.loads((source/"source_manifest.json").read_text())
    if any(hashlib.sha256((source/rel).read_bytes()).hexdigest()!=digest for rel,digest in hashes.items()):
        raise ValueError("Frozen environment source changed")
    identity=m["identity"]
    if set(identity)!={"task","instance","seed"} or not isinstance(identity["task"],str):
        raise ValueError("Explicit task/instance/seed required")
    if any(type(identity[k]) is not int for k in ("instance","seed")):
        raise ValueError("Integer instance and seed required")
    if type(m["gpu"]) is not int or m["gpu"]<0 or type(m["bridge_port"]) is not int or not 1024 <= m["bridge_port"] <=65535:
        raise ValueError("Explicit GPU and loopback bridge port required")
    if not m["gpu_uuid"].startswith("GPU-"):
        raise ValueError("GPU UUID required")
    minimum = m.get("minimum_free_gpu_mib", 8*1024)
    if (type(minimum) is not int or minimum < 0
            or (minimum == 0 and m.get('skip_gpu_memory_admission') is not True)):
        raise ValueError("A zero memory threshold requires skip_gpu_memory_admission=true")
    if "resource_helper_path" in m:
        helper = Path(m["resource_helper_path"])
        if not helper.is_absolute() or helper.is_symlink() or hashlib.sha256(helper.read_bytes()).hexdigest()!=m.get("resource_helper_sha256"):
            raise ValueError("Operator resource helper hash mismatch")
    for entry in m.get('runtime_pythonpaths', []):
        if not Path(entry).is_absolute() or not Path(entry).resolve().is_relative_to(root):
            raise ValueError('Private runtime Python paths must be under data_root')
    if m.get('manip_engine') not in (None, 'geometry_pick_v1', 'model_pose_v1', 'model_program_v1'):
        raise ValueError('Unknown manipulation engine')
    if m.get('manip_timing') not in (None, 'historical_full_trajectory', 'explicit_fast_v1', 'model_estimated_v1', 'model_authored_v1'):
        raise ValueError('Unknown manipulation timing policy')
    if m.get('local_scratch_root') is not None:
        scratch = Path(m['local_scratch_root'])
        if (not scratch.is_absolute() or scratch.is_symlink() or
                not scratch.resolve().is_relative_to(Path('/tmp').resolve()) or
                scratch.name != run.name):
            raise ValueError('Local scratch must be a private /tmp directory named for this run')
    return m,run,source

def simulator_arguments(m, run):
    identity = m["identity"]
    return ["--backend","omnigibson", "--policy","serve",
      "--agent-profile","oracle_waypoints","--goal-skill","none",
      "--task",identity["task"],"--instance",str(identity["instance"]),"--seed",str(identity["seed"]),
      "--instruction",m["instruction"],"--ik-backend","omnigibson","--grasping-mode","assisted",
      "--wrist-cameras","--record-video","--unbounded-episode",
      "--oracle-transport-policy",m.get("transport_policy","native_only"),
      "--port",str(m["bridge_port"]),"--output",str(run/"env")]

def command(m,run,source):
    scratch = Path(m.get('local_scratch_root', str(run/'tmp')))
    env={
      "PYTHONPATH":os.pathsep.join([str(source/"src"), *m.get('runtime_pythonpaths', [])]),"PYTHONDONTWRITEBYTECODE":"1",
      "OMNIGIBSON_GPU_ID":str(m["gpu"]),"MAS_GPU_UUID":m["gpu_uuid"],
      "OMNIGIBSON_APPDATA_PATH":str(run/"appdata"),"TMPDIR":str(scratch),
      "XDG_CONFIG_HOME":str(run/"appdata/config"),"XDG_DATA_HOME":str(run/"appdata/share"),
      "XDG_STATE_HOME":str(run/"appdata/state"),"XDG_CACHE_HOME":str(run/"appdata/cache"),
      "MPLCONFIGDIR":str(run/"appdata/matplotlib"),"IPYTHONDIR":str(run/"appdata/ipython"),
    }
    if m.get('manip_engine'):
        env['MAS_MANIP_ENGINE'] = m['manip_engine']
    if m.get('manip_timing'):
        env['MAS_MANIP_TIMING'] = m['manip_timing']
    if m.get('smooth_navigation'):
        env['MAS_NAV_SMOOTH'] = '1'
    if m.get('navigation_engine_manifest') is not None:
        path = Path(m['navigation_engine_manifest'])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Existing absolute navigation manifest required')
        env['MAS_NAV_ENGINE_MANIFEST'] = str(path)
    if m.get('execution_capture_interval') is not None:
        if type(m['execution_capture_interval']) is not int or m['execution_capture_interval'] < 1:
            raise ValueError('execution_capture_interval must be a positive integer')
        env['MAS_EXECUTION_CAPTURE_INTERVAL'] = str(m['execution_capture_interval'])
    if m.get('native_transport') is not None:
        if m['native_transport'] != 'native_joint_v1':
            raise ValueError('Unknown native transport policy')
        env['MAS_NATIVE_TRANSPORT'] = m['native_transport']
    for key, leaf in {
        "PIP_CACHE_DIR":"pip", "HF_HOME":"huggingface", "CUDA_CACHE_PATH":"cuda",
        "TORCH_EXTENSIONS_DIR":"torch-extensions", "NUMBA_CACHE_DIR":"numba",
        "__GL_SHADER_DISK_CACHE_PATH":"nvidia-gl", "TORCH_HOME":"torch",
        "TRITON_CACHE_DIR":"triton",
    }.items():
        env[key] = str(run/"appdata/cache"/leaf)
    env["TORCHINDUCTOR_CACHE_DIR"] = str(scratch/"torchinductor")
    # Keep task prose out of the native process argv. Kit's crash reporter can
    # fault while parsing it; the established worker uses the same JSON handoff.
    args=[m["python"],"-u",str(source/"scripts/exploration_simulator_bootstrap.py"),
          str(run/"simulator_argv.json")]
    if m.get("use_proxy", False):
        args = ["proxy", *args]
    child=["/bin/bash","-lc",'source "$1"; shift; exec "$@"',"exploration-env",m["env_sh"],
           "/usr/bin/env","-u","CUDA_VISIBLE_DEVICES",*[k+"="+v for k,v in env.items()],*args]
    unit="mas-explore-"+hashlib.sha256(str(run).encode()).hexdigest()[:16]+".service"
    cmd=["systemd-run","--user","--unit",unit,"--collect","--wait","--pipe",
         "--property=MemoryMax=28G","--property=MemorySwapMax=0","--property=KillMode=control-group",
         "--",*child]
    return cmd,unit,env

def runtime_storage_paths(run, env):
    return [str(run), str(run/'appdata'), env['TMPDIR']]

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",required=True);p.add_argument("--plan",action="store_true")
    args=p.parse_args(argv)
    m,run,source=configuration(args.manifest)
    cmd,unit,env=command(m,run,source)
    if args.plan:
        print(json.dumps({"command":cmd,"unit":unit,"model_called":False,"gpu_started":False,
                          "minimum_free_gpu_mib":m.get("minimum_free_gpu_mib",8*1024)}))
        return 0
    if run.exists():raise ValueError("Refuse to overwrite an existing episode")
    if m.get('local_scratch_root'):
        Path(m['local_scratch_root']).mkdir(parents=True, exist_ok=False)
    run.mkdir(parents=True)
    for value in env.values():
        if value.startswith(str(run)):
            Path(value).mkdir(parents=True,exist_ok=True)
    write_json(run/"worker_manifest.json",m)
    write_json(run/"simulator_argv.json",simulator_arguments(m,run))
    write_json(run/"simulator_command.json",{"argv":cmd,"unit":unit,"identity":m["identity"]})
    helper=Path(m["resource_helper_path"]) if "resource_helper_path" in m else source/"scripts/behavior100_remote.py"
    spec=importlib.util.spec_from_file_location("_mas_exploration_admission",helper)
    remote=importlib.util.module_from_spec(spec);spec.loader.exec_module(remote)
    worker={"id":run.name,"ssh":[socket.gethostname()],"gpu":m["gpu"],
            "expected_gpu_uuid":m["gpu_uuid"],"memory_budget_gib":28}
    def owner_active(owner):
        # Retain unknown/active old units. Never stop another worker.
        check=subprocess.run(["systemctl","--user","show",owner["unit"],"--property=ActiveState","--value"],
                             capture_output=True,text=True,timeout=10)
        return check.returncode != 0 or check.stdout.strip() not in {"inactive","failed"}
    lease=WorkerLease(m["lease_dir"],worker,host_budget_gib=32,adaptive_slot_count=16,
        owner={"unit":unit,"run_id":run.name},owner_active=owner_active)
    gate=FileLease(Path(m["lease_dir"])/("startup-"+socket.gethostname()+".lock"),{})
    process=None
    code=None
    try:
        if not gate.acquire() or not lease.acquire():
            raise RuntimeError("Resource leases busy; no simulator started")
        policy={"profile":"throughput","host_budget_gib":None,"candidate_gib":28,"startup_gib":12,
                "peers":lease.active_peers()}
        memory_options = {'skip_gpu_memory_admission':True} if m.get('skip_gpu_memory_admission') is True else {}
        admission=remote.preflight(m["gpu"],m["bridge_port"],Path(m["data_root"]),
            min_free_gpu_mib=m.get("minimum_free_gpu_mib",8*1024),memory_budget_gib=28,light=True,memory_policy=policy,
            runtime_storage_paths=runtime_storage_paths(run, env), **memory_options)
        admission["checks"]["exact_gpu_uuid"]=admission["gpu_uuid"]==m["gpu_uuid"]
        with socket.socket() as probe:probe.bind(("127.0.0.1",m["bridge_port"]))
        write_json(run/"admission.json",admission)
        if not all(admission["checks"].values()):raise RuntimeError("Resource admission failed")
        lease.record(runtime_stage="starting")
        with (run/"simulator.log").open("w") as log:
            process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+m.get("startup_timeout",1800)
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError("Simulator startup exited")
                try:
                    if health("http://127.0.0.1:"+str(m["bridge_port"])).get("ready"):break
                except OSError:pass
                time.sleep(.5)
            else:
                subprocess.run(["systemctl","--user","stop",unit],check=False,timeout=45)
                raise TimeoutError("Owned initialization watchdog")
            lease.record(runtime_stage="running")
            gate.release()
            # No action or episode wall timeout. The session owner closes via finish.
            code=process.wait()
        return code
    except BaseException as exc:
        write_json(run/"worker_failure.json",{
            "exception":type(exc).__name__, "message":str(exc)[:1000],
            "unit":unit, "blocked_owner":lease.blocked_owner,
            "simulator_launcher_exit_code":process.poll() if process is not None else None})
        raise
    finally:
        gate.release()
        # Only release as cleared after our own unit has exited. An orphan retains
        # ownership metadata and requires operator reconciliation.
        lease.release(cleared=process is None or process.poll() is not None)
        # Consumers may attach after a provider restart without this launcher's
        # Popen handle. Publish closure only AFTER resource ownership is released.
        if code is not None:
            write_json(run/"worker_closed.json",{"exit_code":code,"unit":unit,
                                                "resource_leases_released":True})
if __name__=="__main__":raise SystemExit(main())
