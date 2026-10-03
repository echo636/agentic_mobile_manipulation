"""RGB model instructions and a Responses loop that attaches actual image pixels."""
import time
from concurrent.futures import ThreadPoolExecutor
from .policies import ResponsesPolicy
from .agent_loop import LoopConfig, VisualToolLoop

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
SKILLS_PROMPT += """
The MCP tool catalog is checked before policy startup. If a tool seems unavailable, actually attempt list_skills/observe and report the returned error; do not infer unavailability from an empty workspace.
For act, use placement_yaw_degrees=null and wait_seconds=null unless applicable. place_on_top can request a relative rotation about vertical with placement_yaw_degrees; wait can request 0.1–20 simulation seconds with wait_seconds. Use repeated bounded waits to allow a visible appliance process, checking RGB between waits.
Before finish(achieved), obtain a fresh four-camera observation and verify every instructed item, destination, count and final door/appliance state. A tool returning completed is only evidence of that operation. If one item or required arrangement remains uncertain, keep inspecting or finish blocked; do not silently omit it.
"""
SYSTEM_PROMPT = SKILLS_PROMPT

OFFICIAL_PROMPT = '''You control a robot through four fixed front/back/left/right RGB cameras and official OmniGibson symbolic actions.
First observe, or start_observation then get_observation until the read-only job returns four images. No camera rotation is required.
Use act(primitive, target, revision); select a normalized point in ANY latest RGB image. Only release takes target=null.
Perceive and select objects yourself. No object names/IDs, poses, depth, maps, current joint state, inventory truth or evaluator feedback are available.
This is a symbolic-executor experiment: the official primitive can directly set object states/poses, including assisted grasp and endpoint navigation.
It has its own preconditions and settling; the harness does not automatically approach targets or supply custom navigation/carry/placement repairs.
The pixel identifies the target object, not an exact placement point. No placement yaw or wait options exist in this mode.
NAVIGATE_TO uses an initialized native planner for candidate collision and arm-reachability checks; sampling a valid nearby pose can still fail. Report limitations from actual tool feedback; do not repeatedly retry the same failure.
Opening, closing and toggling require an empty hand under official preconditions. Select an item for grasp, a destination for placement, a fluid source/container for soaking a held item, a target for wiping/cutting with a held tool, or a heat source for a held item.
Every act returns fresh RGB, including after errors; failed official actions may have changed the world and are not rolled back.
Use only these MCP tools. No planning/memory tools, code execution, shell, files, reset, scene queries or evaluator access.
Inspect RGB to verify every requested object and final state before finish(achieved). If blocked, finish(blocked) with the observed limitation. Receive closed=true before final text.
The independent evaluator scores the final scene privately. Tool completion is not whole-task success. This is not a physical-control benchmark submission.
Your public output and tool calls are recorded verbatim for replay.
'''

def system_prompt(profile='skills'):
    return {'minimal':MINIMAL_PROMPT,'skills':SKILLS_PROMPT,'workflow':WORKFLOW_PROMPT,'official':OFFICIAL_PROMPT}[profile]

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
