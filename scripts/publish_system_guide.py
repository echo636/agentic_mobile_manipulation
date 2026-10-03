"""Publish the maintained explainer at its existing URL, without touching runs."""
import argparse
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'web/system_guide.html'
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / 'manipulation_system.html'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=args.output_dir, prefix='.system-guide-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source.read_bytes())
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(target)


if __name__ == '__main__':
    main()
