"""Restore this task's three pinned scene files outside the source repository."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def restore(archive_repo: Path, output: Path) -> None:
    provenance = json.loads((Path(__file__).parent.parent / 'provenance.json').read_text())
    scene_prefix = provenance['source_runtime'] + '/tasks/prepare_and_serve_hot_drink/scene/'
    files = [item for item in provenance['external_support']
             if item['source_path'].startswith(scene_prefix)]
    if output.exists() and any(output.iterdir()):
        raise ValueError(f'Choose an empty output directory: {output}')
    restored = []
    for item in files:
        payload = subprocess.check_output([
            'git', '-C', str(archive_repo), 'show',
            provenance['source_commit'] + ':' + item['source_path'],
        ])
        if hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise ValueError(f'Scene differs from pinned source: {item["source_path"]}')
        relative = Path(item['source_path'].removeprefix(scene_prefix))
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        restored.append({'path': str(relative), 'sha256': item['sha256']})
    (output / 'scene_manifest.json').write_text(json.dumps({
        'task': 'prepare_and_serve_hot_drink',
        'source_commit': provenance['source_commit'], 'files': restored,
        'status': 'scene_files_restored', 'simulator_validation': 'not_run',
    }, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-repo', type=Path, required=True,
                        help='Git checkout containing the pinned PR #6 commit')
    parser.add_argument('--output', type=Path, required=True,
                        help='Empty scene output directory outside the source tree')
    args = parser.parse_args()
    restore(args.archive_repo.resolve(), args.output.resolve())
