"""Install the bundled current library snapshot into a new writable runtime root.

No model/simulator/network calls. Source hashes are checked before copying.
An existing runtime root is preserved; select a new destination for another import.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src'))
from manipulation_agent.geometry_library import GeometryProgramLibrary
from manipulation_agent.program_library import ProgramLibrary
from manipulation_agent.experience_skills import ExperienceStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True,
                        help='New writable runtime directory outside the source repository')
    args = parser.parse_args()
    target = args.root.expanduser().resolve()
    if target.is_relative_to(REPO):
        parser.error('Keep mutable runtime libraries outside the source repository')
    for name in ['skill_library', 'experience_skills']:
        if (target/name).exists():
            parser.error('Destination already contains '+name+'; select a new runtime root')
    seed = REPO/'resources/library_seed'
    manifest = json.loads((seed/'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        source = seed/name
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(seed.resolve()):
            raise ValueError('Invalid library seed path: '+name)
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('Library seed changed: '+name)
    target.mkdir(parents=True, exist_ok=True)
    for name in ['skill_library', 'experience_skills']:
        shutil.copytree(seed/name, target/name)
    library = ProgramLibrary(target/'skill_library')
    library.initialize_git()
    programs = GeometryProgramLibrary(target/'skill_library').snapshot()
    compiled = library.catalog()
    store = ExperienceStore(target/'experience_skills')
    for entry in manifest['experience_entries']:
        record = store.current(entry['kind'], entry['key'], entry['protocol'])
        assert record and record['revision'] == entry['revision']
    result = {
        'root': str(target), 'geometry_programs': len(programs),
        'compiled_skills': len(compiled), 'experience_entries': len(manifest['experience_entries']),
        'library_commit': library.git.head(),
        'source': 'bundled current snapshot; historical evidence references are archive-only',
        'new_physical_validation': False,
    }
    (target/'library_import.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
