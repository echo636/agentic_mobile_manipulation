"""Attach only byte-verified images actually returned in completed MCP results.

The simulator's captures are lookup candidates, never evidence of image delivery.
Public event files are filtered, so transcript source_event_index is NOT a line
number into that file. Alignment uses call IDs and returned evidence IDs instead.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

_LABEL = re.compile(r'RGB view=([^,\s]+),\s*image_ref=([^\s]+)')
_EXTENSIONS = {'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
               'image/webp': 'webp', 'image/gif': 'gif'}


def _sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json_objects(texts):
    for text in texts:
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            yield value


def _evidence(texts, structured=None):
    values = list(_json_objects(texts))
    if isinstance(structured, dict):
        values.append(structured)
    return {str(v['evidence_id']) for v in values if v.get('evidence_id')}


def _safe_file(root: Path, relative) -> Path | None:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        return None
    path = root / relative
    return path if path.resolve().is_relative_to(root.resolve()) and path.is_file() else None


def enrich_tool_trace(data: dict, source_dir: Path | None = None,
                      asset_dir: Path | None = None) -> dict:
    """Return a shallow data copy with copied transcript entries and derived assets.

    source_dir is an archived run. asset_dir is the output run directory (staged
    during publication); no original JSON, image or video is ever modified.
    Without raw completed MCP results, delivery remains explicitly unverified.
    """
    entries = [dict(entry) for entry in data.get('model_transcript', [])]
    stats = {'schema_version': 1, 'source': None, 'result_count': 0,
             'verified_results': 0, 'partial_results': 0, 'unverified_results': 0,
             'attachment_count': 0, 'verified_images': 0, 'archive_matches': 0,
             'derived_images': 0, 'diagnostics': [], 'source_files': {}, 'asset_files': {}}
    root = Path(source_dir).resolve() if source_dir is not None else None
    output = Path(asset_dir).resolve() if asset_dir is not None else root
    source = next((root / name for name in ('model_public_events.jsonl',
                  'model_events.compat.jsonl', 'model_events.jsonl')
                   if (root / name).is_file()), None) if root else None
    by_call = defaultdict(list)
    by_evidence = defaultdict(list)
    archive = defaultdict(list)
    hash_cache = {}
    if source:
        stats['source'] = source.name
        stats['source_files'][source.name] = _sha(source)
        with source.open() as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    stats['diagnostics'].append({'code': 'malformed_event', 'line': line_number})
                    continue
                if not isinstance(event, dict):
                    continue
                item = event.get('item') or {}
                if event.get('type') == 'item.completed' and item.get('type') == 'mcp_tool_call':
                    pass
                elif event.get('kind') == 'tool_result' and event.get('call_id'):
                    item = {**event, 'id': event['call_id']}
                else:
                    continue
                result = item.get('result')
                # A transport error with result:null is known to deliver no images.
                content = result.get('content', []) if isinstance(result, dict) else []
                if not isinstance(content, list):
                    content = []
                texts = [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
                structured = (result or {}).get('structuredContent', (result or {}).get('structured_content')) if isinstance(result, dict) else None
                record = {'id': item.get('id'), 'tool': item.get('tool'), 'content': content,
                          'evidence': _evidence(texts, structured), 'line': line_number}
                if record['id']:
                    by_call[record['id']].append(record)
                for value in record['evidence']:
                    by_evidence[value].append(record)
        capture_path = root / 'captures.jsonl'
        if capture_path.is_file():
            stats['source_files']['captures.jsonl'] = _sha(capture_path)
            with capture_path.open() as stream:
                for line in stream:
                    try:
                        capture = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    for image in capture.get('images', []):
                        if image.get('image_ref') and image.get('file'):
                            archive[image['image_ref']].append(image['file'])
        # Older archives may lack captures.jsonl. These paths remain lookup-only:
        # a matching raw MCP attachment and a byte hash are still required.
        for step in data.get('steps', []):
            for name in ('before', 'after'):
                for image in (step.get(name) or {}).get('images', []):
                    if image.get('image_ref') and image.get('file'):
                        archive[image['image_ref']].append(image['file'])

    for entry in entries:
        if entry.get('kind') != 'tool_result':
            continue
        stats['result_count'] += 1
        entry['images'] = []
        delivery = {'status': 'unverified', 'attachment_count': None, 'verified_count': 0,
                    'recorded_image_count': entry.get('image_count'), 'diagnostics': []}
        entry['image_delivery'] = delivery
        identifiers = {entry.get('call_id'), entry.get('source_id')} - {None}
        evidence = _evidence(entry.get('text_blocks', []), entry.get('structured_content'))
        candidates = [r for key in identifiers for r in by_call.get(key, [])]
        if not candidates and evidence:
            candidates = [r for key in evidence for r in by_evidence.get(key, [])]
        candidates = list({r['line']: r for r in candidates}.values())
        candidates = [r for r in candidates if not entry.get('tool') or r['tool'] == entry['tool']]
        if evidence:
            candidates = [r for r in candidates if r['evidence'] == evidence]
        if len(candidates) != 1:
            delivery['diagnostics'].append({'code': 'raw_stream_missing' if source is None else
                                           ('ambiguous_result' if candidates else 'completed_result_not_found')})
            stats['unverified_results'] += 1
            continue
        record = candidates[0]
        content = record['content']
        metadata = {}
        for value in _json_objects(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text'):
            for image in (value.get('observation') or {}).get('images', []):
                if image.get('image_ref'):
                    metadata[image['image_ref']] = image
        count = sum(isinstance(b, dict) and b.get('type') == 'image' for b in content)
        delivery.update(attachment_count=count, source=source.name, source_line=record['line'],
                        call_id=record['id'], evidence_ids=sorted(record['evidence']))
        stats['attachment_count'] += count
        label = None
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text':
                match = _LABEL.fullmatch(block.get('text', '').strip())
                label = {'view': match[1], 'image_ref': match[2]} if match else None
                continue
            if block.get('type') != 'image':
                label = None
                continue
            mime = block.get('mimeType', block.get('mime_type', block.get('media_type', '')))
            encoded = block.get('data', block.get('base64', block.get('image_base64')))
            if isinstance(encoded, str) and encoded.startswith('data:'):
                header, _, encoded = encoded.partition(',')
                mime = header[5:].split(';')[0]
            try:
                if mime not in _EXTENSIONS or not isinstance(encoded, str):
                    raise ValueError('unsupported image content')
                payload = base64.b64decode(encoded, validate=True)
                if not payload:
                    raise ValueError('empty image content')
            except (ValueError, binascii.Error):
                delivery['diagnostics'].append({'code': 'invalid_image_payload', 'content_index': index})
                label = None
                continue
            sha = hashlib.sha256(payload).hexdigest()
            info = dict(label or {})
            label = None
            # Without an adjacent label, exact declared SHA can associate metadata.
            if not info:
                matches = [i for i in metadata.values() if i.get('sha256') == sha]
                if len(matches) == 1:
                    info = {k: matches[0].get(k) for k in ('view', 'image_ref')}
            ref = info.get('image_ref')
            declared = metadata.get(ref, {})
            if declared.get('sha256') and declared['sha256'] != sha:
                delivery['diagnostics'].append({'code': 'metadata_sha256_mismatch', 'image_ref': ref})
            relative = None
            for candidate in dict.fromkeys(archive.get(ref, [])):
                path = _safe_file(root, candidate)
                if path is None:
                    delivery['diagnostics'].append({'code': 'archive_file_unavailable', 'image_ref': ref})
                    continue
                if candidate not in hash_cache:
                    hash_cache[candidate] = _sha(path)
                stats['source_files'][candidate] = hash_cache[candidate]
                if hash_cache[candidate] == sha:
                    relative = candidate
                    break
                delivery['diagnostics'].append({'code': 'archive_sha256_mismatch', 'image_ref': ref})
            verification = 'matched_archive_sha256'
            if relative is None:
                verification = 'raw_attachment_sha256'
                relative = f'trace_attachments/{sha}.{_EXTENSIONS[mime]}'
                if output is None:
                    delivery['diagnostics'].append({'code': 'asset_output_unavailable', 'content_index': index})
                    continue
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    temporary = path.with_suffix(path.suffix + '.tmp')
                    temporary.write_bytes(payload)
                    temporary.replace(path)
                elif _sha(path) != sha:
                    raise ValueError(f'Derived attachment path has different bytes: {path}')
                stats['asset_files'][relative] = sha
                stats['derived_images'] += 1
            else:
                stats['archive_matches'] += 1
            dimensions = {key: declared[key] for key in ('width', 'height')
                          if declared.get('sha256') == sha and isinstance(declared.get(key), int)
                          and declared[key] > 0}
            entry['images'].append({**dimensions, 'file': relative, 'view': info.get('view'), 'image_ref': ref,
                                    'sha256': sha, 'source': source.name, 'verification': verification,
                                    'call_id': record['id'], 'evidence_ids': sorted(record['evidence']),
                                    'content_index': index})
        delivery['verified_count'] = len(entry['images'])
        delivery['status'] = 'verified' if len(entry['images']) == count else 'partial'
        stats[delivery['status'] + '_results'] += 1
        stats['verified_images'] += len(entry['images'])
    return {**data, 'model_transcript': entries, 'tool_trace_stats': stats}
