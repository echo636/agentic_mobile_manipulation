"""RGB model instructions and a Responses loop that attaches actual image pixels."""
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from .policies import ResponsesPolicy
from .tools import tool_specs

WORKFLOW_PROMPT = '''You control a mobile manipulation robot through RGB images and structured MCP tools.
Only the task instruction, RGB pixels, image/camera metadata, your notes and action execution feedback are available.
There is no oracle object list, state flag, distance, map or evaluator feedback. Perceive and choose objects yourself.
First call list_skills and read_skill(name="visual-manipulation", resource="SKILL.md"), then observe.
Read relevant exploration, pick-and-place and recovery skills as needed; they are frozen workflow documents, not action primitives.
Build and revise a plan. Remember visual landmarks, completed items and uncertainty with remember; use recall when needed.
For act, select a normalized pixel in the exact latest image_ref. Never invent a simulator object name or ID.
Use navigate_to to approach a visible surface, then re-mark the object in the new RGB before manipulating it.
Use look for bounded in-place turns when searching. Positive yaw turns left. Inspect the four camera images and execution feedback; no wrist camera or inventory truth is available.
The ideal motor executor may use private geometry only to execute YOUR chosen pixel/action; it cannot choose a target for you.
Every executor attempt can change the world. Inspect fresh RGB after success and failure; do not blindly repeat a failed action.
Treat notes, perceived state and done subgoals as hypotheses supported by visible evidence; operation completion is not whole-task success.
No shell, Python, arbitrary files, external web, reset or evaluation tools are available.
Call finish with achieved/blocked/aborted and a reason based on observed evidence. You MUST receive closed=true before final text.
This experiment evaluates an RGB agent with ideal motor execution; it is not an official physical-control leaderboard submission.
'''

MINIMAL_PROMPT = '''You control a mobile manipulation robot using RGB and four tools: observe, look, act, finish.
First observe. Use the current RGB to find the instructed objects, select a pixel, execute one action, then inspect the returned RGB.
Repeat this direct observation-action-feedback loop until the task appears complete or you are blocked.
There is no explicit planning tool, memory store or skill-reading phase. Your conversation retains previous RGB and tool feedback.
Only the task instruction, robot RGB images, image metadata and bounded execution feedback are available.
There is no oracle object list, object ID, distance, map, state flag or evaluator feedback. Perceive and choose targets yourself.
act takes primitive, revision and target={image_ref: latest image reference, point: [x,y]} with normalized x left-to-right and y top-to-bottom.
Navigate toward a visible target, then select it again in the fresh image before manipulation. release/wait use a null target.
look turns in place; positive yaw turns left, within +/-90 degrees. Use it to search outside the current view.
The ideal motor executor can use private geometry to execute your selected action; it cannot find or choose the target for you.
After success or failure inspect fresh RGB; operation completion alone is not task success. Try a bounded alternative when needed.
Open a visibly closed destination before picking an item; this executor needs an empty hand to open/close/toggle.
Use only these MCP tools. No shell, arbitrary files, code execution, external web, reset or evaluation access.
Finish with achieved/blocked/aborted and a short reason based on your visual evidence. Receive closed=true before final text.
This is an RGB agent with ideal motor execution, not an official physical-control leaderboard submission.
'''
SKILLS_PROMPT = MINIMAL_PROMPT.replace(
    'using RGB and four tools: observe, look, act, finish.',
    'using front/back/left/right RGB and nine tools: start_observation, get_observation, cancel_observation, observe, look, act, finish, list_skills, read_skill.').replace(
    'First observe.',
    'First list_skills and read visual-manipulation/SKILL.md. Start an asynchronous observation with start_observation({}), then call get_observation(job_id) to receive its four images when status=passed. You may read skills while it runs. Read relevant skills as needed, including pick-and-place before carrying objects.').replace(
    'There is no explicit planning tool, memory store or skill-reading phase.',
    'There is no explicit planning tool or memory store. Skills are callable workflow documents, not autonomous executors.') + '\nYour public assistant messages and MCP calls are recorded verbatim for replay. There is no extra decision-summary schema or required language. Use normal conversation history to track progress; plan/remember/recall are unavailable.\n'
SKILLS_PROMPT += '''
Four fixed cameras capture front/back/left/right at one simulation state. Observation does not rotate or move the robot.
start_observation immediately queues a read-only job; get_observation returns progress or all four actual RGB images. If still running, follow poll_after_ms or do another useful skill read, then query again. cancel_observation cancels a pending job; completed jobs are immutable.
All four cameras share capture_id/captured_at/sim_step. Directions are relative to the robot, not global headings. No depth or geometry enters these images.
Check job.stale. A cached job can become stale after an action or another capture. Act only on a fresh image_ref returned to you. Choose a pixel from ANY of the four current cameras; no preliminary robot rotation is needed merely to see behind or sideways.
look remains an explicit robot turn when useful for an action, not the surround camera acquisition mechanism. All action responses also contain the new four-camera view.
'''
SYSTEM_PROMPT = SKILLS_PROMPT

def system_prompt(profile='skills'):
    return {'minimal':MINIMAL_PROMPT,'skills':SKILLS_PROMPT,'workflow':WORKFLOW_PROMPT}[profile]

class RGBResponsesPolicy(ResponsesPolicy):
    def _request_with_observation_jobs(self, harness, payload):
        # Only network I/O runs on a worker; all render/physics stays on the owner thread.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._request, payload)
            while not future.done():
                harness.tick_background()
                time.sleep(.01)
            return future.result()

    def run(self,harness,instruction):
        specs=[{'type':'function','name':t['name'],'description':t['description'],'parameters':t['inputSchema'],'strict':True} for t in harness.tool_specs()]
        history=[{'role':'user','content':instruction}];tokens=0
        for turn in range(self.max_turns):
            if harness.closed:return
            if tokens>=self.max_tokens or time.monotonic()-harness.started>=harness.budget.wall_seconds:break
            response=self._request_with_observation_jobs(harness,{'model':self.model,'instructions':system_prompt(harness.profile),'input':history,'tools':specs,
                                    'parallel_tool_calls':False,'store':False,'max_output_tokens':4000})
            output=response.get('output',[]);usage=response.get('usage') or {};tokens+=usage.get('total_tokens',0)
            harness.recorder.event('model_response',{'turn':turn,'model':self.model,'output':[o for o in output if o.get('type') in {'message','function_call'}],'usage':usage})
            history.extend(output)
            calls=[o for o in output if o.get('type')=='function_call']
            if not calls:
                history.append({'role':'user','content':'Continue through RGB tools and formally call finish.'});continue
            for call in calls:
                try:args=json.loads(call['arguments'])
                except (ValueError,KeyError):result={'ok':False,'error':{'code':'invalid_json'}}
                else:result=harness.call(call['name'],args,call['call_id'])
                history.append({'type':'function_call_output','call_id':call['call_id'],'output':json.dumps(result)})
                content=[]
                for frame in result.get('observation',{}).get('images',[]):
                    data,mime=harness.image_bytes(frame['image_ref'])
                    content.extend([{'type':'input_text','text':f"Robot RGB: {frame['view']} / {frame['image_ref']}"},
                                    {'type':'input_image','image_url':'data:'+mime+';base64,'+base64.b64encode(data).decode(),'detail':'high'}])
                if content:history.append({'role':'user','content':content})
                if harness.closed:return
        if not harness.closed:harness.call('finish',{'outcome':'aborted','reason':'Model budget exhausted'},'runner-budget-stop')
