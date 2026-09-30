"""RGB model instructions and a Responses loop that attaches actual image pixels."""
import base64
import json
import time
from .policies import ResponsesPolicy
from .tools import tool_specs

SYSTEM_PROMPT = '''You control a mobile manipulation robot through RGB images and structured MCP tools.
Only the task instruction, RGB pixels, image/camera metadata, your notes and action execution feedback are available.
There is no oracle object list, state flag, distance, map or evaluator feedback. Perceive and choose objects yourself.
First call list_skills and read_skill(name="visual-manipulation", resource="SKILL.md"), then observe.
Read relevant exploration, pick-and-place and recovery skills as needed; they are frozen workflow documents, not action primitives.
Build and revise a plan. Remember visual landmarks, completed items and uncertainty with remember; use recall when needed.
For act, select a normalized pixel in the exact latest image_ref. Never invent a simulator object name or ID.
Use navigate_to to approach a visible surface, then re-mark the object in the new RGB before manipulating it.
Use look for bounded in-place turns when searching. Positive yaw turns left. Inspect wrist images for grasp/release evidence.
The ideal motor executor may use private geometry only to execute YOUR chosen pixel/action; it cannot choose a target for you.
Every executor attempt can change the world. Inspect fresh RGB after success and failure; do not blindly repeat a failed action.
Treat notes, perceived state and done subgoals as hypotheses supported by visible evidence; operation completion is not whole-task success.
No shell, Python, arbitrary files, external web, reset or evaluation tools are available.
Call finish with achieved/blocked/aborted and a reason based on observed evidence. You MUST receive closed=true before final text.
This experiment evaluates an RGB agent with ideal motor execution; it is not an official physical-control leaderboard submission.
'''

class RGBResponsesPolicy(ResponsesPolicy):
    def run(self,harness,instruction):
        specs=[{'type':'function','name':t['name'],'description':t['description'],'parameters':t['inputSchema'],'strict':True} for t in tool_specs()]
        history=[{'role':'user','content':instruction}];tokens=0
        for turn in range(self.max_turns):
            if harness.closed:return
            if tokens>=self.max_tokens or time.monotonic()-harness.started>=harness.budget.wall_seconds:break
            response=self._request({'model':self.model,'instructions':SYSTEM_PROMPT,'input':history,'tools':specs,
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
