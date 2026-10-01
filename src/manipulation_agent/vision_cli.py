"""Default RGB-only agent entry point. Legacy oracle probes live in cli.py."""
import argparse
import faulthandler
import json
from pathlib import Path
import signal
import traceback
from .contracts import Budget
from .records import Recorder
from .vision_harness import VisionHarness
from . import bridge
from .executors.omnigibson_rgb import RGBBackend
from .observations.mock_rgb import MockRGBBackend
from .vision_policy import RGBResponsesPolicy


def main():
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
    recorder=Recorder(a.output,config);backend=None;harness=None
    try:
        backend=MockRGBBackend(a.output) if a.backend=='mock' else RGBBackend(a.task,a.instance,a.output,seed=a.seed,max_steps=a.max_sim_steps,inside_placement=a.inside_placement,record_video=a.record_video)
        harness=VisionHarness(backend,recorder,Budget(max_actions=a.max_actions,max_sim_steps=a.max_sim_steps),profile=a.agent_profile)
        if policy:policy.run(harness,a.instruction)
        else:bridge.serve(harness,a.port)
        return 0 if recorder.run.get('task_success') else 2
    except BaseException as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        recorder.event('runtime_failure',{'type':type(exc).__name__,'message':str(exc)})
        recorder.finish({'status':'failed','task_success':None,'failure':type(exc).__name__,
                         'actions':harness.actions if harness else None,'sim_steps':backend.steps if backend else None})
        raise
    finally:
        if backend is not None:backend.close()

if __name__=='__main__':raise SystemExit(main())
