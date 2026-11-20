"""Publish, review and version trusted executable experience. Never starts a simulator."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.program_library import ProgramLibrary, configured_program_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=configured_program_root())
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    commands.add_parser('init', help='Initialize our independent local Git history and migrate old revision directories')
    history = commands.add_parser('history')
    history.add_argument('--name')
    history.add_argument('--limit', type=int, default=20)
    publish = commands.add_parser('publish')
    publish.add_argument('--source', type=Path, required=True)
    publish.add_argument('--expected-version', type=int, required=True)
    publish.add_argument('--demonstration-run', type=Path, required=True)
    publish.add_argument('--image', type=Path, required=True)
    record = commands.add_parser('record-run')
    record.add_argument('--run', type=Path, required=True)
    record.add_argument('--invocation-id', required=True)
    for name in ['verify', 'deprecate']:
        sub = commands.add_parser(name)
        sub.add_argument('--name', required=True)
        sub.add_argument('--expected-version', type=int, required=True)
        if name == 'deprecate': sub.add_argument('--reason', required=True)
    args = parser.parse_args()
    if args.root is None: parser.error('Set MAS_PROGRAM_LIBRARY_ROOT or use --root on NAS')
    library = ProgramLibrary(args.root)
    try:
        if args.command == 'init':
            result = library.initialize_git()
        elif args.command == 'history':
            result = library.history(args.name, args.limit)
        elif args.command == 'list':
            result = [{k: str(v) if isinstance(v, Path) else v for k, v in e.items() if k != 'runtime_files'}
                      for e in library.catalog()]
        elif args.command == 'publish':
            result = library.publish(args.source, args.expected_version, args.demonstration_run, args.image)
        elif args.command == 'record-run':
            result = library.record_run(args.run, args.invocation_id)
        else:
            result = library.set_status(args.name, 'verified' if args.command == 'verify' else 'deprecated',
                                        args.expected_version, getattr(args, 'reason', ''))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))

if __name__ == '__main__': main()
