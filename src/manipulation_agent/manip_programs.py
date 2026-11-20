"""Versioned model-authored geometry programs; isolated computation, no actuators."""
import ast
from copy import deepcopy
import hashlib
import json
from functools import wraps
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from .contracts import SkillError, object_schema, validate
from .model_pose_protocol import specs as pose_specs
from .posture_goals import checked_goal
from .records import write_json

ENGINE = 'model_program_v1'


def enabled():
    import os
    return os.environ.get('MAS_MANIP_ENGINE') == ENGINE


def specs(legacy):
    names = {'initialize', 'observe_scene', 'navigate_to', 'wait', 'finish', 'task_notes'}
    rows = [row for row in legacy if row['name'] in names]
    text = {'type': 'string'}
    opened = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': True}
    pose_properties = deepcopy(pose_specs([])[0]['inputSchema']['properties'])
    entries = [
        ('list_manip_references', {}, 'List existing reusable geometry implementations and original authorship. Read/reuse/adapt applicable code before inventing a replacement.'),
        ('read_manip_reference', {'reference': text}, 'Read an existing geometry implementation, exact source hash, inputs and output contract. These are harness references, never falsely attributed to the model.'),
        ('get_manip_geometry', {'object_ids': {'type': 'array', 'items': text, 'minItems': 1, 'maxItems': 8, 'uniqueItems': True}, 'revision': {'type': 'integer'}},
            'Read selected objects\' actual collision-mesh vertices and triangle faces in current robot_base. No stepping, render or control. Cached only for this revision and included in your next program input as object.collision_mesh. Use mesh sections, not AABB as exact shape.'),
        ('get_placement_state', {'object_id': text, 'container_id': text, 'revision': {'type': 'integer'}},
            'Read actual native Inside relation and native release state for an object/container pair, without stepping or rendering. A true Inside result uses the simulator predicate, not an AABB approximation; whole task scoring remains separate.'),
        ('list_manip_programs', {}, 'List named geometry programs, immutable source hashes and descriptions. Choose by task and actual geometry; all unqualified sources remain drafts.'),
        ('read_manip_program', {'program': text}, 'Read the current source and hash of a geometry program before selecting or changing it.'),
        ('write_manip_program', {'program': text, 'source': {'type': 'string', 'maxLength': 100000},
            'description': {'type': 'string', 'maxLength': 2000},
            'expected_sha256': {'type': ['string', 'null']}},
            'Write or adapt a Python geometry program defining generate(task, oracle, parameters). You may import NumPy and manip_reference.bottle_grasps. Read/reuse the catalog first; revise the same program name when improving it. Use expected_sha256=null only for a new name; existing programs need the current hash. Immutable old versions remain. The host immediately collects drafts in the configured shared program library and reports library_publication; pending means the local source is saved but shared collection needs reconciliation. Runs offline, no GPU/simulator/network. Return {phases:[...]} for a single sequence, or {candidates:[{name,score,phases:[...]}],prepare_gripper:null|{arm,command:"open"}} for ranked alternatives. All positions are EEF-link goals in input robot_base metres/xyzw. Candidate sequences use one arm and only the LAST phase may change gripper. Each phase uses the direct-pose planning/execution template: plan, validate, execute and verify it before planning the next from measured state. No motion occurs when writing.'),
        ('manip', {'mode': {'type': 'string', 'enum': ['pose', 'program']},
                   **pose_properties, 'program': text, 'source_sha256': text, 'parameters': opened},
            'Choose mode=pose for a simple lift/retract: supply arm,frame,goal,gripper,contact_target,control_steps,revision. '
            'Choose mode=program for computed grasps/placement: supply program,source_sha256,parameters,revision; include the program timing parameter. '
            'Choose positive control_steps for each phase from translation, rotation, payload and measured tracking, balancing smoothness and speed without a fixed initial value. Default model_authored_v1 uses your duration without joint-limit checks or rate extension; collisions and native tracking remain checked. '
            'The selected geometry program receives task description and current full Oracle3D plus explicitly requested meshes. It owns sampling, scores and candidate order. For each candidate in list order, cuRobo plans its first pose; a planning rejection permits the next candidate. Once the first pose passes planning and validation, that candidate is selected and execution begins. Each phase uses the same direct-pose template: plan, validate, execute open loop and verify before planning the next from actual measured joints, obstacles and payload. A later planning or execution failure returns to the model without switching candidates. Backend never invents goals or candidates. All failures or failed endpoint/retention restore the whole-call checkpoint. Lift after close uses a separate pose call and measured payload. Observer-only spectator/wrist RGB. Runtime success is not task success.'),
    ]
    for name, properties, description in entries:
        schema = object_schema(properties)
        if name == 'manip':
            schema['required'] = ['mode', 'revision']
        rows.append({'name': name, 'description': description, 'inputSchema': schema,
            'annotations': {'readOnlyHint': name.startswith(('list_', 'read_', 'get_')),
                'destructiveHint': name == 'manip', 'idempotentHint': name.startswith(('list_', 'read_', 'get_')),
                'openWorldHint': False},
            '_meta': {'mas': {'profile': 'oracle_waypoints', 'mutates_world': name == 'manip',
                'requires_fresh_observation': name == 'manip', 'closes_episode': False,
                'motion_source': ENGINE, 'goal_source': 'model_selected_or_authored_program'}}})
    return rows


