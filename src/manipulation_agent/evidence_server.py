"""Episode-local RGB feedback between the motor MCP endpoint and its clients."""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
import tempfile

from mcp import types
from PIL import Image

from .agent.action_evidence import EvidenceMemory, INSPECT_TOOL, digest, sample, valid_point

VIEWS = {'front', 'back', 'left', 'right'}
FRAME_KEYS = ('image_ref', 'view', 'width', 'height', 'mime_type', 'sha256')
CAMERA_TOOLS = {'initialize', 'act', 'look'}
BASIC_TOOLS = CAMERA_TOOLS | {'list_skills', 'read_skill', 'finish'}
PROTOCOL_ERRORS = {'stale_observation', 'stale_image_ref', 'unknown_image_ref', 'invalid_arguments',
                   'invalid_target', 'invalid_request_id', 'episode_closed', 'budget_exhausted',
                   'agent_step_budget_exhausted', 'episode_cancelled', 'episode_timeout'}
PANEL_LABEL = 'RGB selection history (derived, read-only). '


def txt(value):
    return types.TextContent(type='text', text=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def reply(body, parts=(), *, error=False):
    # Construct a fresh MCP envelope: annotations, untrusted text, structuredContent and isError
    # in the upstream motor reply may disclose private postcondition checks.
    return types.CallToolResult(content=[txt(body), *parts], isError=error)


def receipt_id(name, args, observation):
    # Correlate a clipped native CLI transcript to its actual RGB packet without
    # exposing a simulator event counter. Identical requests at a revision are idempotent.
    identity = [name, args, observation['revision'], [f['sha256'] for f in observation['images']]]
    return 'rgb-' + digest(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode())


def public_hand(raw):
    hand = raw.get('hand') or {}
    # Older simulated grippers report their attachment constraint as occupancy.
    # A measured gripper reading must identify its source before it can cross this boundary.
    if hand.get('source') != 'gripper_sensor':
        return 'unavailable'
    value = hand.get('state')
    return value if value in ('holding', 'empty') else 'unavailable'


def public_result(name, args, raw):
    if name == 'act' or name == 'look':
        code = (raw.get('error') or {}).get('code')
        if code in PROTOCOL_ERRORS:
            return {'ok': False, 'error': {'code': code, 'message': 'Check the request and the current revision.'}}
        return {'ok': True, 'effect': {'primitive': args.get('primitive', name),
            'status': 'request_processed', 'verification': 'inspect_returned_rgb'},
            'hand': {'state': public_hand(raw)}}
    if name == 'initialize':
        if raw.get('initialized') is not True:
            return {'ok': False, 'error': {'code': 'initialize_failed'}}
        return {'ok': True, 'initialized': True, 'hand': {'state': public_hand(raw)}}
    if name == 'finish':
        return {'ok': raw.get('closed') is True, 'closed': raw.get('closed') is True,
                'agent_outcome': args.get('outcome')}
    if name == 'list_skills' and raw.get('ok') and 'skills' in raw:
        return {'ok': True, 'skills': [{k: item[k] for k in ('name','description','resources')} for item in raw['skills']],
                'bundle_sha256': raw['bundle_sha256']}
    if name == 'read_skill' and raw.get('ok') and 'text' in raw:
        return {k: raw[k] for k in ('ok', 'name', 'resource', 'text', 'bundle_sha256')}
    return {'ok': False, 'error': {'code': 'tool_unavailable'}}


def attachments(raw, parts):
    obs = raw['observation']
    frames = [{k: f[k] for k in FRAME_KEYS} for f in obs['images']]
    if (obs.get('observation_mode') != 'rgb_only' or len(frames) != 4 or
            {f['view'] for f in frames} != VIEWS or len({f['image_ref'] for f in frames}) != 4):
        raise ValueError('Invalid current RGB packet')
    schema = {'observation_mode': 'rgb_only', 'revision': obs['revision'], 'images': frames}
    reference = raw.get('reference_image')
    metadata = list(frames)
    if reference:
        reference = {'revision': reference['revision'], 'image': {k: reference['image'][k] for k in FRAME_KEYS}}
        metadata.append(reference['image'])
    images = [p for p in parts if p.type == 'image']
    if len(images) != len(metadata):
        raise ValueError('RGB metadata / attachment count mismatch')
    verified = []
    for f, p in zip(metadata, images):
        data = base64.b64decode(p.data, validate=True)
        if p.mimeType != f['mime_type'] or f['mime_type'] not in ('image/jpeg','image/png') or digest(data) != f['sha256']:
            raise ValueError('RGB attachment hash or MIME mismatch')
        with Image.open(io.BytesIO(data)) as im:
            if im.size != (f['width'], f['height']) or Image.MIME[im.format] != f['mime_type']:
                raise ValueError('RGB dimensions or format mismatch')
        verified.append({**f, 'data': p.data})
    return schema, verified[:4], reference, verified[4:]


def public_images(frames, historical=()):
    parts = []
    for f in [*frames, *historical]:
        label = ('RGB' if f in frames else 'Historical RGB for comparison only.')
        parts.extend((txt(f"{label} view={f['view']}, image_ref={f['image_ref']}"),
                      types.ImageContent(type='image', mimeType=f['mime_type'], data=f['data'],
                                         _meta={'codex/imageDetail': 'original'})))
    return parts


def planner_parts(result):
    """Keep the original-camera cardinality check and verify independent derived imagery."""
    first = result.content[0] if result.content else None
    if first is None or first.type != 'text' or not first.text.startswith('{'):
        return result, []
    evidence = json.loads(first.text).get('visual_evidence')
    if not evidence:
        return result, []
    label, img = result.content[-2:]
    if (label.type != 'text' or not label.text.startswith(PANEL_LABEL) or img.type != 'image'
            or img.mimeType != 'image/png' or digest(base64.b64decode(img.data, validate=True)) != evidence['sha256']):
        raise ValueError('Selection panel does not match its evidence record')
    return result.model_copy(update={'content': result.content[:-2]}), [
        {'type': 'input_text', 'text': label.text + json.dumps(evidence, ensure_ascii=False)},
        {'type': 'input_image', 'image_url': 'data:image/png;base64,' + img.data, 'detail': 'original'}]


class Feedback:
    def __init__(self, mode, path=None):
        if mode not in ('sensor_only', 'evidence'):
            raise ValueError('Unknown action feedback mode')
        self.enabled = mode == 'evidence'
        self.path = Path(path) if path else None
        self.current = {}
        self.observation = None
        self.memory = EvidenceMemory()
        if self.path and self.path.exists():
            state = json.loads(self.path.read_text())
            self.observation = state['observation']
            self.current = {f['image_ref']: f for f in state['current']}
            self.memory = EvidenceMemory(state['memory'])

    def persist(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(dict(observation=self.observation, current=list(self.current.values()),
                               memory=self.memory.state()), out, separators=(',', ':'))
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def render(self, body, history=(), inspection=None):
        parts = public_images(list(self.current.values()), history)
        if self.enabled:
            info, data = self.memory.panel(self.current, inspection)
            if data:
                body['visual_evidence'] = info
                parts.extend((txt(PANEL_LABEL + 'Left: source view. Middle: prior selection. Right: current pixels at those coordinates.'),
                              types.ImageContent(type='image', mimeType='image/png',
                              data=base64.b64encode(data).decode('ascii'), _meta={'codex/imageDetail': 'original'})))
        return reply(body, parts, error=not body['ok'])

    def inspect(self, args):
        if not self.observation:
            return reply({'ok': False, 'error': {'code': 'initialize_first'}}, error=True)
        ref = args.get('image_ref')
        src = self.current.get(ref) if isinstance(ref, str) else None
        if (set(args) != {'revision','image_ref','point'} or type(args.get('revision')) is not int
                or args['revision'] != self.observation['revision'] or not src or not valid_point(args.get('point'))):
            return self.render({'ok': False, 'error': {'code': 'invalid_inspection'},
                                'observation': self.observation})
        crop = sample(src, args['point'], 'inspect_target', args['revision'])
        return self.render({'ok': True, 'observation': self.observation,
                            'evidence_id': receipt_id('inspect_target', args, self.observation), 'hand': {'state': self.memory.occupancy}}, inspection=crop)

    def invalidate(self, code='observation_unavailable'):
        self.current = {}
        self.observation = None
        self.memory = EvidenceMemory()
        self.persist()
        return reply({'ok': False, 'error': {'code': code}}, error=True)

    def receive(self, name, args, upstream):
        first = upstream.content[0] if upstream.content else None
        if first is None or first.type != 'text' or not first.text.startswith('{'):
            if name in CAMERA_TOOLS:
                return self.invalidate()
            return reply({'ok': False, 'error': {'code': 'upstream_unavailable'}}, error=True)
        raw = json.loads(first.text)
        body = public_result(name, args, raw)
        # Legacy motors can attach their pre-action image on interruption, including a
        # nominally successful motor return that raced with a request to finish.
        stopped = (raw.get('error') or {}).get('code') in {'episode_cancelled', 'episode_timeout', 'episode_closed'}
        same_revision = (name in {'act', 'look'} and body['ok'] and
                         isinstance(args.get('revision'), int) and
                         (raw.get('observation') or {}).get('revision') == args['revision'])
        missing = 'observation' not in raw and body['ok']
        if name in CAMERA_TOOLS and (stopped or same_revision or missing):
            code = raw['error']['code'] if stopped else 'observation_unavailable'
            return self.invalidate(code)
        if name not in CAMERA_TOOLS or 'observation' not in raw:
            return reply(body, error=not body['ok'])
        obs, current, reference, history = attachments(raw, upstream.content)
        if reference and (name != 'initialize' or reference['image']['image_ref'] != args.get('reference_image_ref')):
            raise ValueError('Unrequested historical RGB attachment')
        prev = (self.observation or {}).get('revision')
        rev = obs['revision']
        if prev is not None and rev < prev:
            return self.render({'ok': False, 'error': {'code': 'stale_observation'}, 'observation': self.observation})
        hand = public_hand(raw)
        if rev != prev:
            if (name in ('act', 'look') and self.enabled and prev is not None and
                    args.get('revision') == prev and rev == prev + 1 and body['ok']):
                self.memory.action(args.get('primitive', name), args.get('target'), self.current, current, prev, hand)
            else:
                self.memory = EvidenceMemory()
        self.memory.observation(hand)
        self.current = {f['image_ref']: f for f in current}
        self.observation = obs
        body['observation'] = obs
        body['evidence_id'] = receipt_id(name, args, obs)
        if reference:
            body['reference_image'] = reference
        self.persist()
        return self.render(body, history)


async def create_server(session, config):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.tools import Tool
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
    from pydantic import ConfigDict
    import fcntl

    catalog = (await session.list_tools()).tools
    if not {t.name for t in catalog} <= BASIC_TOOLS:
        raise ValueError('Feedback requires a minimal or basic-skills robot endpoint')
    if config['mode'] == 'evidence':
        catalog.append(types.Tool(**INSPECT_TOOL))
    class Arguments(ArgModelBase):
        model_config = ConfigDict(extra='allow')
        def model_dump_one_level(self):
            return dict(self.model_extra or {})
    lock = asyncio.Lock()
    state = Path(config['state'])
    state.parent.mkdir(parents=True, exist_ok=True)
    def wrap(name):
        async def invoke(**args):
            if name == 'finish' or name in ('list_skills', 'read_skill'):
                upstream = await session.call_tool(name, args)
                return Feedback(config['mode']).receive(name, args, upstream)
            async with lock:
                # Separate MCP transports can attach to the same live robot on resume.
                with state.with_suffix('.lock').open('a') as lock_file:
                    await asyncio.to_thread(fcntl.flock, lock_file, fcntl.LOCK_EX)
                    feedback = Feedback(config['mode'], state)
                    if name == 'inspect_target':
                        return feedback.inspect(args)
                    try:
                        upstream = await session.call_tool(name, args)
                        return feedback.receive(name, args, upstream)
                    except asyncio.CancelledError:
                        feedback.invalidate()
                        raise
                    except Exception as exc:
                        # The motor may have moved before the transport or image check failed.
                        logging.getLogger(__name__).warning('Camera reply unavailable (%s)', type(exc).__name__)
                        return feedback.invalidate()
        return invoke
    return FastMCP('robot-rgb-feedback', log_level='WARNING', tools=[
        Tool(name=t.name, description=t.description or '', parameters=t.inputSchema,
             fn=wrap(t.name), is_async=True, fn_metadata=FuncMetadata(arg_model=Arguments),
             annotations=t.annotations, meta=t.meta) for t in catalog])


async def run(config):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    upstream = StdioServerParameters(command=config['upstream_command'], args=config['upstream_args'], env=dict(os.environ))
    async with stdio_client(upstream) as streams, ClientSession(*streams) as session:
        await session.initialize()
        await (await create_server(session, config)).run_stdio_async()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    asyncio.run(run(json.loads(p.parse_args().config.read_text())))
