"""RGB model instructions and a Responses loop that attaches actual image pixels."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from .policies import ResponsesPolicy
from .agent_loop import LoopConfig, VisualToolLoop

WORKFLOW_PROMPT = '''You control a mobile manipulation robot through RGB images and structured MCP tools.
Only the task instruction, RGB pixels, image/camera metadata, your notes and action execution feedback are available.
There is no oracle object list, state flag, distance, map or evaluator feedback. Perceive and choose objects yourself.
After a successful grasp, effect.grasped_category reports only the class actually picked by your selected pixel. Check it against the instruction before choosing a destination; it is not an object ID or goal-state report.
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
For liquid tasks, carry the visible source container and use `pour` on a visible fillable destination; for boiling or heating, carry the container, select the visible stove or appliance with `place_near_heating_element`, then use bounded `wait` actions and inspect the returned RGB.
Treat notes, perceived state and done subgoals as hypotheses supported by visible evidence; operation completion is not whole-task success.
No shell, Python, arbitrary files, external web, reset or evaluation tools are available.
Verify the instruction using the latest returned RGB and action feedback; no additional observation call is required before finish.
The episode has a finite model-action budget. Tool results report actions_remaining; when the required goals are verified, call finish promptly instead of spending the remaining budget on redundant views or retries.
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
After a successful grasp, effect.grasped_category reports only the class actually picked by your selected pixel. Check it against the instruction before choosing a destination; it is not an object ID or goal-state report.
act takes primitive, revision and target={image_ref: latest image reference, point: [x,y]} with normalized x left-to-right and y top-to-bottom.
For walking, call act with primitive=navigate_to and select a point in the current RGB. No manipulation action moves the base for you. After walking, select the object again in the fresh image before manipulation. release/wait use a null target. Use confirm_bulk_grasp=false for ordinary actions; true deliberately lifts an entire loaded support or furniture-sized object after a confirmation error.
Once the target is clearly visible and within manipulation range, select it for the intended manipulation action instead of navigating toward it again. A navigation step is for obtaining a workable view and reach, not for moving as close as possible.
When approaching an object to manipulate it, navigate toward a visible point on that object. If the object becomes cropped, hidden by the robot, or too small to identify in the fresh RGB, first move to a visible observation position and reselect the object; do not guess a manipulation pixel.
Four fixed cameras share capture_id/captured_at/sim_step and show directions relative to the robot. Capture does not rotate or move the robot. No wrist image, depth or geometry is model input.
Any of the four current image refs can supply a target; no turn is needed merely to see sideways or behind. look turns in place when a different physical orientation is useful; positive yaw turns left, within +/-90 degrees.
The ideal motor executor can use private geometry to execute your selected action; it cannot find or choose the target for you.
After success or failure inspect fresh RGB; operation completion alone is not task success. Try a bounded alternative when needed.
Open a visibly closed destination before picking an item; open/close need an empty hand. You may toggle the object you are currently carrying by selecting it in current RGB; toggling a different object needs an empty hand.
Use only these MCP tools. No shell, arbitrary files, code execution, external web, reset or evaluation access.
For act, use placement_yaw_degrees=null, wait_seconds=null and confirm_bulk_grasp=false unless applicable. place_on_top can request a relative rotation about vertical with placement_yaw_degrees. wait can request 0.1–20 simulation seconds with wait_seconds; inspect its returned RGB before another bounded wait. If grasp rejects a loaded support or furniture-sized pixel, no grasp occurred: choose a clearly exposed pixel on the intended item in fresh RGB. Set confirm_bulk_grasp=true only when you intentionally want to lift the whole support or furniture.
For attachment tasks, grasp the movable child, navigate to a visible approach to the compatible parent if it is beyond reach, then use attach on a fresh RGB point of that parent. If attach reports out_of_reach, change viewpoint or navigate to a different visible approach before retrying. The ideal executor aligns the pair's attachment links and verifies the simulator attachment state. Inspect the result; a successful tool response is not the independent task score.
For an under-placement task, carry the object and select a visible point on the parent for place_under. It uses the simulator's Under sampler and checks the resulting relation; simply placing an object on nearby floor does not prove it is under that parent.
For a next-to task, carry the object and select a visible point on the intended same parent for place_next_to. It searches nearby supported poses and checks the simulator's NextTo relation. Select the same parent for every item that must share one parent.
For material or cutting tasks, carry a compatible tool and select the visible target: wipe/sweep/vacuum remove material, spray/spread apply it, soak selects a fluid source or filled container, and cut selects the cuttable object. hang selects a compatible nail or hook while carrying the item.
Verify every instructed item, destination, count and final door/appliance state using the latest returned RGB and action feedback. No additional observation call is required before finish. If a process needs simulation time, use wait rather than repeatedly fetching images.
The episode has a finite model-action budget. Tool results report actions_remaining; when the required goals are verified, call finish promptly instead of spending the remaining budget on redundant views or retries.
Do not report blocked solely because an earlier pixel or action failed while the instruction still has feasible work and actions remain. Inspect fresh views, choose a different visible point or navigate to another view, and continue; use blocked only when no feasible recovery remains or a hard limit is reached.
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
If one item or required arrangement remains uncertain, keep inspecting the available RGB and change viewpoint when useful. Finish blocked only after an explicit tool budget/deadline error or after distinct recovery approaches have failed and no visible feasible action remains. Elapsed conversation length alone is not an episode-limit signal; do not stop midway through a feasible sequence. Do not silently omit a required item.
Before a material or cutting action, read visual-manipulation/references/material-actions.md through read_skill.
"""
PLANNING_PROMPT = SKILLS_PROMPT.replace(
    'using front/back/left/right RGB and six tools: initialize, look, act, finish, list_skills, read_skill.',
    'using front/back/left/right RGB and nine tools: initialize, look, act, finish, list_skills, read_skill, update_plan, remember, recall.').replace(
    'First call initialize({})', 'First call initialize with the required decision object').replace(
    'There is no explicit planning tool or memory store. Skills are callable workflow documents, not autonomous executors.',
    'Use update_plan for a revisable dependency graph and remember/recall for observed facts and uncertainty. Skills are workflow documents, not autonomous executors.').replace(
    'There is no extra decision-summary schema or required language. Use normal conversation history to track progress; plan/remember/recall are unavailable.',
    'Every tool call requires a short public decision object with state, active_subgoal, reason, and expected. Report observable evidence and the immediate purpose, not private hidden reasoning. Keep the goal DAG in update_plan and revise it after relevant feedback.')
SYSTEM_PROMPT = SKILLS_PROMPT

SEMANTIC_PROMPT = '''You control a mobile robot through high-level actions.
The user message is your task instruction. Call initialize first; it returns
four RGB views and the target names accepted by act_semantic. The tool catalog
defines the available actions. Choose each action, target and order yourself.
The executor may walk to a named target and apply a high-level operation.
Inspect feedback after each call; it reports only the attempted operation, not
whether the entire task is complete. When done or blocked, call finish with a
factual reason and wait for closed=true. No shell, files, web or evaluator access.
'''

def system_prompt(profile='skills'):
    prompt = {'minimal':MINIMAL_PROMPT,'skills':SKILLS_PROMPT,'planning':PLANNING_PROMPT,
              'workflow':WORKFLOW_PROMPT,'semantic':SEMANTIC_PROMPT}[profile]
    if os.environ.get('MAS_HIGH_LEVEL_HARNESS') == '1':
        prompt += ('\nFor this hot-drink task, act with primitive=boil selects '
                   'the visible kettle or stove. A successful boil result confirms the water is boiled; '
                   'do not use wait to heat water. Choose the action order yourself.\n')
    return prompt

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