def checked_invocation(arguments):
    validate(arguments, next(row['inputSchema'] for row in specs([]) if row['name'] == 'manip'))
    fields = ({'arm', 'frame', 'goal', 'gripper', 'contact_target'} if arguments['mode'] == 'pose'
              else {'program', 'source_sha256', 'parameters'})
    optional = {'control_steps'} if arguments['mode'] == 'pose' else set()
    if not fields | {'mode', 'revision'} <= set(arguments) or set(arguments) - fields - {'mode', 'revision'} - optional:
        raise SkillError('invalid_arguments', 'Supply exactly the fields for mode=' + arguments['mode'])
    if arguments['mode'] == 'pose':
        checked_goal(arguments)
    return deepcopy(arguments)


def checked_phases(result):
    """Validate the entire generated batch before any native action/setup."""
    if not isinstance(result, dict) or set(result) != {'phases'}:
        raise SkillError('invalid_program_output', 'Return exactly {phases: [...]}')
    phases = result['phases']
    if not isinstance(phases, list) or not 1 <= len(phases) <= 16:
        raise SkillError('invalid_program_output', 'Generate between one and sixteen phases')
    schema = deepcopy(pose_specs([])[0]['inputSchema'])
    schema['properties'].pop('revision'); schema['required'].remove('revision')
    schema['properties']['name'] = {'type': 'string', 'minLength': 1, 'maxLength': 80}
    schema['required'].append('name')
    checked = []
    for phase in phases:
        validate(phase, schema)
        goal = checked_goal(phase)
        if phase['gripper'] == 'close' and phase['contact_target'] is None:
            raise SkillError('invalid_program_output', 'close needs an explicit object and physical link')
        checked.append({**deepcopy(phase), 'goal': goal})
    if len({p['arm'] for p in checked}) != 1 or len({p['name'] for p in checked}) != len(checked):
        raise SkillError('invalid_program_output', 'Use one arm and unique phase names in a Manip')
    return checked


