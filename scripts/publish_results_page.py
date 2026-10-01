"""Publish the results page which embeds the standalone replay viewer."""
from pathlib import Path
import argparse
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[1]
args.output_dir.mkdir(parents=True, exist_ok=True)
for source in [repo / 'web/results.html']:
    temporary = args.output_dir / (source.name + '.tmp')
    shutil.copyfile(source, temporary)
    temporary.replace(args.output_dir / source.name)
