"""Actual MCP delivery, archive verification and presentation-only publication."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.tool_trace import enrich_tool_trace

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4mMAAAAASUVORK5CYII=')
SHA = hashlib.sha256(PNG).hexdigest()
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('trace_ui_publisher', REPO / 'scripts/publish_replay_ui.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def fixture(root, *, archive_bytes=PNG, images=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'frames').mkdir(exist_ok=True)
    (root / 'frames/front.png').write_bytes(archive_bytes)
    image = {'image_ref': 'rgb-00002-front', 'view': 'front', 'file': 'frames/front.png', 'sha256': SHA, 'width': 1, 'height': 1}
    (root / 'captures.jsonl').write_text(json.dumps({'images': [image]}) + '\n')
    text = json.dumps({'evidence_id': 'event-42', 'observation': {'images': [image]}})
    content = [{'type': 'text', 'text': text}]
    if images:
        content += [{'type': 'text', 'text': 'RGB view=front, image_ref=rgb-00002-front'},
                    {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(PNG).decode()}]
    event = {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': 'call-image',
             'tool': 'observe', 'result': {'content': content}}}
    # Index is intentionally NOT the transcript's original unfiltered event index.
    (root / 'model_public_events.jsonl').write_text(json.dumps(event) + '\n')
    transcript = [
        {'kind': 'assistant', 'text': 'Original output. No translation.', 'sequence': 1},
        {'kind': 'tool_result', 'call_id': 'call-image', 'source_id': 'call-image',
         'tool': 'observe', 'text_blocks': [c['text'] for c in content if c['type'] == 'text'],
         'image_count': int(images), 'source_event_index': 99, 'sequence': 2}]
    data = {'run_id': 'test-actual-trace', 'model_transcript': transcript, 'audit': None,
            'video': None, 'has_public_trace': True,
            'steps': [{'index': 1, 'after': {'images': [image]}}]}
    (root / 'replay.json').write_text(json.dumps(data))
    (root / 'replay.html').write_text('old html')
    return data, event


class ToolTraceTests(unittest.TestCase):
    def test_filtered_stream_aligns_by_id_and_verifies_actual_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, _ = fixture(root)
            original = json.dumps(data, sort_keys=True)
            result = enrich_tool_trace(data, root, root / 'staged')
            entry = result['model_transcript'][1]
            self.assertEqual(json.dumps(data, sort_keys=True), original)
            self.assertEqual(entry['source_event_index'], 99)
            self.assertEqual(entry['image_delivery']['source_line'], 1)
            self.assertEqual(entry['image_delivery']['status'], 'verified')
            self.assertEqual(entry['images'][0]['file'], 'frames/front.png')
            self.assertEqual(entry['images'][0]['sha256'], SHA)
            self.assertEqual((entry['images'][0]['width'], entry['images'][0]['height']), (1, 1))
            self.assertEqual(entry['images'][0]['verification'], 'matched_archive_sha256')
            self.assertFalse((root / 'staged').exists())

    def test_metadata_or_after_image_does_not_prove_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, _ = fixture(root, images=False)
            result = enrich_tool_trace(data, root)
            entry = result['model_transcript'][1]
            self.assertEqual(entry['images'], [])
            self.assertEqual(entry['image_delivery']['status'], 'verified')
            self.assertEqual(entry['image_delivery']['attachment_count'], 0)

    def test_mismatch_uses_actual_attachment_and_deduplicates_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, event = fixture(root, archive_bytes=b'wrong archived frame')
            event['item']['result']['content'] += event['item']['result']['content'][1:]
            (root / 'model_public_events.jsonl').write_text(json.dumps(event) + '\n')
            result = enrich_tool_trace(data, root, root / 'stage')
            entry = result['model_transcript'][1]
            self.assertEqual(len(entry['images']), 2)
            self.assertEqual(len(list((root / 'stage/trace_attachments').iterdir())), 1)
            self.assertEqual((root / 'stage' / entry['images'][0]['file']).read_bytes(), PNG)
            self.assertEqual(entry['images'][0]['verification'], 'raw_attachment_sha256')
            self.assertIn('archive_sha256_mismatch', [d['code'] for d in entry['image_delivery']['diagnostics']])
            self.assertEqual((root / 'frames/front.png').read_bytes(), b'wrong archived frame')

    def test_missing_raw_is_explicit_and_preserves_original_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, _ = fixture(root)
            (root / 'model_public_events.jsonl').unlink()
            for source in (root, None):
                result = enrich_tool_trace(data, source)
                self.assertEqual(result['model_transcript'][0], data['model_transcript'][0])
                entry = result['model_transcript'][1]
                self.assertEqual(entry['text_blocks'], data['model_transcript'][1]['text_blocks'])
                self.assertEqual(entry['images'], [])
                self.assertEqual(entry['image_delivery']['status'], 'unverified')

    def test_evidence_ids_disambiguate_reused_calls_and_reject_wrong_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, event = fixture(root)
            wrong = json.loads(json.dumps(event))
            wrong['item']['result']['content'][0]['text'] = '{"evidence_id":"other-attempt"}'
            (root / 'model_public_events.jsonl').write_text(json.dumps(wrong) + '\n' + json.dumps(event) + '\n')
            result = enrich_tool_trace(data, root)
            self.assertEqual(result['model_transcript'][1]['image_delivery']['source_line'], 2)
            (root / 'model_public_events.jsonl').write_text(json.dumps(wrong) + '\n')
            entry = enrich_tool_trace(data, root)['model_transcript'][1]
            self.assertEqual(entry['images'], [])
            self.assertEqual(entry['image_delivery']['status'], 'unverified')

    def test_invalid_base64_and_unsafe_archive_paths_never_become_guessed_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            data, event = fixture(root)
            (Path(tmp) / 'outside.png').write_bytes(PNG)
            (root / 'captures.jsonl').write_text(json.dumps({'images': [{'image_ref': 'rgb-00002-front', 'file': '../outside.png'}]}))
            data['steps'] = []
            result = enrich_tool_trace(data, root, root / 'stage')
            self.assertTrue(result['model_transcript'][1]['images'][0]['file'].startswith('trace_attachments/'))
            event['item']['result']['content'][-1]['data'] = 'not base64!'
            (root / 'model_public_events.jsonl').write_text(json.dumps(event) + '\n')
            entry = enrich_tool_trace(data, root)['model_transcript'][1]
            self.assertEqual(entry['images'], [])
            self.assertEqual(entry['image_delivery']['status'], 'partial')

    def test_publication_stages_assets_then_preserves_evidence_on_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'reports'
            run = root / 'runs/episode'
            data, _ = fixture(run, archive_bytes=b'wrong archived frame')
            selection = Path(tmp) / 'selected.json'
            selection.write_text(json.dumps({'replays': ['runs/episode/replay.json']}))
            record = Path(tmp) / 'publication'
            evidence = {name: (run / name).read_bytes() for name in
                        ('replay.json', 'model_public_events.jsonl', 'captures.jsonl', 'frames/front.png')}
            args = argparse.Namespace(reports_root=root, record_dir=record, selection_json=selection,
                                      scope=None, comparison=False, expected_source=None)
            manifest = publisher.prepare(args)
            self.assertEqual(manifest['status'], 'planned')
            self.assertEqual(manifest['derived_attachment_count'], 1)
            self.assertFalse((run / 'trace_attachments').exists())
            self.assertEqual((run / 'replay.html').read_text(), 'old html')
            result = publisher.apply(record)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual((record / 'before/runs/episode/replay.html').read_text(), 'old html')
            self.assertEqual((run / f'trace_attachments/{SHA}.png').read_bytes(), PNG)
            self.assertIn('raw_attachment_sha256', (run / 'replay.html').read_text())
            for name, payload in evidence.items():
                self.assertEqual((run / name).read_bytes(), payload)

    def test_publication_rejects_changed_raw_evidence_before_writing_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'reports'
            run = root / 'runs/episode'
            fixture(run)
            record = Path(tmp) / 'publication'
            args = argparse.Namespace(reports_root=root, record_dir=record, selection_json=None,
                                      scope=['runs'], comparison=False, expected_source=None)
            publisher.prepare(args)
            (run / 'model_public_events.jsonl').write_text('changed after staging')
            with self.assertRaisesRegex(ValueError, 'Trace evidence changed'):
                publisher.apply(record)
            self.assertEqual((run / 'replay.html').read_text(), 'old html')


if __name__ == '__main__':
    unittest.main()
