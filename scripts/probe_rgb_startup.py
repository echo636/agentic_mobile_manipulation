"""Isolated asset compatibility / RGB initialization check, without a model."""
import argparse
import json
from pathlib import Path
import sys
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import Recorder, write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--instance',type=int,default=301)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    recorder=Recorder(a.output,{'backend':'omnigibson','policy':'scripted_startup_probe',
        'task':a.task,'instance':a.instance,'seed':0,'validation_level':'real_simulator_initialization_only',
        'not_a_benchmark_attempt':True});backend=None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        backend=RGBBackend(a.task,a.instance,a.output,seed=0,record_video=True)
        recorder.run['backend']=backend.provenance();write_json(a.output/'run.json',recorder.run)
        obs=backend.observe();recorder.event('diagnostic_rgb',{'observation':obs})
        video=backend.finalize_video()
        validation={'status':'passed','level':'real_simulator_initialization_and_rgb',
                    'views':list(backend.rig),'model_used':False,'task_success_assessed':False}
        write_json(a.output/'validation.json',validation)
        recorder.finish({'status':'passed','task_success':None,'validation':validation,'video':video,
                         'actions':0,'tool_calls':0,'sim_steps':backend.steps})
        print(json.dumps(validation),flush=True)
    except Exception as exc:
        (a.output/'traceback.txt').write_text(traceback.format_exc())
        write_json(a.output/'validation.json',{'status':'failed','level':'real_simulator_initialization_only','error':str(exc)})
        recorder.finish({'status':'failed','task_success':None,'failure':str(exc)})
        raise
    finally:
        if backend is not None:backend.close()


if __name__=='__main__':main()