def checked_output(result):
    """Keep sequence compatibility; alternatives are ordered by the authored code."""
    if isinstance(result, dict) and set(result) == {'phases'}:
        return checked_phases(result)
    if not isinstance(result, dict) or set(result) - {'candidates', 'prepare_gripper', 'planning_profile'} or 'candidates' not in result:
        raise SkillError('invalid_program_output', 'Return phases or ranked candidates, not both')
    candidates = result['candidates']
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 256:
        raise SkillError('invalid_program_output', 'Return 1..256 ranked candidates')
    checked, names, arms = [], set(), set()
    import math
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {'name', 'score', 'phases'}:
            raise SkillError('invalid_program_output', 'Candidate needs name, score and phases')
        name, score = item['name'], item['score']
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or name in names:
            raise SkillError('invalid_program_output', 'Use unique candidate names, at most 80 characters')
        if type(score) not in (int, float) or not math.isfinite(score):
            raise SkillError('invalid_program_output', 'Candidate score must be finite')
        phases = checked_phases({'phases': item['phases']})
        if phases[0]['arm'] == 'both':
            raise SkillError('invalid_program_output', 'Whole-body recovery uses one explicit phase sequence, not EEF grasp candidates')
        if any(p['gripper'] != 'hold' for p in phases[:-1]):
            raise SkillError('invalid_program_output', 'Only the last candidate phase may change gripper; use prepare_gripper=open and a separate held-object lift')
        names.add(name); arms.add(phases[0]['arm'])
        checked.append({'name': name, 'score': score, 'phases': phases})
    if len(arms) != 1:
        raise SkillError('invalid_program_output', 'Use one arm for a ranked candidate batch')
    prepare = result.get('prepare_gripper')
    if prepare is not None and (not isinstance(prepare, dict) or
            set(prepare) != {'arm', 'command'} or prepare['arm'] not in arms or prepare['command'] != 'open'):
        raise SkillError('invalid_program_output', 'prepare_gripper must explicitly open the selected arm')
    output = {'candidates': checked, 'prepare_gripper': deepcopy(prepare)}
    if 'planning_profile' in result:
        if result['planning_profile'] != 'geometry_pick_v1':
            raise SkillError('invalid_program_output', 'Unknown planning profile')
        # The historical contact adapter supports a free single-link grasp,
        # not arbitrary contact/release sequences.
        if any(c['phases'][-1]['gripper'] != 'close' for c in checked):
            raise SkillError('invalid_program_output', 'Historical grasp profile needs terminal close')
        output['planning_profile'] = result['planning_profile']
    return output


# The model's source is never exec/imported in the simulator process.
ENTRY = '''import importlib.util,json,sys,time,signal,resource,math
started=time.perf_counter()
import numpy
sys.path.insert(0,"/reference")
import manip_reference
setup_seconds=time.perf_counter()-started
cpu_limit=math.ceil(time.process_time())+10
resource.setrlimit(resource.RLIMIT_CPU,(cpu_limit,cpu_limit))
signal.alarm(15)
started=time.perf_counter()
spec=importlib.util.spec_from_file_location("geometry_program","/program/source.py")
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
with open("/input.json") as f: data=json.load(f)
result=module.generate(data["task"],data["oracle"],data["parameters"])
with open("/output/goals.json","x") as f: json.dump(result,f,allow_nan=False)
with open("/output/runtime.json","x") as f: json.dump({"manip_reference_imported":"manip_reference" in sys.modules,"setup_seconds":setup_seconds,"computation_seconds":time.perf_counter()-started},f)
signal.alarm(0)
'''


