"""Freeze and atomically select an operator-owned geometric-pick backend."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--activate', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve() / 'src/manipulation_agent'
    payloads = {name: (source / name).read_bytes()
                for name in ('grasp_geometry.py', 'executors/oracle_pick.py')}
    embedded_sources = {}
    marker = b'from manipulation_agent.executors.oracle_open_loop import OpenLoopPickTrial'
    if marker in payloads['executors/oracle_pick.py']:
        support = (source / 'executors/oracle_open_loop.py').read_bytes()
        embedded_sources['executors/oracle_open_loop.py'] = hashlib.sha256(support).hexdigest()
        payloads['executors/oracle_pick.py'] = payloads['executors/oracle_pick.py'].replace(
            marker, b'# BEGIN embedded open-loop support\n'+support+b'\n# END embedded open-loop support')
    files = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    version = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.output.resolve() / version
    if not root.exists():
        staging = args.output / ('.staging-'+uuid.uuid4().hex)
        (staging / 'executors').mkdir(parents=True)
        try:
            for name, data in payloads.items():
                (staging / name).write_bytes(data)
            staging.rename(root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    for name, digest in files.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Existing immutable bundle differs: '+name)
    manifest = dict(version=version, root=str(root), files=files, embedded_sources=embedded_sources)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    args.activate.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.activate.with_name('.'+args.activate.name+'.'+uuid.uuid4().hex)
    temporary.write_text(json.dumps(manifest, indent=2)+'\n')
    temporary.replace(args.activate)
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
