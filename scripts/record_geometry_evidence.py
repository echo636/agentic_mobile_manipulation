"""Append an audited exact-source development result to the local geometry library."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.program_library import ProgramLibrary
from manipulation_agent.records import now


def record(root, acceptance, expected_version):
    acceptance = Path(acceptance).resolve()
    payload = acceptance.read_bytes()
    review = json.loads(payload)
    if (not review.get('evidence_complete') or review.get('pending')
            or review.get('source_mismatches') or review.get('navigation_source_mismatches')
            or not (review.get('native_worker_closed') or {}).get('resource_leases_released')):
        raise ValueError('Require complete, closed native evidence before import')
    calls = review.get('fixed_program_calls') or []
    if not calls:
        raise ValueError('This run did not call the fixed placement source')
    library = ProgramLibrary(root)
    with library.locked():
        library.git.require_clean()
        folder = library.root / 'geometry_programs/place_in_open_bin'
        metadata = json.loads((folder / 'metadata.json').read_text())
        version = metadata['version']
        source_hash = hashlib.sha256((folder / 'source.py').read_bytes()).hexdigest()
        if (version != expected_version or metadata['source_sha256'] != source_hash
                or any(c['source_sha256'] != source_hash for c in calls)
                or review['placement_program_release']['source_sha256'] != source_hash):
            raise ValueError('Record evidence against the current exact geometry version')
        entry = dict(at=now(), run=review['run'], version=version, source_sha256=source_hash,
            acceptance=str(acceptance), acceptance_sha256=hashlib.sha256(payload).hexdigest(),
            fixed_program_native_passed=review['fixed_program_native_passed'],
            single_bottle_passed=review['passed'], calls=calls,
            coordinated_development=review['coordinated_development'],
            independent_rehearsal=False, status_change=False)
        evidence = folder / 'evidence/runs.jsonl'
        previous = [json.loads(line) for line in evidence.read_text().splitlines()] if evidence.exists() else []
        if any(row['run'] == entry['run'] for row in previous):
            raise ValueError('Run already recorded; evidence is append-only')
        evidence.parent.mkdir(exist_ok=True)
        with evidence.open('a') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        commit = library.git.commit('Record geometry place_in_open_bin v' + str(version) + ' development result')
        library.git.require_clean()
        return dict(entry, library_commit=commit, status=metadata['status'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--expected-version', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(record(args.root, args.acceptance, args.expected_version), ensure_ascii=False))


if __name__ == '__main__':
    main()
