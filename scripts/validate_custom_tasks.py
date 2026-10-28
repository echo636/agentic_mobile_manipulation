#!/usr/bin/env python3
"""Validate custom runtime contracts on CPU; does not certify physical readiness."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.tasks.custom_tasks import load_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', nargs='+', type=Path)
    parser.add_argument('--require-frozen', action='store_true')
    args = parser.parse_args()
    rows = []
    for path in args.spec:
        try:
            _, row = load_spec(path, require_frozen=args.require_frozen)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            row = {'status': 'failed', 'error': str(exc)}
        rows.append({'path': str(path), **row})
    passed = all(row['status'] == 'passed' for row in rows)
    print(json.dumps({'status': 'passed' if passed else 'failed',
                      'benchmark_ready': False, 'tasks': rows}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
