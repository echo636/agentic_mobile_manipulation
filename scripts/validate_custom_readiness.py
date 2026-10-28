#!/usr/bin/env python3
"""Verify a hash-pinned custom release against physical and policy evidence."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.references.custom_readiness import validate_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = validate_release(args.manifest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'status': 'failed', 'benchmark_ready': False, 'error': str(exc)}
    payload = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(payload)
    print(payload, end='')
    return 0 if result['benchmark_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
