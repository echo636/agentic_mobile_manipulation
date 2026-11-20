"""Stage replay HTML and actual MCP attachment assets without encoding media.

Prepare makes a reviewable manifest and staged pages. Apply verifies that the
source JSON and currently published HTML still match that manifest, preserves
old HTML, and atomically installs derived attachments before their HTML pages.
Original replay JSON, raw event streams, archived images and videos stay intact.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def within(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path outside selected root: {relative}')
    return path


def prepare(args: argparse.Namespace) -> dict:
    root = args.reports_root.resolve()
    record = args.record_dir.resolve()
    manifest_path = record / 'manifest.json'
    if manifest_path.exists():
        raise FileExistsError(f'Preserve existing publication record: {manifest_path}')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    if args.expected_source and commit != args.expected_source:
        raise ValueError(f'Expected source {args.expected_source}; found {commit}')
    sys.path.insert(0, str(REPO / 'src'))
    from manipulation_agent.replay import render_replay_page
    if args.selection_json:
        selection = json.loads(args.selection_json.read_text())
        inputs = sorted({within(root, value) for value in selection['replays']})
        if any(path.name != 'replay.json' or not path.is_file() for path in inputs):
            raise ValueError('Selection must contain existing replay.json paths')
    else:
        inputs = sorted({path for scope in args.scope
                         for path in within(root, scope).glob('*/replay.json')})
    pages = []
    for source in inputs:
        target = source.with_name('replay.html')
        data = json.loads(source.read_text())
        relative = target.relative_to(root).as_posix()
        staged = within(record / 'staged', relative)
        staged.parent.mkdir(parents=True, exist_ok=True)
        trace_stats = {}
        staged.write_text(render_replay_page(data, source_dir=source.parent,
                                            asset_dir=staged.parent, trace_stats=trace_stats))
        pages.append({'target': relative, 'input': source.relative_to(root).as_posix(),
                      'input_sha256': digest(source), 'before_html_sha256': digest(target),
                      'staged_html_sha256': digest(staged), 'run_id': data['run_id'],
                      'trace': trace_stats,
                      'attachments': [
                          {'target': (target.parent / name).relative_to(root).as_posix(),
                           'sha256': sha, 'before_sha256': digest(target.parent / name)}
                          for name, sha in trace_stats['asset_files'].items()]})
    if args.comparison:
        path = root / 'compare100_20261002/comparison.json'
        data = json.loads(path.read_text())
        spec = importlib.util.spec_from_file_location('ui_batch_comparison', REPO / 'scripts/batch_comparison.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        relative = 'compare100_20261002/index.html'
        staged = within(record / 'staged', relative)
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(module.render_comparison_page(tuple(data['arms'])))
        pages.append({'target': relative, 'input': path.relative_to(root).as_posix(),
                      'input_sha256': digest(path), 'before_html_sha256': digest(root / relative),
                      'staged_html_sha256': digest(staged), 'kind': 'comparison'})
    source_files = [REPO / 'src/manipulation_agent/replay.py', REPO / 'src/manipulation_agent/tool_trace.py',
                    REPO / 'scripts/publish_replay_ui.py',
                    REPO / 'scripts/batch_comparison.py', REPO / 'scripts/comparison_dashboard.html',
                    *sorted((REPO / 'src/manipulation_agent/replay_assets').glob('*'))]
    manifest = {'status': 'planned', 'prepared_at': stamp(), 'source_commit': commit,
                'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO, text=True).strip()),
                'selection_file': str(args.selection_json.resolve()) if args.selection_json else None,
                'selection_sha256': digest(args.selection_json) if args.selection_json else None,
                'source_hashes': {str(p.relative_to(REPO)): digest(p) for p in source_files if p.is_file()},
                'reports_root': str(root), 'pages': pages,
                'html_only': not any(p.get('attachments') for p in pages),
                'derived_attachment_count': sum(len(p.get('attachments', [])) for p in pages),
                'media_encoded': False, 'evaluation_or_replay_json_modified': False}
    write_json(manifest_path, manifest)
    return manifest


def apply(record: Path) -> dict:
    record = record.resolve()
    manifest = json.loads((record / 'manifest.json').read_text())
    if manifest['status'] != 'planned':
        raise ValueError('Only an unapplied, planned manifest can be applied')
    root = Path(manifest['reports_root']).resolve()
    # Check the whole plan before writing any destination. A later renderer must
    # not silently replace a page or data updated since the preview was prepared.
    for page in manifest['pages']:
        if digest(within(root, page['input'])) != page['input_sha256']:
            raise ValueError(f'Input changed after prepare: {page["input"]}')
        if digest(within(root, page['target'])) != page['before_html_sha256']:
            raise ValueError(f'Published HTML changed after prepare: {page["target"]}')
        if digest(within(record / 'staged', page['target'])) != page['staged_html_sha256']:
            raise ValueError(f'Staged HTML changed after prepare: {page["target"]}')
        run_root = within(root, page['input']).parent
        for name, sha in page.get('trace', {}).get('source_files', {}).items():
            if digest(within(run_root, name)) != sha:
                raise ValueError(f'Trace evidence changed after prepare: {page["input"]}: {name}')
        for attachment in page.get('attachments', []):
            if digest(within(root, attachment['target'])) != attachment['before_sha256']:
                raise ValueError(f'Attachment destination changed: {attachment["target"]}')
            if digest(within(record / 'staged', attachment['target'])) != attachment['sha256']:
                raise ValueError(f'Staged attachment changed: {attachment["target"]}')
    manifest['status'] = 'running'
    manifest['started_at'] = stamp()
    write_json(record / 'manifest.json', manifest)
    published = []
    published_attachments = []
    try:
        for page in manifest['pages']:
            for attachment in page.get('attachments', []):
                target_asset = within(root, attachment['target'])
                if attachment['before_sha256'] == attachment['sha256']:
                    continue
                if target_asset.exists():
                    backup_asset = within(record / 'before', attachment['target'])
                    backup_asset.parent.mkdir(parents=True, exist_ok=True)
                    backup_asset.write_bytes(target_asset.read_bytes())
                target_asset.parent.mkdir(parents=True, exist_ok=True)
                temporary_asset = target_asset.with_name(target_asset.name + '.ui-publish.tmp')
                temporary_asset.write_bytes(within(record / 'staged', attachment['target']).read_bytes())
                temporary_asset.replace(target_asset)
                published_attachments.append(attachment['target'])
            target = within(root, page['target'])
            backup = within(record / 'before', page['target'])
            if target.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(target.read_bytes())
                if digest(backup) != page['before_html_sha256']:
                    raise ValueError(f'Backup mismatch: {page["target"]}')
            temporary = target.with_name(target.name + '.ui-publish.tmp')
            temporary.write_bytes(within(record / 'staged', page['target']).read_bytes())
            temporary.replace(target)
            published.append(page['target'])
        manifest['status'] = 'passed'
    except Exception as exc:
        manifest.update(status='failed', error=str(exc))
        raise
    finally:
        manifest['published_attachments'] = published_attachments
        manifest['published'] = published
        manifest['finished_at'] = stamp()
        write_json(record / 'manifest.json', manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    stage = commands.add_parser('prepare')
    stage.add_argument('--reports-root', type=Path, required=True)
    stage.add_argument('--record-dir', type=Path, required=True)
    selection = stage.add_mutually_exclusive_group(required=True)
    selection.add_argument('--selection-json', type=Path, help='Explicit {replays:[relative replay.json paths]} selection')
    selection.add_argument('--scope', action='append', help='Explicit relative run-parent directory; includes all its attempts')
    stage.add_argument('--comparison', action='store_true', help='Also stage the existing comparison index')
    stage.add_argument('--expected-source', help='Require exact repository HEAD')
    publish = commands.add_parser('apply')
    publish.add_argument('--record-dir', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args) if args.command == 'prepare' else apply(args.record_dir)
    print(json.dumps({'status': result['status'], 'pages': len(result['pages']),
                      'record_dir': str(args.record_dir.resolve()), 'html_only': result.get('html_only'),
                      'derived_attachments': result.get('derived_attachment_count', 0)}))


if __name__ == '__main__':
    main()
