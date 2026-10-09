"""Publish each finalized visible-demo attempt to the internal review site."""
import argparse
from pathlib import Path
import subprocess
import sys
import time


def snapshot(trials):
    return tuple((str(path.relative_to(trials)), path.stat().st_mtime_ns)
                 for trial in sorted(trials.glob('[0-9][0-9]_*_r*'))
                 for name in ('summary.json', 'demo_audit.json')
                 if (path := trial / name).exists())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=Path, required=True)
    parser.add_argument('--site-root', type=Path, required=True)
    parser.add_argument('--index-name', default='demo_motion_first10_index.html')
    parser.add_argument('--scheduler-pid', type=int, required=True)
    parser.add_argument('--poll-seconds', type=int, default=30)
    args = parser.parse_args()
    script = Path(__file__).with_name('publish_demo_trials.py')
    previous = None
    while True:
        current = snapshot(args.trials)
        if current != previous:
            subprocess.run([sys.executable, str(script), '--trials', str(args.trials),
                            '--site-root', str(args.site_root), '--index-name', args.index_name],
                           check=True)
            previous = current
        if not Path(f'/proc/{args.scheduler_pid}').exists():
            return
        time.sleep(args.poll_seconds)


if __name__ == '__main__':
    main()
