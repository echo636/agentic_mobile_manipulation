"""RGB model instructions and a Responses loop that attaches actual image pixels."""
import time
from concurrent.futures import ThreadPoolExecutor
from .policies import ResponsesPolicy
from .agent_loop import LoopConfig, VisualToolLoop

WORKFLOW_PROMPT = '''You control a mobile manipulation robot through RGB images and structured MCP tools.
Only the task instruction, RGB pixels, image/camera metadata, your notes and action execution feedback are available.
There is no oracle object list, state flag, distance, map or evaluator feedback. Perceive and choose objects yourself.
First call initialize({}) and inspect its front/back/left/right RGB. It returns the prepared episode's current view without resetting the scene.
Then call list_skills and read_skill(name="visual-manipulation", resource="SKILL.md").
Read relevant exploration, pick-and-place and recovery skills as needed; they are frozen workflow documents, not action primitives.
Build and revise a plan. Remember visual landmarks, completed items and uncertainty with remember; use recall when needed.
For act, select a normalized pixel in the exact latest image_ref. Never invent a simulator object name or ID.
Use navigate_to to approach a visible surface, then re-mark the object in the new RGB before manipulating it.
Use look for bounded in-place turns when searching. Positive yaw turns left. Inspect the four camera images and execution feedback; no wrist camera or inventory truth is available.
The ideal motor executor may use private geometry only to execute YOUR chosen pixel/action; it cannot choose a target for you.
Every executor attempt can change the world. Inspect the RGB returned by act/look after success and failure; do not blindly repeat a failed action.
Use act with primitive=wait and target=null when a process needs simulation time, then inspect its returned RGB.
Treat notes, perceived state and done subgoals as hypotheses supported by visible evidence; operation completion is not whole-task success.
No shell, Python, arbitrary files, external web, reset or evaluation tools are available.
Verify the instruction using the latest returned RGB and action feedback; no additional observation call is required before finish.
Call finish with achieved/blocked/aborted and a reason based on observed evidence. You MUST receive closed=true before final text.
This experiment evaluates an RGB agent with ideal motor execution; it is not an official physical-control leaderboard submission.
'''

MINIMAL_PROMPT = '''You control a mobile manipulation robot using RGB and four tools: initialize, look, act, finish.
First call initialize({}) and inspect its front/back/left/right RGB. It returns the prepared episode's current view without resetting the scene.
Use the current RGB to find the instructed objects, select a pixel, execute one action, then inspect the four views returned by act/look.
Repeat this direct observation-action-feedback loop until the task appears complete or you are blocked.
There is no explicit planning tool, memory store or skill-reading phase. Your conversation retains previous RGB and tool feedback.
Only the task instruction, robot RGB images, image metadata and bounded execution feedback are available.
There is no oracle object list, object ID, distance, map, state flag or evaluator feedback. Perceive and choose targets yourself.
act takes primitive, revision and target={image_ref: latest image reference, point: [x,y]} with normalized x left-to-right and y top-to-bottom.
Navigate toward a visible target, then select it again in the fresh image before manipulation. release/wait use a null target.
When approaching an object to manipulate it, navigate toward a visible point on that object. If the object becomes cropped, hidden by the robot, or too small to identify in the fresh RGB, first move to a visible observation position and reselect the object; do not guess a manipulation pixel.
Four fixed cameras share capture_id/captured_at/sim_step and show directions relative to the robot. Capture does not rotate or move the robot. No wrist image, depth or geometry is model input.
Any of the four current image refs can supply a target; no turn is needed merely to see sideways or behind. look turns in place when a different physical orientation is useful; positive yaw turns left, within +/-90 degrees.
The ideal motor executor can use private geometry to execute your selected action; it cannot find or choose the target for you.
After success or failure inspect fresh RGB; operation completion alone is not task success. Try a bounded alternative when needed.
Open a visibly closed destination before picking an item; this executor needs an empty hand to open/close/toggle.
Use only these MCP tools. No shell, arbitrary files, code execution, external web, reset or evaluation access.
For act, use placement_yaw_degrees=null and wait_seconds=null unless applicable. place_on_top can request a relative rotation about vertical with placement_yaw_degrees. wait can request 0.1–20 simulation seconds with wait_seconds; inspect its returned RGB before another bounded wait.
Verify every instructed item, destination, count and final door/appliance state using the latest returned RGB and action feedback. No additional observation call is required before finish. If a process needs simulation time, use wait rather than repeatedly fetching images.
Finish with achieved/blocked/aborted and a short reason based on your visual evidence. Receive closed=true before final text.
This is an RGB agent with ideal motor execution, not an official physical-control leaderboard submission.
'''
SKILLS_PROMPT = MINIMAL_PROMPT.replace(
    'using RGB and four tools: initialize, look, act, finish.',
    'using front/back/left/right RGB and six tools: initialize, look, act, finish, list_skills, read_skill.').replace(
    'Use the current RGB to find the instructed objects,',
    'Read visual-manipulation/SKILL.md through list_skills/read_skill. Read relevant skills as needed, including pick-and-place before carrying objects. Use the current RGB to find the instructed objects,').replace(
    'There is no explicit planning tool, memory store or skill-reading phase.',
    'There is no explicit planning tool or memory store. Skills are callable workflow documents, not autonomous executors.') + '\nYour public assistant messages and MCP calls are recorded verbatim for replay. There is no extra decision-summary schema or required language. Use normal conversation history to track progress; plan/remember/recall are unavailable.\n'
SKILLS_PROMPT += """
The MCP tool catalog is checked before policy startup. If a tool seems unavailable, actually attempt initialize or list_skills and report the returned error; do not infer unavailability from an empty workspace.
If one item or required arrangement remains uncertain, keep inspecting the available RGB, change viewpoint when useful, or finish blocked; do not silently omit it.
"""
SYSTEM_PROMPT = SKILLS_PROMPT

def system_prompt(profile='skills'):
    return {'minimal':MINIMAL_PROMPT,'skills':SKILLS_PROMPT,'workflow':WORKFLOW_PROMPT}[profile]

class RGBResponsesPolicy(ResponsesPolicy):
    def _request_with_observation_jobs(self, harness, payload):
        # Only network I/O runs on a worker; all render/physics stays on the owner thread.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._request, payload, deadline=harness.deadline)
            while not future.done():
                harness.tick_background()
                time.sleep(.01)
            return future.result()

    def run(self, harness, instruction, *, image_history_captures=0):
        loop = VisualToolLoop(model=self.model, instructions=system_prompt(harness.profile),
                              request=self._request_with_observation_jobs,
                              config=LoopConfig(max_turns=self.max_turns, max_tokens=self.max_tokens,
                                                image_history_captures=image_history_captures))
        return loop.run(harness, instruction)
