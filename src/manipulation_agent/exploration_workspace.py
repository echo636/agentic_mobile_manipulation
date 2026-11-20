"""RAX-style task workspace and immutable controller bundles; simulator-free."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import shutil

WORKFLOW = """# Exploration agent
Read TASK.md, MCP_SESSION.md, memory/PROGRESS.md and memory/INDEX.md, then status.
Call take_notes({}) on startup, resume and after context compaction. It is the
durable continuation summary, not evaluator truth. Replace it (content plus
expected_version) after each meaningful result, code milestone or failed hypothesis:
task constraints; actual live episode/held objects/revision (stale until refreshed);
completed/pending operation IDs; code/skill paths; concise evidence and failures;
next step. Keep it under 6000 characters. Do not copy complete code/logs/geometry.
Writing notes does not erase chat history; the client separately compacts at its
configured threshold. After compaction read notes and status before continuing;
never repeat an in-flight/completed action because its chat output disappeared.
After an observable subtask succeeds at a safe boundary, update take_notes with
the current revision, then call checkpoint_subtask with concise postconditions and
disk evidence references. Do not checkpoint a plan, a failed attempt or an
ambiguous result. A successful checkpoint ends this conversation: immediately
return your final response and call no more tools. The host starts a FRESH model
thread on the same live episode using notes, status, current observation and only
the relevant skill/code. Full chat and logs remain on disk and are not reloaded.
Await MCP tools, including long run_development/rehearse, in one functions.exec
call. The host's configured default yield waits for completion. Do not override
it with short yield_time_ms pragmas, call yield_control, or poll functions.wait.
Host waits need no periodic model commentary; the operator monitors local files.
Robot wait advances simulation and is only for physical settling, not host polling.
Read file slices and use patch_workspace_file with the current hash for small
edits. Query only needed object_fields and robot_sections, preserving precision
for contact and collision checks. Images may be delivered on demand: use
get_cached_rgb at the current revision to inspect relevant wrist/surround views
before contact planning and after failures. This reads existing frames, not a render.
Use the existing episode after a resume. First explore with current observations;
record observable preconditions, effects, failures and decisive frame references.
Do not load complete histories or image bytes into notes.
Write code/<project>/controller.py with main(ctx). Use ctx.call(tool, **arguments);
the supplied context serializes robot calls and supplies current revisions.
Context also supports get_oracle_geometry/set_oracle_focus from the cached
current observation, without advancing physics or attaching RGB again.
Geometry must be reacquired, never replay a previous episode's world coordinates.
Separate perception, checked motions and the task script. Stop at the first failed
or ambiguous check. Do not release a retained object merely to clear an error.
Package new reusable procedures as Manip(ctx, recipe=..., targets=...,
parameters=..., constraints=..., revision=...). Each recipe declares typed inputs,
defaults, units, supported scope, observable pre/postconditions and failure output.
Keep main(ctx) as the thin task entrypoint; keep internal move_joints/grasp/wait
calls visible in the normal ledger. Resolve EntityRef task bindings from current
geometry. Default to local manipulation (allow_base_motion=False); any optional
empty approach must be explicit and labeled ideal/kinematic. Never navigate held.
Reject invalid parameters before motion and respect trunk/base permissions. A
recipe is executable code, not a promise that the planner invents a grasp. Record
adapted code and manual refactors honestly; each changed source starts as draft.
Use list_manip_recipes and read_manip_recipe to discover seeded recipe projects.
After empty navigate_to, inspect current RGB/geometry and choose manip(recipe,
targets, parameters, constraints, revision, allow_draft=true). Keep base motion off
inside local Manip. Catalog entries give the workspace project and controller path.
On failure preserve the scene, read its failed phase and current wrist RGB/geometry,
form a concrete hypothesis, patch that recipe at its file hash, and test again.
Do not blindly repeat unchanged failures or reset the scene. Use native tools only
when no recipe applies or deliberate recovery is needed; never hide extra actions.
After development success, freeze a complete navigate+Manip main(ctx) in one project
(include the recipe's Manip implementation and spec.json there). Call
ctx.call('manip', recipe=..., targets=..., revision=..., allow_draft=True) after
empty navigation. In rehearsal/formal this tool is bound only to that exact
read-only bundle, never the mutable workspace library. The broker records one
parent call with its move_joints/grasp/wait children, enforcing recipe constraints.
Finish development before independent rehearsal. New pipeline sessions require two
successful unattended rehearsals of the EXACT bundle/runner under the operator's
independent qualification metric. Check status for counts/reasons; clean exit or
Manip ok alone never counts. After any code edit register a new bundle and start its
qualification count at zero. On rehearsal failure inspect evidence, resume deliberate
development and repair, then freeze again. Never optimize from formal test records.
Register freezes code; rehearse runs that exact bundle in a NEW independent episode
without your intervention. An edit needs a new registration and rehearsal.
A clean rehearsal is submission qualification, NOT proof of task success.
Submit the exact rehearsed bundle. Formal evaluation runs frozen code alone on
the operator's disjoint held-out schedule. Do not inspect or modify formal records.
Never import the simulator or access it outside ctx. No arbitrary shell reaches
the robot. Keep notes and code in this workspace; credentials and service state
belong to the host. Finish exploration when requested; never claim untested success.
This is Oracle + native assisted manipulation unless TASK.md states otherwise,
NOT RAX's RGB-D benchmark. Existing ideal navigation must remain labeled.
"""
def sha(data):
    return hashlib.sha256(data).hexdigest()

def safe_tree(root: Path):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Source must be a regular project directory")
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError("Controller bundles cannot contain symlinks")
        if p.is_dir():
            continue
        if not p.is_file() or p.stat().st_size > 2_000_000:
            raise ValueError("Unsupported/oversized controller file")
        if len(files) >= 200:
            raise ValueError("Too many controller files")
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        data = p.read_bytes()
        if p.suffix == ".py":
            ast.parse(data, filename=rel)
        files[rel] = sha(data)
    if "controller.py" not in files:
        raise ValueError("Expected controller.py:main(ctx)")
    tree = ast.parse((root / "controller.py").read_bytes())
    if not any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body):
        raise ValueError("Expected top-level main(ctx)")
    return files

def tree_hash(files):
    return sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())

def create_workspace(root, task, tools):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    for name in ("code", "experiments", "memory", "output", "api"):
        (root / name).mkdir()
    (root / "AGENTS.md").write_text(WORKFLOW)
    (root / "TASK.md").write_text(task + "\n")
    (root / "MCP_SESSION.md").write_text(
        "# Control contract\nUse live status and tool schemas. Oracle geometry is privileged.\n"
        "Actions block in Context until complete; do not poll from the VLM.\n"
        "No action/physics/wall deadline is added by this workflow.\n\n" +
        json.dumps(tools, ensure_ascii=False, indent=2) + "\n")
    (root / "memory/PROGRESS.md").write_text("# Progress\nNo exploration evidence yet.\n")
    (root / "memory/INDEX.md").write_text("# Learned procedures\nNo verified procedures yet.\n")
    (root / "memory/TIPS.md").write_text("# Tips\nRecord evidence-backed relative recipes.\n")
    (root / "api/__init__.py").touch()
    shutil.copyfile(Path(__file__).with_name("exploration_context.py"), root / "api/runtime.py")

def runner_fingerprint():
    root = Path(__file__).parent
    names = [p for p in root.glob("exploration_*.py")] + [root/"oracle_presentation.py", root/"bridge.py",
                                                         root/'manip_runtime.py', root/'manip_acceptance.py',
                                                         root/'contracts.py', root/'oracle_contracts.py', root/'oracle_harness.py']
    return tree_hash({p.name: sha(p.read_bytes()) for p in sorted(names)})

def freeze_project(workspace, project, bundle_root):
    if not isinstance(project, str) or not project or "/" in project or project in (".", ".."):
        raise ValueError("Use one project name under code/")
    source = Path(workspace) / "code" / project
    files = safe_tree(source)
    code_hash = tree_hash(files)
    runner_hash = runner_fingerprint()
    digest = sha((code_hash + runner_hash).encode())
    target = Path(bundle_root) / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.mkdir()
        for rel in files:
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, p)
    if safe_tree(target) != files:
        raise ValueError("Frozen source changed")
    return {"bundle": digest, "code_sha256": code_hash, "runner_sha256": runner_hash, "project": project, "files": files}
