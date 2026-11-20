"""Update the existing results portal without publishing per-operation reports."""
from pathlib import Path
import argparse
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, required=True)
parser.add_argument('--sources-json', type=Path, help='Catalog of compact result feeds and replay locations')
args = parser.parse_args()
repo = Path(__file__).resolve().parents[1]
args.output_dir.mkdir(parents=True, exist_ok=True)
for source in [repo / 'web/replays.html', repo / 'web/replay_portal.js']:
    temporary = args.output_dir / (source.name + '.tmp')
    shutil.copyfile(source, temporary)
    temporary.replace(args.output_dir / source.name)
if args.sources_json:
    temporary = args.output_dir / 'results_sources.json.tmp'
    shutil.copyfile(args.sources_json, temporary)
    temporary.replace(args.output_dir / 'results_sources.json')
# Existing bookmarks still resolve, but there is only one results UI.
redirect = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>任务结果与回放</title><script>location.replace('replays.html'+location.search+location.hash)</script>
<a href="replays.html">打开任务结果与回放</a></html>'''
for name in ('results.html', 'index.html'):
    temporary = args.output_dir / (name + '.tmp')
    temporary.write_text(redirect)
    temporary.replace(args.output_dir / name)
