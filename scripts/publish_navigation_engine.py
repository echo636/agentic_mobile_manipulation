"""Freeze a navigation follower and atomically select it for opted-in hosts."""
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
    payload = (args.source / 'src/manipulation_agent/executors/gt_navigation.py').read_bytes()
    version = hashlib.sha256(payload).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.output.resolve() / version
    if not root.exists():
        staging = args.output / ('.staging-' + uuid.uuid4().hex)
        staging.mkdir()
        try:
            (staging / 'gt_navigation.py').write_bytes(payload)
            staging.rename(root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if hashlib.sha256((root / 'gt_navigation.py').read_bytes()).hexdigest() != version:
        raise RuntimeError('Existing navigation bundle differs')
    manifest = dict(format_version=1, root=str(root), source_sha256=version)
    args.activate.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.activate.with_name('.' + args.activate.name + '.' + uuid.uuid4().hex)
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(args.activate)
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
