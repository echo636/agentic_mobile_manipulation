"""Collect existing episode geometry sources into local Git, without running them."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.geometry_library import GeometryProgramLibrary, checked_source
from manipulation_agent.manip_builtin import catalog, read
from manipulation_agent.manip_programs import ManipPrograms


POLICY = '''
## Collect and reuse geometry programs

Written geometry programs are collected immediately as drafts after basic source
checks; physical certification is separate. New model_program_v1 episodes freeze
the shared catalog, read applicable programs and reuse them. Improve the same
program name with its current hash rather than inventing a new name for every
retry. Draft status does not block collection or selection. Git preserves prior
versions, authorship and source origins; failed attempts remain in their original
episode records. Active episodes keep their frozen copies.

See [the geometry catalog](oracle_waypoints/GEOMETRY_INDEX.md). The host collects
write_manip_program through GeometryProgramLibrary; publication conflicts preserve
the local draft and report pending rather than overwriting another update.
'''


def collect(root, program_roots, *, include_builtins=True):
    library = GeometryProgramLibrary(root)
    current = {row['program']: row for row in library.snapshot()}
    collected, skipped = [], []
    known = {}

    def save(entry):
        name, sha = entry['program'], entry['source_sha256']
        if name not in known:
            hashes = {current[name]['source_sha256']} if name in current else set()
            git = library.library.git
            if git.head():
                tags = git.command('tag', '--list', f'oracle_waypoints/geometry-{name}/v*').stdout.splitlines()
                for tag in tags:
                    metadata = git.command('show', tag+f':oracle_waypoints/geometry_programs/{name}/metadata.json')
                    hashes.add(json.loads(metadata.stdout)['source_sha256'])
            known[name] = hashes
        if sha in known[name]:
            skipped.append({'program': name, 'source_sha256': sha})
            return
        expected = current[name]['source_sha256'] if name in current else None
        published = library.publish(entry, expected)
        current[name] = published
        known[name].add(sha)
        collected.append(published['library_release'])

    if include_builtins:
        for row in catalog():
            if row['program'] not in current:
                save(read(row['program']))
    for program_root in program_roots:
        program_root = Path(program_root).resolve()
        programs = ManipPrograms(program_root)
        for folder in sorted(program_root.iterdir()):
            if not (folder / 'current.json').is_file():
                continue
            entry = programs.read(folder.name)
            versions = [p for p in folder.iterdir() if p.is_dir() and re.fullmatch(r'[0-9a-f]{64}', p.name)]
            versions.sort(key=lambda p: (p.name == entry['source_sha256'], p.stat().st_mtime_ns, p.name))
            for version in versions:
                source = (version / 'source.py').read_text()
                if checked_source(source) != version.name:
                    raise ValueError('Archived geometry source hash mismatch: '+str(version))
                save({**entry, 'source': source, 'source_sha256': version.name,
                    'provenance': {'episode_program_root': str(program_root),
                        'archived_source': str(version / 'source.py'),
                        'validation': 'Historical source collection; inspect original run outcomes before reuse'}})
    with library.library.locked():
        library.library.git.require_clean()
        readme = library.library.repository / 'README.md'
        if '## Collect and reuse geometry programs' not in readme.read_text():
            readme.write_text(readme.read_text().rstrip()+'\n'+POLICY)
            library.library.git.commit('Document immediate geometry collection and reuse policy')
    return {'collected': collected, 'already_collected': skipped,
            'library_commit': library.library.git.head()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--program-root', action='append', type=Path, default=[])
    args = parser.parse_args()
    print(json.dumps(collect(args.root, args.program_root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
