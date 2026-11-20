"""Stage a published RAX controller in a fresh prepared session; no model/robot calls."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import write_json
from manipulation_agent.skill_git import SkillGit


def stage(library, session, name, project, *, allow_draft=False):
    if any(not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value) for value in (name, project)):
        raise ValueError('Use lowercase names without path components')
    library, session = Path(library).resolve(), Path(session).resolve()
    with (library / '.library.lock').open('a') as library_lock, \
         (session / 'owner.lock').open('a') as session_lock, \
         (session / 'agent_owner.lock').open('a') as agent_lock:
        for lock in (library_lock, session_lock, agent_lock):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        git = SkillGit(library)
        git.require_clean()
        cfg_path = session / 'config.json'
        cfg, state = json.loads(cfg_path.read_text()), json.loads((session / 'state.json').read_text())
        if state.get('config_sha256') != hashlib.sha256(cfg_path.read_bytes()).hexdigest():
            raise ValueError('Prepared session configuration changed')
        if (state.get('phase') != 'exploring' or state.get('active') is not None
                or state.get('exploration_used') != 0 or state.get('bundles')):
            raise ValueError('Stage code only before the first episode in a fresh prepared session')
        if not session.is_relative_to(Path(cfg['data_root']).resolve()):
            raise ValueError('Prepared session must remain under its NAS data root')
        source = library / 'oracle_waypoints/rax_skills' / name
        if source.is_symlink() or not source.resolve().is_relative_to(library):
            raise ValueError('Invalid published skill directory')
        manifest = json.loads((source / 'source_manifest.json').read_text())
        # This entry supports the current single-file main(ctx) code format.
        for filename in ('controller.py', 'SKILL.md', 'spec.json'):
            file = source / filename
            if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != manifest.get(filename):
                raise ValueError('Published skill source hash mismatch')
        spec = json.loads((source / 'spec.json').read_text())
        if spec.get('interface') != 'rax_main_ctx' or spec.get('protocol') != 'oracle_waypoints':
            raise ValueError('Require the RAX Oracle main(ctx) interface')
        if (spec.get('status') != 'verified' and
                not (allow_draft and spec.get('status') == 'draft')):
            raise ValueError('Require verified code or explicit --allow-draft')
        if hashlib.sha256((source / 'controller.py').read_bytes()).hexdigest() != spec.get('controller_sha256'):
            raise ValueError('Controller differs from its accepted source')
        target = session / 'workspace/code' / project
        if not target.resolve().is_relative_to(session):
            raise ValueError('Workspace project escapes the prepared session')
        if target.exists() or target.is_symlink():
            raise ValueError('Refuse to overwrite an existing project')
        target.mkdir()
        for filename in ('controller.py', 'SKILL.md'):
            shutil.copyfile(source / filename, target / filename)
        origin = {'skill': name, 'version': spec['version'], 'status': spec['status'],
                  'library_commit': git.head(), 'controller_sha256': spec['controller_sha256'],
                  'source_bundle': spec['bundle'], 'source_runner_sha256': spec['runner_sha256'],
                  'requires_new_registration_and_rehearsal': True, 'project': project}
        (session / 'seeded_skills').mkdir(exist_ok=True)
        write_json(session / 'seeded_skills' / (project + '.json'), origin)
        with (session / 'workspace/memory/INDEX.md').open('a') as index:
            index.write(f'\nStaged {name} v{spec["version"]} ({spec["status"]}): '
                        f'read code/{project}/SKILL.md and controller.py. '
                        'Recompute geometry, then register/rehearse with this session runner.\n')
        return origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--project', default='bottle')
    parser.add_argument('--allow-draft', action='store_true')
    args = parser.parse_args()
    print(json.dumps(stage(args.library, args.session, args.name, args.project,
                           allow_draft=args.allow_draft), indent=2))


if __name__ == '__main__':
    main()
