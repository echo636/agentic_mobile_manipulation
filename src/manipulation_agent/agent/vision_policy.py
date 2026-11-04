"""RGB model instructions and a Responses loop that attaches actual image pixels."""
import time
from concurrent.futures import ThreadPoolExecutor
from .policies import ResponsesPolicy
from .agent_loop import LoopConfig, VisualToolLoop

MINIMAL_PROMPT = '''Complete the user's task using only the robot's RGB images, feedback and manipulation tools.
Start with initialize({}) and inspect the front, back, left and right views. Targets can be selected from any current view without turning first.
For act, use the current revision and target={image_ref: current image reference, point: [x,y]}. Coordinates run from 0 to 1, left to right and top to bottom. Select a visible part of the intended object. Use navigate_to for travel and exploration.
Inspect the returned images and feedback after each action. A failed action may still have moved or released something; reselect from the new images before trying again.
Use wait to let cooking or other timed processes progress. release and wait take target=null; leave unused optional arguments null.
Before finishing, check the requested objects, quantities, locations and states. Call finish with achieved, blocked or aborted and a brief reason, then wait for closed=true.
'''
BASIC_SKILL_INSTRUCTION = '''
After initialize, read visual-manipulation/SKILL.md through list_skills/read_skill. Read visual-exploration, pick-and-place and failure-recovery as needed, and visual-manipulation/references/material-actions.md before cutting or transferring material. Reread a resource only if it is no longer in context.
'''
BASIC_SKILLS_PROMPT = MINIMAL_PROMPT + BASIC_SKILL_INSTRUCTION
GOAL_TREE_PROMPT = BASIC_SKILLS_PROMPT + '''
Read goal-tree/SKILL.md. Use task_notes to maintain a hierarchy of required results, item progress and your next local actions.
'''
SKILLS_BASE_PROMPT = BASIC_SKILLS_PROMPT + '''
Use task_notes when you need persistent working memory. Keep observations, progress and uncertainties concise. Read it when context is missing; use the format required by your planning skill, if present.
'''
GOAL_SKILL_INSTRUCTION = '''
Read goal-decomposition/SKILL.md before acting. Describe the required results, observed state and available operations in task_notes. A write/update returns a proposed operation order with estimated costs and missing prerequisites. Execute the selected work using current RGB, then update what actually happened. Use select to override the proposed next operation.
'''
SKILLS_PROMPT = SKILLS_BASE_PROMPT + GOAL_SKILL_INSTRUCTION
WORKFLOW_PROMPT = BASIC_SKILLS_PROMPT + '''
Use remember and recall to retain useful observations, completed work and uncertainties across the task.
'''
SYSTEM_PROMPT = SKILLS_PROMPT


def system_prompt(profile='skills', *, goal_skill='hierarchical'):
    if goal_skill not in {'hierarchical','tree','none'}: raise ValueError('Unknown goal skill mode')
    return {'minimal':MINIMAL_PROMPT,'basic-skills':BASIC_SKILLS_PROMPT,'goal-tree':GOAL_TREE_PROMPT,
            'skills':SKILLS_BASE_PROMPT + (GOAL_SKILL_INSTRUCTION if goal_skill=='hierarchical' else ''),
            'workflow':WORKFLOW_PROMPT}[profile]


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
        loop = VisualToolLoop(model=self.model, instructions=system_prompt(harness.profile,goal_skill=harness.goal_skill),
                              request=self._request_with_observation_jobs,
                              config=LoopConfig(max_turns=self.max_turns, max_tokens=self.max_tokens,
                                                image_history_captures=image_history_captures))
        return loop.run(harness, instruction)
