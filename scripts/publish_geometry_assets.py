"""Publish a selected geometry program and task flow to our local skill Git.

This publishes a draft of generate(task, oracle, parameters), not a compiled
main(ctx) controller. It never starts a model or simulator or changes old evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.manip_builtin import read
from manipulation_agent.program_library import ProgramLibrary, runtime_identity
from manipulation_agent.records import now, write_json


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish(root, source_root, program, expected_version, evidence):
    entry = read(program)
    if entry is None or 'version' not in entry:
        raise ValueError('Select a versioned built-in geometry program')
    source_root, evidence = Path(source_root), Path(evidence)
    flow_path = source_root / 'task_designs/bottle_to_bin_flow_v1.json'
    doc_path = source_root / 'README.md'
    flow = json.loads(flow_path.read_text())
    document = doc_path.read_text().split('<!-- bottle-flow:start -->', 1)[1].split('<!-- bottle-flow:end -->', 1)[0].strip()+'\n'
    if flow['geometry_program'] != program or flow['geometry_source_sha256'] != entry['source_sha256']:
        raise ValueError('Task flow must pin this exact program source')
    files = ['cpu_validation_takeover.xml', 'cpu_host_sandbox.xml',
             'cpu_adaptive_execution.xml', 'cpu_navigation.xml',
             'archived_mesh_validation.json', 'compatibility.json', 'validation.json']
    for name in files:
        if not (evidence / name).is_file():
            raise ValueError('Missing reviewed evidence: ' + name)
    if not json.loads((evidence / 'validation.json').read_text())['passed']:
        raise ValueError('Complete regression validation before publication')
    library = ProgramLibrary(root)
    with library.locked():
        library.git.require_clean()
        folder = library.root / 'geometry_programs' / program
        current = folder / 'metadata.json'
        version = json.loads(current.read_text())['version'] if current.exists() else 0
        if type(expected_version) is not int or expected_version != version:
            raise ValueError('Geometry asset version conflict; read the current release')
        if entry['version'] != version + 1:
            raise ValueError('Increment the built-in version for each changed source release')
        tag_name = 'geometry-' + program
        tag = f'oracle_waypoints/{tag_name}/v{version+1:06d}'
        if library.git.command('tag', '--list', tag).stdout.strip():
            raise ValueError('Release tag already exists')
        metadata = {k: v for k, v in entry.items() if k != 'source'}
        metadata.update(schema_version=1, kind='geometry_program_and_task_flow',
            interface='generate(task, oracle, parameters) -> EEF phases or candidates',
            status='draft', validation='CPU and archived-mesh checks; new native qualification pending',
            published_at=now(), library_parent_commit=library.git.head(), release_tag=tag,
            flow_sha256=sha(flow_path), document_sha256=hashlib.sha256(document.encode()).hexdigest(),
            harness_runtime_sha256=runtime_identity()['runtime_sha256'],
            evidence=[dict(path=str(evidence / name), sha256=sha(evidence / name)) for name in files])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'source.py').write_text(entry['source'])
        (folder / 'SKILL.md').write_text(document)
        (folder / 'flow.json').write_bytes(flow_path.read_bytes())
        write_json(current, metadata)
        write_json(folder / 'source_manifest.json',
                   {name: sha(folder / name) for name in ('source.py', 'SKILL.md', 'flow.json', 'metadata.json')})
        commit = library.git.commit(f'Publish geometry {program} v{version+1} and bottle-to-bin flow as draft')
        library.git.tag(tag_name, version+1)
        library.git.require_clean()
        release = dict(program=program, version=version+1, source_sha256=entry['source_sha256'],
                       library_root=str(library.repository), library_commit=commit, release_tag=tag)
        # Future frozen episodes expose the actual release identity alongside
        # the exact source bytes; they never execute mutable library files.
        write_json(source_root / f'src/manipulation_agent/geometry_programs/{program}.release.json', release)
        return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--program', default='place_in_open_bin', choices=['place_in_open_bin'])
    parser.add_argument('--expected-version', type=int, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    result = publish(args.root, Path(__file__).resolve().parents[1], args.program,
                     args.expected_version, args.evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
