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


TOOL_INTENT_PROMPT = '''Every tool call must include intent: one concise public sentence stating the immediate purpose of this particular call. This includes initialize, look, act, list_skills, read_skill, task_notes and finish, and any other enabled tool. Intent is a required tool argument, not a replacement for task_notes or an occasional progress message. Base it on the task and evidence already available; before initialize, state what you intend to inspect without inventing scene facts. Do not provide a private reasoning transcript.
'''

def _replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Inline skill rewrite no longer matches its source text: ' + old[:60])
    return text.replace(old, new)


# skills_inline: the skill files the skills prompt requires before acting. In 59 recorded
# Codex episodes these were read in 46-55 episodes; failure-recovery (8),
# visual-exploration (1) and evidence.md (0) stay out. Rewrites remove read_skill steps.
INLINE_SKILL_RESOURCES = (
    ('visual-manipulation', 'SKILL.md', [(
        'Read each relevant resource once; reread only if its instructions are no longer available in context:\n\n'
        '- `visual-exploration`: finding objects and traveling between areas.\n'
        '- `pick-and-place`: carrying, placing and attaching objects.\n'
        '- [references/material-actions.md](references/material-actions.md): cleaning, treating, soaking or cutting, before using those actions.\n'
        '- `failure-recovery`: diagnosing a rejected or partially executed action.\n'
        '- [references/evidence.md](references/evidence.md): deciding whether uncertain results support completion.\n\n',
        '')]),
    ('visual-manipulation', 'references/material-actions.md', []),
    ('pick-and-place', 'SKILL.md', []),
    ('goal-decomposition', 'SKILL.md', [(
        'Read [references/working-state.md](references/working-state.md) for the format before the initial write.',
        'Use the format in goal-decomposition/references/working-state.md below for the initial write.')]),
    ('goal-decomposition', 'references/working-state.md', []),
)
# Rewrites follow the 2026-11 prompt layout: one skill-reading paragraph, one planning paragraph.
INLINE_PROMPT_REWRITES = [
    (BASIC_SKILL_INSTRUCTION, '\nFollow the workflow guidance included at the end of these instructions.\n'),
]
INLINE_GOAL_REWRITE = ('Read goal-decomposition/SKILL.md before acting.',
                       'Follow the included goal-decomposition guidance before acting.')


def inline_skill_guidance(goal_skill='hierarchical'):
    from .skill_runtime import skill_root
    root, sections = skill_root(), []
    for name, resource, rewrites in INLINE_SKILL_RESOURCES:
        if name == 'goal-decomposition' and goal_skill == 'none':
            continue
        text = (root / name / resource).read_text()
        if text.startswith('---\n'):
            text = text.split('\n---\n', 1)[1]
        for old, new in rewrites:
            text = _replace_once(text, old, new)
        sections.append(f'=== {name}/{resource} ===\n{text.strip()}\n')
    return ('\nWorkflow guidance, included in full. There are no skill-reading tools.\n\n' + '\n'.join(sections))


def system_prompt(profile='skills', *, goal_skill='hierarchical', require_tool_intent=False):
    if goal_skill not in {'hierarchical','tree','none'}: raise ValueError('Unknown goal skill mode')
    if profile == 'skills_inline':
        prompt = SKILLS_BASE_PROMPT
        for old, new in INLINE_PROMPT_REWRITES:
            prompt = _replace_once(prompt, old, new)
        if goal_skill == 'hierarchical':
            prompt += _replace_once(GOAL_SKILL_INSTRUCTION, *INLINE_GOAL_REWRITE)
        prompt += inline_skill_guidance(goal_skill)
    else:
        prompt = {'minimal':MINIMAL_PROMPT,'basic-skills':BASIC_SKILLS_PROMPT,'goal-tree':GOAL_TREE_PROMPT,
                  'skills':SKILLS_BASE_PROMPT + (GOAL_SKILL_INSTRUCTION if goal_skill=='hierarchical' else ''),
                  'workflow':WORKFLOW_PROMPT}[profile]
    if require_tool_intent:
        intent = TOOL_INTENT_PROMPT
        if profile == 'skills_inline':
            intent = _replace_once(intent, 'list_skills, read_skill, ', '')
        prompt = intent + prompt.replace(
            'initialize({})', 'initialize(intent="Inspect the initial four views to locate the task-relevant items.")')
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
        loop = VisualToolLoop(model=self.model, instructions=system_prompt(harness.profile,goal_skill=harness.goal_skill,
            require_tool_intent=getattr(harness, 'require_tool_intent', False)),
                              request=self._request_with_observation_jobs,
                              config=LoopConfig(max_turns=self.max_turns, max_tokens=self.max_tokens,
                                                image_history_captures=image_history_captures))
        return loop.run(harness, instruction)