def program_boundary(method):
    @wraps(method)
    def checked(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except SkillError:
            raise
        except Exception as exc:
            raise SkillError('geometry_program_error', f'{type(exc).__name__}: {str(exc)[:500]}') from exc
    return checked


class ManipPrograms:
    def __init__(self, root, *, library_root=None):
        self.root = Path(root)
        self.shared = None
        if library_root is not None:
            from .geometry_library import GeometryProgramLibrary
            self.shared = GeometryProgramLibrary(library_root)
            snapshot = self.root / 'library_snapshot.json'
            if not snapshot.exists():
                entries = self.shared.snapshot()
                self.root.mkdir(parents=True, exist_ok=True)
                for entry in entries:
                    folder = self.folder(entry['program'])
                    if (folder / 'current.json').exists():
                        continue  # Preserve any local development already in progress.
                    path = folder / entry['source_sha256'] / 'source.py'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.exists() and path.read_text() != entry['source']:
                        raise ValueError('Immutable geometry snapshot differs')
                    path.write_text(entry['source'])
                    write_json(folder / 'current.json', {k: v for k, v in entry.items() if k != 'source'})
                write_json(snapshot, {'library_root': str(self.shared.library.repository),
                    'programs': [entry['library_release'] for entry in entries]})

    def folder(self, name):
        if not isinstance(name, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) is None:
            raise SkillError('invalid_program', 'Use a lowercase program identifier, at most 64 characters')
        path = self.root / name
        if self.root.is_symlink() or path.is_symlink():
            raise SkillError('invalid_program', 'Program directories cannot be symlinks')
        return path

    @program_boundary
    def read(self, name):
        folder = self.folder(name)
        if not (folder/'current.json').exists():
            from .manip_builtin import read
            entry = read(name)
            if entry is not None:
                return entry
        try:
            row = json.loads((folder / 'current.json').read_text())
            sha = row['source_sha256']
            if re.fullmatch(r'[0-9a-f]{64}', sha) is None:
                raise ValueError('Invalid source hash')
            source = (folder / sha / 'source.py').read_text()
            if hashlib.sha256(source.encode()).hexdigest() != sha:
                raise ValueError('Program source hash mismatch')
        except (OSError, ValueError, KeyError) as exc:
            raise SkillError('unknown_program', 'Missing or invalid geometry program: '+name) from exc
        return {**row, 'source': source,
                'validation': row.get('validation', 'No independent physical qualification implied')}

    @program_boundary
    def catalog(self):
        from .manip_builtin import catalog
        names = {entry['program'] for entry in catalog()}
        if self.root.exists():
            names.update(path.name for path in self.root.iterdir() if path.is_dir())
        return [{k: v for k, v in self.read(name).items() if k != 'source'} for name in sorted(names)]

    @program_boundary
    def write(self, program, source, description, expected_sha256):
        folder = self.folder(program)
        if not isinstance(source, str) or len(source.encode()) > 100000:
            raise SkillError('invalid_program', 'Python source must fit 100000 bytes')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise SkillError('invalid_program', str(exc)) from exc
        if not any(isinstance(n, ast.FunctionDef) and n.name == 'generate' for n in tree.body):
            raise SkillError('invalid_program', 'Define generate(task, oracle, parameters)')
        from .manip_builtin import read as read_builtin
        builtin = read_builtin(program)
        previous = self.read(program) if (folder/'current.json').exists() or builtin else None
        current = previous['source_sha256'] if previous else None
        if current != expected_sha256:
            raise SkillError('program_version_conflict', 'Read the current source hash before replacing a program')
        if builtin is not None:
            old = folder/builtin['source_sha256']/'source.py'
            old.parent.mkdir(parents=True, exist_ok=True)
            if not old.exists():
                old.write_text(builtin['source'])
        sha = hashlib.sha256(source.encode()).hexdigest()
        destination = folder / sha
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / 'source.py'
        if path.exists() and path.read_text() != source:
            raise SkillError('program_version_conflict', 'Immutable source differs')
        if not path.exists():
            path.write_text(source)
        row = {'program': program, 'source_sha256': sha, 'description': description,
               'author': previous['author'] if previous and sha == current else 'model', 'status': 'draft',
               'provenance': {'episode_program_root': str(self.root.resolve()),
                   'parent_source_sha256': current,
                   'parent_author': previous['author'] if previous else None}}
        write_json(folder / 'current.json', row)
        if self.shared is not None:
            pending = (previous or {}).get('library_publication', {})
            expected = pending.get('expected_sha256', current) if pending.get('status') == 'pending' else current
            try:
                published = self.shared.publish({**row, 'source': source}, expected)
                row = {k: v for k, v in published.items() if k != 'source'}
                row['library_publication'] = {'status': 'published'}
            except Exception as error:
                row['library_publication'] = {'status': 'pending', 'expected_sha256': expected,
                    'error': str(error)[:1000], 'local_source_preserved': True}
        else:
            row['library_publication'] = {'status': 'not_configured'}
        write_json(folder / 'current.json', row)
        return row

    @program_boundary
    def generate(self, program, source_sha256, *, task, oracle, parameters, output):
        entry = self.read(program)
        identity = {key: deepcopy(entry[key]) for key in
                    ('version', 'status', 'provenance', 'library_release') if key in entry}
        if entry['source_sha256'] != source_sha256:
            raise SkillError('program_version_conflict', 'Program changed; select its current source hash')
        from .manip_builtin import read as read_builtin
        builtin = read_builtin(program)
        trusted = (builtin is not None and entry['author'] == 'harness_reference'
                   and entry['source_sha256'] == builtin['source_sha256'])
        bwrap, limiter = shutil.which('bwrap'), shutil.which('prlimit')
        if not trusted and (not bwrap or not limiter):
            raise SkillError('program_sandbox_unavailable', 'bubblewrap and prlimit are required; no in-process fallback')
        run = Path(output) / ('geometry_program_' + uuid.uuid4().hex)
        exports = run / 'output'; exports.mkdir(parents=True)
        (run / 'entry.py').write_text(ENTRY)
        from .manip_references import source as reference_source, catalog as reference_catalog
        references = run / 'reference'; references.mkdir()
        (references / 'manip_reference.py').write_text(reference_source())
        write_json(run / 'input.json', {'task': task, 'oracle': oracle, 'parameters': parameters})
        program_dir = run/'selected_program'; program_dir.mkdir()
        (program_dir/'source.py').write_text(entry['source'])
        if trusted:
            # Fixed operator-owned callable only. Model edits, including an
            # override with the same name, must still use the isolated runner.
            # This restores the historical in-process NumPy/sampler path.
            from .geometry_programs.bottle_grasps import generate
            started = time.perf_counter()
            try:
                goals = generate(task, deepcopy(oracle), deepcopy(parameters))
                write_json(exports / 'goals.json', goals)
                phases = checked_output(goals)
            except Exception as exc:
                write_json(run / 'result.json', {'ok': False, 'source_sha256': source_sha256,
                    'runtime': 'trusted_builtin_in_process', 'error': str(exc)})
                raise SkillError('geometry_program_failed', str(exc)[:1000]) from exc
            evidence = {**identity, 'program': program, 'source_sha256': source_sha256,
                'author': entry['author'], 'input': str(run / 'input.json'),
                'output': str(exports / 'goals.json'),
                'sandbox': 'trusted_builtin_in_process', 'reference_imported': False,
                'reference_used': True, 'available_references': reference_catalog(),
                'computation_seconds': time.perf_counter() - started,
                **({'phases': phases} if isinstance(phases, list) else phases)}
            write_json(exports / 'runtime.json', {'runtime': 'trusted_builtin_in_process',
                'manip_reference_imported': False, 'numpy_reused': True})
            write_json(run / 'result.json', {'ok': True, **evidence})
            return phases, evidence
        # Immutable source mount, input-only Oracle, output-only result. No robot
        # socket, credentials, repository, NAS, GPU or network in the child.
        cmd = [bwrap, '--die-with-parent', '--cap-drop', 'ALL', '--unshare-net', '--unshare-pid',
               '--ro-bind', '/usr', '/usr', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
        for name in ('bin', 'sbin', 'lib', 'lib64'):
            path = Path('/') / name
            if path.is_symlink():
                import os
                cmd += ['--symlink', os.readlink(path), str(path)]
            elif path.exists():
                cmd += ['--ro-bind', str(path), str(path)]
        cmd += ['--ro-bind', str(program_dir.resolve()), '/program',
                '--ro-bind', str(references.resolve()), '/reference',
                '--ro-bind', str((run / 'input.json').resolve()), '/input.json',
                '--ro-bind', str((run / 'entry.py').resolve()), '/entry.py',
                '--bind', str(exports.resolve()), '/output', '--chdir', '/output',
                '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                '--setenv', 'OPENBLAS_NUM_THREADS', '1', '--setenv', 'OMP_NUM_THREADS', '1',
                '--setenv', 'MKL_NUM_THREADS', '1']
        # Numerical geometry uses the already installed interpreter/NumPy. This
        # read-only environment mount exposes no task workspace, credentials or
        # native scene. GPU devices and network remain absent.
        prefix = str(Path(sys.prefix).resolve())
        if prefix not in ('/usr', '/'):
            cmd += ['--ro-bind', prefix, prefix]
        cmd += [sys.executable, '-I', '/entry.py']
        cmd = [limiter, '--cpu=120', '--as=1073741824', '--nofile=64', '--fsize=1048576', '--', *cmd]
        try:
            with (run / 'stdout.log').open('w') as out, (run / 'stderr.log').open('w') as err:
                process = subprocess.run(cmd, stdout=out, stderr=err, timeout=135)
            if process.returncode != 0:
                raise ValueError('Geometry program failed; see '+str(run / 'stderr.log'))
            goals = json.loads((exports / 'goals.json').read_text())
            phases = checked_output(goals)
        except (OSError, ValueError, subprocess.TimeoutExpired, SkillError) as exc:
            write_json(run / 'result.json', {'ok': False, 'source_sha256': source_sha256, 'error': str(exc)})
            raise SkillError('geometry_program_failed', str(exc)[:1000]) from exc
        runtime = json.loads((exports/'runtime.json').read_text())
        evidence = {**identity, 'program': program, 'source_sha256': source_sha256, 'author': entry['author'],
                    'input': str(run / 'input.json'), 'output': str(exports / 'goals.json'),
                    'sandbox': 'bubblewrap_offline_no_gpu_no_robot',
                    'reference_imported': runtime['manip_reference_imported'],
                    'setup_seconds': runtime['setup_seconds'],
                    'computation_seconds': runtime['computation_seconds'],
                    'available_references': reference_catalog(),
                    **({'phases': phases} if isinstance(phases, list) else phases)}
        write_json(run / 'result.json', {'ok': True, **evidence})
        return phases, evidence


SYSTEM_PROMPT = '''You own geometry algorithm development and selection for R1Pro using task descriptions and Oracle3D.
Call initialize. First judge whether the Manip task is simple or complex. For a simple
lift/retract/free-space move, directly supply the goal waypoint with mode=pose.
For a complex task, list/read the program catalog and select applicable existing code.
The catalog includes drafts frozen from the shared library: reuse them when applicable;
draft is a validation label, not a reason to rewrite or exclude a program. When a
program needs improvement, read it and update the SAME name with expected_sha256.
Create a new name only for a different capability, not for each retry. Each write
is collected by the host into the shared library without waiting for certification;
check library_publication and report a pending save rather than assuming it persisted.
Local snapshots keep active runs stable; later episodes receive shared updates.
For a user-saved whole-body recovery, read/reuse the matching recovery program.
It may emit a Pose phase with arm="both", goal={joint_positions_rad:{all18 torso
and arm joints: radians}}, gripper="hold", contact_target=null. This is an explicit
robot-relative joint reference, not an object/world coordinate or simulator reset.
cuRobo plans torso and both arms together with actual payloads in either/both hands;
base and grippers stay at their current state. Verify actual joint posture and
retention. Do not replace it with a right-hand-only EEF target or invent a new
reference on every use. Whole-body goals use a phase sequence, not grasp candidates.
Only write a new program when no existing program fits. Take over the developer's
work: inspect geometry and robot conventions, generate appropriate poses, inspect
planning failures and improve the algorithm or empty-hand station. A program may
produce one ordered phase sequence or ranked alternative candidates; these are
different concepts. Do not require a fixed candidate count or multiple alternatives
for every complex task. When grasp sampling is appropriate, generate and rank
plausible candidates rather than reducing the sampler to one guessed point.
Use list_manip_references/read_manip_reference to inspect the existing bottle_grasps
implementation and its limits. It is a harness-authored reference; retain authorship.
You may import it with `from manip_reference import bottle_grasps`, adapt its source,
or replace it. list/read/write_manip_program manage your selected program and immutable
source hashes; code generation and appropriate reuse are both part of your job.
The catalog includes bottle_grasps and place_in_open_bin. For open-bin placement,
read place_in_open_bin and prefer its direct candidate goal from fresh payload/bin
meshes, measured retention and your control_steps estimate. Set release=true only
when intending release; wait and query Inside/released after execution.
The program catalog already includes an existing bottle_grasps program. If it fits,
read it and call manip(mode="program",program="bottle_grasps",source_sha256,parameters,
revision) directly. You need not write a wrapper. Write/adapt a program only when none fits.
Before mesh-based grasping, call get_manip_geometry(object_ids,revision). It reads actual
collision vertices/faces without stepping or rendering, and includes object.collision_mesh
in your program's input for THIS revision. get_oracle_geometry supplies fingers/body/links.
Navigation or physical motion invalidates the mesh cache: query again at the new revision.
Define generate(task, oracle, parameters) using Python, NumPy and optional manip_reference.
All geometry and goals use INPUT robot_base, metres, quaternion xyzw, EEF-LINK origin.
Use measured finger contact offset and aperture; distinguish +Z finger approach from
jaw closing. Sample object sections, approach azimuths and tilts as appropriate, reject
incompatible jaw widths, and score/order candidates in YOUR code. No hard-coded episode world/object coordinates;
explicitly user-saved robot joint recovery references are allowed.
Return {"candidates":[{"name":...,"score":...,"phases":[...]}],
"prepare_gripper":null|{"arm":"left|right","command":"open"}} for alternatives.
Each phase has name,arm,frame="robot_base",goal={position_m,quaternion_xyzw},
gripper="hold|open|close",contact_target=null|{object_id,link_name}, and optional positive
integer control_steps, estimated by YOU for each phase (30Hz, excluding gripper). Use one arm and
unique names. Only the LAST phase in a candidate may change gripper; explicitly request
prepare_gripper=open if needed, then use hold for pregrasp and close at contact.
The backend does not invent or rank candidates. cuRobo checks each candidate's FIRST
pose in your list order. If its planning/validation fails before candidate execution,
the next candidate may be tried. As soon as the first pose passes, that candidate
is selected and the pose is executed immediately. Every phase uses the SAME direct-pose
template: plan/validate, execute its whole joint trajectory open loop, then verify
its endpoint and native retention before planning the next pose from ACTUAL measured
joints, current obstacles and payload. Goals stay in the original INPUT robot_base
frame. The next phase is not preplanned from the previous predicted joint endpoint.
Only the terminal candidate phase may change the gripper, after reaching its goal.
Every rejection, executed phase and the chosen candidate are recorded and returned.
Any later planning/execution/verification failure stops the sequence, rolls back the
entire Manip and returns to YOU; it never silently retries another physical grasp.
For a SIMPLE lift, retract or free-space move, directly call manip(mode="pose",arm,
frame="robot_base",goal,gripper,contact_target,revision), without writing a program.
Plan lift after close using actual native retention and measured carried geometry.
Return {"phases":[...]} for a single sequential program when that is appropriate;
these phases are planned as reached, including any changed payload. No candidate fallback is implied.
Select your code with manip(mode="program",program,source_sha256,parameters,revision).
No simulator/socket/network/GPU/host task files are available inside geometry code.
For EEF goals, base and other arm are locked in cuRobo; torso and selected arm may move. No automatic
lift, body-restoration goal or bottle-specific target replacement. Under
model_authored_v1, torso/arm position limits are disabled throughout planning,
execution and native simulation, including when selecting geometry_pick_v1.
No extra joint-angle, torso-tilt or torso-height constraints apply.
When walking with a held object, prefer the torso as close to world-vertical as
safely feasible, and maintain that transport posture. Before travel, combine lift,
arm retraction and torso recovery into a small number of explicitly chosen Manip
moves where feasible. Recheck measured posture, native retention and current full
robot/payload clearance before navigating. If standing upright would compromise
collision safety or retention, choose the closest safe posture and
record the reason in task_notes. This is a planning preference, not a fixed torso
angle/height gate or automatic pose projection.
Use model_authored_v1 timing. Choose a positive integer control_steps for EVERY
phase/pose yourself from its translation, rotation, payload and actual tracking
feedback at30Hz. Balance smoothness and speed; there is no fixed initial value
and no preference for requesting the smallest possible duration. The backend uses
your duration without extending it for joint rates, then densely rechecks collisions.
Planning/execution joint position, velocity, acceleration and jerk limits are disabled;
Native torso/arm position limits and native controller position-command clamps are
also disabled; initialize reports the actually applied policy. Native motor dynamics
and real collision checks remain.
Keep as few necessary task phases as possible, without dropping endpoints or gripper
actions. Do not omit timing or use a fixed100
cap. Read the selected program's parameter contract and pass its timing parameter:
for recover_body pass your estimate in parameters.control_steps; for bottle_grasps
pass your separate pregrasp/contact_close estimates in parameters.control_steps. Omitted estimates retain
the planner duration. Historical/explicit_fast_v1/model_estimated_v1
policies are available only when explicitly selected by the runtime. After a
geometry_pick_v1 native grasp, lift retains that planner and may use its certified
vertical support-departure path. Under model_authored_v1 it uses your duration
without joint-rate limits, and its newly timed dense
interpolants must pass the support-departure collision certificate before execution.
For placement, compute the desired held-object pose over the actual opening and
convert it to an EEF goal using the measured hand-object transform. Prefer a
reachable navigation station and a direct goal before inventing many intermediate
poses. Choose orientation from the task requirements instead of always copying the
current wrist quaternion. A failed timing check is not evidence that IK failed or
joints cannot bend.
no path truncation. Native gripper control30 steps is separate, no90-step hold.
Execution cameras are spectator/left_wrist/right_wrist for observation only.
Successful IK/trajectory/execution is not native retention or official task success.
'''
