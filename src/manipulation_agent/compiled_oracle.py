"""Explicit executable-experience profile. Existing Oracle/RGB catalogs stay intact."""
import copy
import json

from .contracts import INT, STRING, SkillError, object_schema
from .oracle_contracts import ACTION_TOOLS, tool_specs as waypoint_specs
from .oracle_harness import OracleWaypointHarness
from .program_library import ProgramLibrary, configured_program_root
from .program_runtime import run_program
from .records import write_json

SYSTEM_PROMPT = '''You control R1Pro in an explicitly privileged Oracle 3D experiment using
six-view RGB, official IK, native assisted retention and frozen executable experience.
This profile evaluates a policy that selects code skills; it is not the original
all-waypoints-model-authored protocol or an RGB benchmark.
First initialize. Read the exact task experience and relevant action documents using
list_skills/read_skill. Use list_programs/read_program to inspect the frozen code skill
contracts, status and parameter schemas. Reacquire every object and geometric anchor
from the current episode. Never replay historical world coordinates or object IDs.
Before your first physical action, including execute_skill, use task_notes to write a
brief plan with the current objective, intended approach and next check. Use
initialize.notes.version or the latest task_notes version as expected_version when
writing; read with content=null and expected_version=null if needed. Update the notes
after meaningful successes, failures or strategy changes, recording evidence and the
next step. Keep them concise; do not repeat long notes after every action. Notes are
your working text, not evaluator truth; they consume no physics or action step.
Choose the target, hand, empty-robot approach station and any calibrated object-frame
parameters yourself from current RGB and Oracle geometry. Use current finger offsets
and active wrist images for contact alignment. Execute a short safe pre-contact motion,
inspect the returned wrist RGB and refine contact before grasping. Native assisted
retention must identify the intended object AND movable link before using a door skill.
Prepare stable start states with the native waypoint/grasp tools. Then execute_skill
can perform several native operations without model waits between them. Code refreshes
geometry and checks each result. Draft code is available for explicitly requested
code-skill experiments via allow_draft=true; it has no verified robot-performance claim.
On code failure inspect execution.why, current RGB, contacts and held_object. Correct
the cause or finish blocked; do not repeatedly invoke the same failed motion. A failure
can leave something retained. Do not drop an object to clear an error. Only if
control.transport_policy is hybrid_legacy_navigation may you call navigate_to while
holding. Record such travel in task_notes as ideal transport, not native carrying;
legacy navigation projects the carried-object pose. Under native_only, do not navigate
while holding. Recheck retention and fresh geometry after permitted transport. Code
skills still perform native local manipulation only; choose navigation yourself.
All manual poses are current robot_base EEF-LINK positions in metres and normalized
xyzw quaternions. IK is not collision-aware. Prepare bent ready arms in clear space;
trunk motion changes both hands/cameras. Refresh geometry after base or trunk changes.
For manual grasp/place/rotate use your own waypoint arrays with gripper=hold. Anchored
handles need joint-consistent probes, not free-object vertical lifts. Code-generated
waypoints are explicitly labeled and recorded as frozen_executable_experience.
A code ok result is local checks only. In particular a place stable flag covers its
last 10 samples; door hold endpoints do not prove all 60 intermediate angles. Passive
traces need independent review. Inspect the final images and evidence, then use finish
with achieved, blocked or aborted and receive closed=true. Official whole-task success
is scored independently. No shell, arbitrary code execution, reset or state setter is
available through tools. There is no episode time or action-count deadline.
'''


def tool_specs():
    result = copy.deepcopy(waypoint_specs())
    for row in result:
        row['_meta']['mas']['profile'] = 'oracle_compiled'
    rows = [
        ('list_programs', 'List the frozen executable skill library, versions and verification status.', {}),
        ('read_program', 'Read an executable skill contract, its parameter schema and evidence limits.', {'name': STRING}),
        ('execute_skill', 'Run one frozen trusted code skill on the simulator owner thread. Recomputes current geometry and stops on first failed check. parameters_json encodes the object schema returned by read_program. Draft code needs allow_draft=true. This is code-generated motion, not a model-authored waypoint benchmark.',
         {'name': STRING, 'parameters_json': {'type': 'string', 'maxLength': 16000},
          'allow_draft': {'type': 'boolean'}, 'revision': INT}),
    ]
    for name, description, fields in rows:
        result.append({'name': name, 'description': description, 'inputSchema': object_schema(fields),
            'annotations': {'readOnlyHint': name != 'execute_skill', 'destructiveHint': name == 'execute_skill',
                            'idempotentHint': name != 'execute_skill', 'openWorldHint': False},
            '_meta': {'mas': {'profile': 'oracle_compiled', 'mutates_world': name == 'execute_skill',
                             'requires_fresh_observation': name == 'execute_skill', 'closes_episode': False,
                             'motion_source': 'frozen_executable_experience'}}})
    return result


class CompiledOracleHarness(OracleWaypointHarness):
    PROFILE = 'oracle_compiled'
    ACTION_TOOLS = (*ACTION_TOOLS, 'execute_skill')

    def __init__(self, backend, recorder, budget, *, program_root=None, **kwargs):
        contract = backend.control_contract()
        if contract.get('ik_backend', 'omnigibson') != 'omnigibson' or contract.get('grasping_mode', 'assisted') != 'assisted':
            raise ValueError('oracle_compiled requires official OmniGibson IK and native assisted grasping')
        super().__init__(backend, recorder, budget, **kwargs)
        root = program_root if program_root is not None else configured_program_root()
        self.programs = ProgramLibrary(root).freeze(recorder.output) if root else {}
        recorder.run['config'].update(motion_source='model_tools_plus_frozen_executable_experience',
            validation_level='compiled_oracle_research_requires_independent_validation')
        write_json(recorder.output / 'run.json', recorder.run)

    def initialize(self):
        packet = super().initialize()
        packet['programs'] = self.program_catalog()
        packet['control'].update(protocol='oracle_compiled_v1', observation_protocol='oracle_3d_waypoints_v2',
            operation_motion_source='model EEF paths and explicitly selected frozen executable skills')
        packet['control']['compiled_skills'] = {'enabled': True, 'motion_source': 'frozen_executable_experience',
            'draft_requires_explicit_opt_in': True, 'failure': 'stop and return latest observation; no automatic drop'}
        return packet

    def program_catalog(self):
        return [{k: v for k, v in entry.items() if k not in {'directory', 'parameters', 'runtime_files'}}
                for entry in self.programs.values()]

    def _dispatch_tool(self, context, name, arguments):
        if name == 'list_programs':
            return {'programs': self.program_catalog()}
        if name == 'read_program':
            entry = self.programs.get(arguments['name'])
            if not entry:
                raise SkillError('unknown_program', 'Choose from list_programs')
            return {'name': entry['name'], 'status': entry['status'], 'version': entry['version'],
                    'text': (entry['directory'] / 'SKILL.md').read_text(), 'parameters': entry['parameters'],
                    'code_sha256': entry['code_sha256'], 'snapshot_sha256': entry['snapshot_sha256'],
                    'runtime_sha256': entry['runtime_sha256']}
        if name == 'execute_skill':
            if arguments['revision'] != self.revision:
                raise SkillError('stale_observation', 'Use the latest returned observation revision')
            try:
                parameters = json.loads(arguments['parameters_json'])
            except ValueError as exc:
                raise SkillError('invalid_arguments', 'parameters_json must encode the documented object') from exc
            return run_program(self, self.programs, arguments['name'], parameters,
                               context.request_id, arguments['allow_draft'])
        return super()._dispatch_tool(context, name, arguments)
