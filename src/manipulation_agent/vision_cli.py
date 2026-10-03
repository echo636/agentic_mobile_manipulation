"""Default RGB-only agent entry point. Legacy oracle probes live in cli.py."""
import argparse
import faulthandler
import json
from pathlib import Path
import signal
import traceback
from .contracts import Budget
from .deadline import EpisodeDeadline
from .records import Recorder
from .vision_harness import VisionHarness
from . import bridge
from .executors.omnigibson_rgb import RGBBackend
from .observations.mock_rgb import MockRGBBackend
from .vision_policy import RGBResponsesPolicy


def main():
    # Batch clocks arm privately after readiness + MCP handshake, immediately
    # before model startup. Initialization has a separate supervisor watchdog.
    deadline=EpisodeDeadline.from_env()
    faulthandler.enable();faulthandler.register(signal.SIGUSR1,all_threads=False)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['mock','omnigibson'],default='omnigibson')
    p.add_argument('--policy',choices=['serve','responses'],default='serve')
    p.add_argument('--task',default='turning_on_radio');p.add_argument('--instance',type=int,default=301)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--instruction');p.add_argument('--inside-placement',choices=['symbolic_raycast','official_volume'],default='official_volume')
    p.add_argument('--port',type=int,default=29440);p.add_argument('--controller',default='unspecified')
    p.add_argument('--agent-profile', choices=['minimal','skills','workflow'], default='skills')
    p.add_argument('--record-video', action=argparse.BooleanOptionalAction, default=None,
                   help='Record a control-step timeline with explicit frame holds (default on for OmniGibson); spectator RGB stays offline')
    p.add_argument('--max-actions',type=int,default=80);p.add_argument('--max-sim-steps',type=int,default=20000)
    a=p.parse_args()
    if a.record_video is None: a.record_video = a.backend == 'omnigibson'
    if a.instruction is None:
        catalog=json.loads((Path(__file__).parent/'tasks.json').read_text())
        a.instruction=catalog['tasks'].get(a.task,{}).get('instruction')
        if not a.instruction:p.error('Provide --instruction for this task')
    policy=RGBResponsesPolicy.from_env() if a.policy=='responses' else None
    config=vars(a).copy();config['output']=str(a.output.resolve());config['observation_mode']='rgb_only'
    config['validation_level']='cpu_rgb_contract_only' if a.backend=='mock' else 'rgb_simulator_requires_controller_image_evidence'
    config['episode_deadline_unix']=deadline.unix
    config['execution_clock_path']=str(deadline.clock_path) if deadline.clock_path is not None else None
    recorder=Recorder(a.output,config);backend=None;harness=None
    try:
        deadline.check()
        backend=MockRGBBackend(a.output) if a.backend=='mock' else RGBBackend(a.task,a.instance,a.output,seed=a.seed,max_steps=a.max_sim_steps,inside_placement=a.inside_placement,record_video=a.record_video)
        backend.deadline=deadline
        harness=VisionHarness(backend,recorder,Budget(max_actions=a.max_actions,max_sim_steps=a.max_sim_steps),profile=a.agent_profile)
        if policy:
            harness.start_standalone_clock()
            policy.run(harness,a.instruction)
        else:bridge.serve(harness,a.port)
        return 0 if recorder.run.get('status')=='passed' else 2
    except BaseException as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        recorder.event('runtime_failure',{'type':type(exc).__name__,'message':str(exc)})
        if recorder.run.get('status')=='running':
            failure={'status':'failed','task_success':None,'failure':type(exc).__name__,
                     'scoring':{'status':'not_attempted'},
                     'actions':harness.actions if harness else None,'sim_steps':backend.steps if backend else None}
            if deadline.expired:
                failure.update(timeout=True,episode_outcome='timeout',termination_reason='episode_deadline_exceeded',
                               episode_deadline_unix=deadline.unix)
            recorder.finish(failure,render=False)
        else:
            # A later transport/packaging failure cannot erase a persisted score.
            recorder.run['post_finish_failure']={'type':type(exc).__name__,'message':str(exc)}
            from .records import write_json
            write_json(a.output/'run.json',recorder.run)
        raise
    finally:
        try:
            if harness is not None:harness.finalize_recording()
        finally:
            if backend is not None:backend.close()
            elif a.backend == 'omnigibson':
                # A failing constructor never assigns backend, even if Isaac
                # already launched. Close that existing app before unwinding.
                from .startup_cleanup import shutdown_partial_simulator
                cleanup = shutdown_partial_simulator()
                try:
                    recorder.event('startup_cleanup', cleanup)
                except Exception:
                    # Primary failure/traceback were persisted above. A final
                    # diagnostic write must not replace that original error.
                    pass

if __name__=='__main__':raise SystemExit(main())
