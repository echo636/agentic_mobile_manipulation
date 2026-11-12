"""One isolated, tool-free Codex call for the visual planning subagent."""
import base64
from dataclasses import replace
import json
from pathlib import Path
import sys
import uuid

from .codex import CodexAdapter
from .types import ClientConfig
from ..records import write_json


class CodexPlannerRequest:
    def __init__(self, *, output, provider, gateway):
        self.output, self.provider, self.gateway = Path(output), provider, gateway

    def __call__(self, payload):
        output = self.output / 'calls' / str(uuid.uuid4())
        output.mkdir(parents=True)
        content = payload['input'][0]['content']
        text = '\n'.join(part['text'] for part in content if part['type'] == 'input_text')
        schema = output / 'schema.json'
        answer = output / 'answer.json'
        write_json(schema, payload['text']['format']['schema'])
        config = ClientConfig('codex', payload['model'], text, sys.executable, [], output,
            timeout=None, isolate_client_storage=True, reasoning_effort=payload['reasoning']['effort'],
            model_provider_profile=self.provider, model_provider_gateway=self.gateway)
        adapter = CodexAdapter()
        project = adapter.prepare_project(config, payload['instructions'], [])
        images, options = [], ['--output-schema', str(schema), '--output-last-message', str(answer)]
        try:
            for part in content:
                if part['type'] == 'input_image':
                    header, encoded = part['image_url'].split(',', 1)
                    image = output / (f'image-{len(images)}' + ('.png' if 'image/png' in header else '.jpg'))
                    image.write_bytes(base64.b64decode(encoded))
                    images.append(image)
                    options += ['--image', str(image)]
            project = replace(project, argv=[*project.argv[:-1], *options, '-'], stdin=text)
            run = adapter.run(project, output, None)
            events = adapter.parse(output / 'model_events.jsonl').raw_events
            usage = next((event.get('usage') for event in reversed(events)
                          if event.get('type') == 'turn.completed'), None)
            write_json(output / 'client_result.json', {'returncode': run.returncode, 'pid': run.pid,
                'duration_seconds': run.duration_seconds, 'image_count': len(images), 'usage': usage,
                'client_storage': project.client_storage, 'model': config.model,
                'reasoning_effort': config.reasoning_effort, 'robot_tools_available': False})
            if run.returncode or not answer.exists():
                raise RuntimeError('Planning client failed; see recorded client events')
            return {'status': 'completed', 'usage': usage, 'client_call': str(output), 'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': answer.read_text()}]}]}
        finally:
            # The episode and native request record retain RGB evidence; these are CLI input temporaries.
            for image in images:
                image.unlink(missing_ok=True)
