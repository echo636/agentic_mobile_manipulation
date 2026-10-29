"""Inspect, verify, and restore archived source bytes without a simulator."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_json(path: Path):
    return json.loads(path.read_text())

def safe_relative(value: str) -> Path:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '\\' in value or any(ord(c)<32 for c in value):
        raise ValueError(f'Unsafe archive path: {value!r}')
    return Path(*p.parts)

def object_file(root: Path, sha: str) -> Path:
    if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid object digest')
    return root / 'objects' / sha[:2] / (sha + '.gz')

def read_object(root: Path, sha: str) -> bytes:
    data = gzip.decompress(object_file(root, sha).read_bytes())
    if digest(data) != sha:
        raise ValueError(f'Object checksum mismatch: {sha}')
    return data

def read_snapshot(root: Path, snapshot_id: str):
    # Snapshot IDs are full SHA256 digests, never filesystem paths.
    object_file(root, snapshot_id)
    value = load_json(root / 'snapshots' / (snapshot_id + '.json'))
    if digest(json.dumps(value['files'], sort_keys=True, separators=(',', ':')).encode()) != snapshot_id:
        raise ValueError('Snapshot checksum mismatch')
    for name in value['files']:
        safe_relative(name)
    return value

def restore(root: Path, snapshot_id: str, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f'Refusing to overwrite: {output}')
    snap = read_snapshot(root, snapshot_id)
    # Verify every object before creating output; corrupt archives cannot leave a partial checkout.
    for sha in set(snap['files'].values()):
        read_object(root, sha)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.archive-restore-', dir=output.parent))
    try:
        for name, sha in snap['files'].items():
            target = staging / safe_relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_object(root, sha))
            target.chmod(0o755 if name in snap.get('executable_files', []) else 0o644)
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {'snapshot': snapshot_id, 'files': len(snap['files']), 'output': str(output)}

def verify(root: Path) -> dict:
    catalog = load_json(root / 'catalog.json')
    ids = {s['snapshot_id'] for s in catalog['snapshots']}
    objects = set()
    for snapshot_id in ids:
        snap = read_snapshot(root, snapshot_id)
        objects.update(snap['files'].values())
    objects.update(e['object_sha256'] for e in catalog.get('environment_version_records', []))
    metadata = root/'recorded_metadata.json'
    if metadata.exists():
        objects.update(e['captured_object_sha256'] for e in load_json(metadata) if 'captured_object_sha256' in e)
    for item in catalog.get('upstream_recoveries', []):
        upstream = load_json(root/'upstream'/item['name']/'recovery.json')
        for name, sha in upstream['overrides'].items():
            safe_relative(name)
            if upstream['files'][name] != sha:
                raise ValueError('Upstream override does not match recorded digest')
            objects.add(sha)
    for sha in sorted(objects):
        read_object(root, sha)
    package_files = 0
    if (root/'PACKAGE_MANIFEST.json').exists():
        for name, expected in load_json(root/'PACKAGE_MANIFEST.json')['files'].items():
            target = root / safe_relative(name)
            if target.is_symlink() or digest(target.read_bytes()) != expected:
                raise ValueError('Publication file checksum mismatch: ' + name)
            package_files += 1
    return {'passed': True, 'snapshots': len(ids), 'unique_file_objects': len(objects),
            'publication_files_checked': package_files,
            'scope': 'Archived file bytes only; not GPU execution or task success.'}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive-root', type=Path, default=ROOT)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    show = sub.add_parser('show'); show.add_argument('experiment')
    take = sub.add_parser('restore'); take.add_argument('snapshot_id'); take.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); root = args.archive_root.resolve()
    if args.command == 'verify':
        result = verify(root)
    elif args.command == 'restore':
        aliases = load_json(root/'selected_runtimes.json') if (root/'selected_runtimes.json').exists() else {}
        result = restore(root, aliases.get(args.snapshot_id, args.snapshot_id), args.output.absolute())
    else:
        c = load_json(root / 'catalog.json')
        matches = [e for e in c['experiments'] if args.experiment in e['name']]
        result = {'experiments': matches, 'snapshots': [s for s in c['snapshots'] if any(s['experiment'] == e['name'] for e in matches)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
