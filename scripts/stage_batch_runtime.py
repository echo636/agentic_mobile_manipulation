"""Pin frozen code and task inputs on worker-local disks before dispatch.

Assets and installed simulator environments retain their recorded locations.
No live attempt or source checkout is changed. Credentials are not bundled.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import now, source_version, write_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build_bundle(config, worker):
    source = Path(config['source_path'])
    scheduler = Path(config.get('scheduler_source_path', source))
    sources = {'executor': source}
    if scheduler != source:
        sources['scheduler'] = scheduler
    identities = {name: source_version(path) for name, path in sources.items()}
    assert all(not i['dirty'] and i['identity_method'] == 'verified_source_freeze'
               for i in identities.values()), 'Only frozen, verified sources may be staged'
    assert identities['executor']['commit'] == config['source_commit']
    staging_root = worker.get('runtime_staging_root')
    destination = (Path(staging_root) if staging_root else
                   Path(worker['simulator_tmpdir']).parents[2] / 'dispatch') / config['batch_tag']
    source_target = destination / 'executor'
    scheduler_target = destination / ('scheduler' if 'scheduler' in sources else 'executor')
    manifest = json.loads((Path(config['batch']) / 'manifest.json').read_text())
    manifest['source_path'] = str(source_target)
    files = {}
    relocated = []
    for row in manifest['tasks']:
        spec_key = 'catalog_task_spec' if row.get('catalog_task_spec') else 'custom_task_spec'
        if not row.get(spec_key):
            continue
        original = Path(row[spec_key])
        spec_bytes = original.read_bytes()
        assert digest(spec_bytes) == row[spec_key + '_sha256']
        spec = json.loads(spec_bytes)
        for key in ('frozen_instance', 'construction_seed', 'sampling_record'):
            if not spec.get(key):
                continue
            entry = spec[key]
            scene = (original.parent / entry['path']).resolve()
            raw = scene.read_bytes()
            assert digest(raw) == entry['sha256']
            name = 'inputs/scene_' + entry['sha256'] + '.json'
            files[name] = raw
            entry['path'] = str(destination / name)
        name = 'inputs/task_' + str(row['index']) + '.json'
        files[name] = (json.dumps(spec, ensure_ascii=False, indent=2) + '\n').encode()
        row[spec_key] = str(destination / name)
        row[spec_key + '_sha256'] = digest(files[name])
        relocated.append({'task': row['task'], 'original_spec_sha256': digest(spec_bytes),
                          'runtime_spec_sha256': row[spec_key + '_sha256'],
                          'physical_instance_sha256': row.get('catalog_instance_sha256') or row.get('custom_instance_sha256')})
    files['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    for label, path in sources.items():
        # This is a small, known frozen release, not an asset/cache traversal.
        for folder in ('src', 'scripts', 'skills', 'task_designs', 'web'):
            for child in sorted((path / folder).rglob('*')):
                if child.is_file() and '__pycache__' not in child.parts and child.suffix != '.pyc':
                    assert not child.is_symlink(), 'Release must not reference external files'
                    files[label + '/' + str(child.relative_to(path))] = child.read_bytes()
        for name in ('source_freeze.json', 'pyproject.toml'):
            files[label + '/' + name] = (path / name).read_bytes()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    proof = {'archive_sha256': digest(payload), 'manifest_sha256': digest(files['manifest.json']),
             'sources': identities, 'relocated_specs': relocated,
             'runtime_source_path': str(source_target), 'runtime_scheduler_path': str(scheduler_target),
             'runtime_manifest_path': str(destination / 'manifest.json')}
    return destination, payload, proof


INSTALL = r'''
import hashlib, io, json, os, subprocess, sys, tarfile
from pathlib import Path
target, expected = Path(sys.argv[1]), sys.argv[2]
raw = sys.stdin.buffer.read()
assert hashlib.sha256(raw).hexdigest() == expected
target.parent.mkdir(parents=True, exist_ok=True)
fstype = subprocess.check_output(['findmnt', '-T', str(target.parent), '-n', '-o', 'FSTYPE'], text=True).strip()
assert fstype in ('ext4', 'xfs', 'btrfs', 'zfs'), ('Staging destination must be a verified local disk; '
    'set worker.runtime_staging_root to a local filesystem, got ' + fstype)
marker = target / 'bundle_sha256'
if target.exists():
    assert marker.read_text().strip() == expected, 'Do not overwrite another frozen runtime'
else:
    target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            path = target / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
    (target / 'preflight').mkdir()
    marker.write_text(expected + '\n')
sys.path.insert(0, str(target / 'executor/src'))
from manipulation_agent.records import source_version
identities = {name: source_version(target / name) for name in ('executor', 'scheduler') if (target / name).exists()}
manifest = target / 'manifest.json'
print(json.dumps({'status':'passed', 'host':os.uname().nodename, 'filesystem':fstype,
    'sources':identities, 'archive_sha256':expected,
    'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(), 'target':str(target)}))
'''


def stage_config(config):
    config = copy.deepcopy(config)
    groups = {}
    for worker in config['workers']:
        key = (tuple(worker['ssh']), str(Path(worker['simulator_tmpdir']).parents[2]))
        groups.setdefault(key, []).append(worker)

    def stage(workers):
        worker = workers[0]
        destination, payload, proof = build_bundle(config, worker)
        result = subprocess.run(worker['ssh'] + [shlex.join([
            worker['sim_python'], '-B', '-S', '-c', INSTALL, str(destination), proof['archive_sha256']])],
            input=payload, capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError('Runtime staging failed on ' + worker['id'] + ': ' + result.stderr.decode()[-1800:])
        check = json.loads(result.stdout)
        assert check['sources'] == proof['sources']
        assert check['manifest_sha256'] == proof['manifest_sha256']
        for lane in workers:
            for key in ('runtime_source_path', 'runtime_scheduler_path', 'runtime_manifest_path'):
                lane[key] = proof[key]
            lane['runtime_staging'] = {**proof, 'host': check['host'], 'filesystem': check['filesystem']}
        return {'at': now(), **check, 'workers': [w['id'] for w in workers],
                'relocated_specs': proof['relocated_specs']}

    with ThreadPoolExecutor(max_workers=min(5, len(groups))) as pool:
        records = list(pool.map(stage, groups.values()))
    return config, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    root = Path(config['batch'])
    assert not (root / 'execution.json').exists(), 'Stage only a new, unstarted batch'
    staged, records = stage_config(config)
    write_json(root / 'runtime_staging.json', {'status':'passed', 'at':now(), 'workers':records,
        'validation_level':'Local frozen source/input hashes; not simulator initialization or task success'})
    write_json(args.config, staged)
    print(json.dumps({'status':'passed', 'hosts':len(records), 'batch':str(root)}))


if __name__ == '__main__':
    main()
