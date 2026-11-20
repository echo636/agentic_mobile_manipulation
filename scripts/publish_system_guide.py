"""Publish the maintained explainer at its existing URL, without touching runs."""
import argparse
import os
from pathlib import Path
import tempfile


def _publish_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.system-guide-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source.read_bytes())
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'web/system_guide.html'
    # Assets are published before the document that references them. Archived
    # runs and their existing replay addresses are never modified here.
    assets = source.parent / 'assets'
    if assets.is_dir():
        for asset in sorted(assets.iterdir()):
            if asset.is_file():
                _publish_file(asset, args.output_dir / 'system_guide_assets' / asset.name)
    target = args.output_dir / 'manipulation_system.html'
    _publish_file(source, target)
    print(target)


if __name__ == '__main__':
    main()
