"""Copy a native worker and its non-system ELF dependency closure, read-only.

Run on the build host; source environments are only read. The resulting runtime
requires the host glibc/ELF loader but no old navigation environment or CUDA.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--dependency-prefix', type=Path, required=True)
    parser.add_argument('--cartographer-static', type=Path, required=True)
    parser.add_argument('--eigen-include', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    root = args.destination.resolve()
    (root / 'bin').mkdir(parents=True, exist_ok=True)
    (root / 'lib').mkdir(exist_ok=True)
    env = dict(os.environ, LD_LIBRARY_PATH=str(args.dependency_prefix / 'lib'))
    original = subprocess.check_output(['ldd', str(args.binary)], env=env, text=True)
    if 'not found' in original:
        raise RuntimeError(original)
    copied = []
    binary = root / 'bin' / 'mas_cartographer_worker'
    shutil.copy2(args.binary, binary)
    for name, path in re.findall(r'^\s*(\S+) => (/\S+)', original, re.M):
        source = Path(path).resolve()
        if str(source).startswith(('/usr/lib/', '/lib/')):
            continue
        destination = root / 'lib' / name
        shutil.copy2(source, destination)
        copied.append({'soname': name, 'source': str(source), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'bytes': destination.stat().st_size})
    env['LD_LIBRARY_PATH'] = str(root / 'lib')
    resolved = subprocess.check_output(['ldd', str(binary)], env=env, text=True)
    if 'not found' in resolved or str(args.dependency_prefix) in resolved:
        raise RuntimeError('Dependency closure is incomplete: ' + resolved)
    (root / 'ldd.txt').write_text(resolved)
    record = {'status': 'packaged', 'host': socket.gethostname(), 'pid': os.getpid(),
              'interpreter': sys.executable, 'gpu_uuid': None,
              'worker_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'cartographer_static': str(args.cartographer_static),
              'cartographer_static_sha256': hashlib.sha256(args.cartographer_static.read_bytes()).hexdigest(),
              'eigen_include': str(args.eigen_include),
              'eigen_macros_sha256': hashlib.sha256((args.eigen_include / 'Eigen/src/Core/util/Macros.h').read_bytes()).hexdigest(),
              'build_flags': (args.binary.parent / 'CMakeFiles/mas_cartographer_worker.dir/flags.make').read_text(),
              'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in Path(__file__).parent.glob('*.cpp')},
              'source_upstream': json.loads((Path(__file__).parent / 'upstream' / 'source.json').read_text()),
              'libraries': copied, 'copied_bytes': sum(x['bytes'] for x in copied),
              'system_runtime': 'host glibc and ELF loader', 'cuda_required': False}
    (root / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    licenses = root / 'licenses'
    licenses.mkdir(exist_ok=True)
    for filename in ('Cartographer.LICENSE', 'upstream/LICENSE'):
        source = Path(__file__).parent / filename
        shutil.copy2(source, licenses / ('Habitat-integration.LICENSE' if filename.startswith('upstream/') else source.name))
    print(json.dumps({'status': record['status'], 'runtime': str(root), 'libraries': len(copied), 'bytes': record['copied_bytes'], 'worker_sha256': record['worker_sha256']}))


if __name__ == '__main__':
    main()
