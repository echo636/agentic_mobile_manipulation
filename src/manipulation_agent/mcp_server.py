"""Official SDK FastMCP proxy for one owner-thread simulator episode.

All transports share the same bridge, action budget and image revision. HTTP is
loopback-only and is not a multi-tenant episode service. Simulator calls remain
queued by bridge.py; this process never calls the simulator API directly.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
import hashlib
import ipaddress
import json
import sys
import time
import uuid
import urllib.request
from urllib.parse import quote

_startup_started = time.monotonic()



# Harness option (off by default): the next session starts without this
# conversation or its images. Ask for facts only, never an ordering strategy.
HANDOFF_NOTE_REQUEST = (
    ' Before ending the turn, write a handoff note as your final message for a fresh session that will have '
    'no access to this conversation or its images. State facts only: (1) task requirements already completed '
    'and the evidence you saw; (2) requirements not yet completed; (3) where the relevant objects, furniture, '
    'appliances and rooms are, described visually, with the state_memory location/container/item ids you '
    'know for them; (4) what each opened or closed container or appliance now holds, any running or finished '
    'process, and what the robot is holding.'
)

def _startup(stage):
    if __name__ == '__main__':
        print('MAS_MCP_STARTUP ' + json.dumps({
            'stage': stage, 'seconds': time.monotonic() - _startup_started,
        }), file=sys.stderr, flush=True)


_startup('proxy_import')
from .observations.boundary import labeled_rgb_images
from .transport.bridge import rpc
from .runtime.deadline import tool_wait_seconds
from .tools.finish_guard import FinishGuard
from .agent.state_memory import StateMemory
from .agent.task_graph_planner import TaskGraphSession
from .contracts import SkillError, validate

# Harness option (--click-echo on, default off): crop around the model's selected pixel on the chosen RGB image.
ECHO_HALF_PX = 80
ECHO_SCALE = 2


def echo_caption(image_ref, size):
    return (f'Click echo: your selected point on {image_ref} (the image you selected on, before '
            f'this action). The crop shows {size}x{size} px around it, enlarged {ECHO_SCALE}x; the magenta ring is '
            'centred on the exact pixel you selected.')


def click_echo(data, point):
    import io
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(data)).convert('RGB')
    width, height = image.size
    x = min(width - 1, max(0, round(float(point[0]) * (width - 1))))
    y = min(height - 1, max(0, round(float(point[1]) * (height - 1))))
    size = min(2 * ECHO_HALF_PX, width, height)
    left = min(max(0, x - size // 2), width - size)
    top = min(max(0, y - size // 2), height - size)
    crop = image.crop((left, top, left + size, top + size)).resize((size * ECHO_SCALE, size * ECHO_SCALE), Image.LANCZOS)
    cx, cy = (x - left) * ECHO_SCALE + ECHO_SCALE // 2, (y - top) * ECHO_SCALE + ECHO_SCALE // 2
    draw = ImageDraw.Draw(crop)
    for colour, line in (((0, 0, 0), 5), ((255, 0, 255), 2)):
        draw.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], outline=colour, width=line)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            draw.line([cx + 6 * dx, cy + 6 * dy, cx + 26 * dx, cy + 26 * dy], fill=colour, width=line)
    out = io.BytesIO()
    crop.save(out, format='JPEG', quality=90)
    return out.getvalue(), size


# Harness transport option (--max-inline-image-bytes N, default 0 = off):
# Antigravity CLI (`agy`) offloads MCP ImageContent blocks > 10,240 bytes to disk files instead of
# attaching them inline to the model turn. Adaptive WebP re-encoding on the MCP proxy side keeps
# normalized [0, 1] coordinates 100% invariant while guaranteeing inline multimodal delivery.
def encode_within_byte_limit(data: bytes, mime_type: str, max_bytes: int) -> tuple[bytes, str]:
    if max_bytes <= 0 or len(data) <= max_bytes:
        return data, mime_type
    import io
    from PIL import Image
    image = Image.open(io.BytesIO(data)).convert('RGB')
    orig_w, orig_h = image.size
    for scale_px in (min(orig_w, orig_h), 448, 384, 320, 256, 192, 160, 128, 96):
        if scale_px > min(orig_w, orig_h):
            continue
        if scale_px == min(orig_w, orig_h) and orig_w == orig_h:
            resized = image
        else:
            w = max(64, round(orig_w * scale_px / max(orig_w, orig_h)))
            h = max(64, round(orig_h * scale_px / max(orig_w, orig_h)))
            resized = image.resize((w, h), Image.LANCZOS)
        for quality in (75, 65, 55, 45, 35, 25, 15):
            buf = io.BytesIO()
            resized.save(buf, format='WEBP', quality=quality)
            encoded = buf.getvalue()
            if len(encoded) <= max_bytes:
                return encoded, 'image/webp'
    buf = io.BytesIO()
    image.resize((64, 64), Image.LANCZOS).save(buf, format='WEBP', quality=10)
    return buf.getvalue(), 'image/webp'


def fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=30) as response:
        return response.read()


def loopback_host(value):
    if value == 'localhost': return value
    try:
        if ipaddress.ip_address(value).is_loopback: return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('MCP HTTP supports loopback hosts only (one shared episode)')


async def create_server(url: str, *, host='127.0.0.1', port=29431, finish_guard=False, guard_state=None,
                        enable_click_echo=False, max_inline_image_bytes=0, state_memory=False,
                        rolling_handoff_steps=0, planning_mode='off', handoff_note=False):
    """Freeze the bridge catalog and register it without changing its schemas.

    SDK Tool/FuncMetadata support schemas from external catalogs. The permissive
    argument carrier intentionally preserves JSON types and unknown fields so
    the owner-thread harness can reject them and record the original request.
    It also preserves the existing nullable-default compatibility for old act
    clients; generating a Pydantic signature here would coerce or drop inputs.
    """
    if planning_mode not in ('off', 'task_graph'):
        raise ValueError('Unknown planning mode')
    _startup('sdk_import')
    from mcp import types
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.tools import Tool
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
    from pydantic import ConfigDict

    _startup('bridge_health')
    host = loopback_host(host)
    health = json.loads(await asyncio.to_thread(fetch, url.rstrip('/') + '/healthz'))
    if not health.get('ready'):
        raise RuntimeError('Simulator bridge is not ready')
    _startup('catalog_registration')
    catalog = health['tools']
    rpc_timeout=health.get('rpc_timeout_seconds',tool_wait_seconds(1800))
    if len({spec['name'] for spec in catalog}) != len(catalog):
        raise ValueError('Duplicate bridge tool names')
    lock = asyncio.Lock()
    # Harness-side option, off by default: defer early finishes while act/look budget remains.
    guard = FinishGuard(guard_state, state_memory=state_memory) if finish_guard else None
    proxy_memory = StateMemory() if state_memory else None
    segment_steps = 0
    bridge_has_task_graph = any(spec['name'] == 'plan_task_graph' for spec in catalog)
    local_task_graph = (
        TaskGraphSession()
        if (planning_mode == 'task_graph' and not bridge_has_task_graph)
        else None
    )

    class BridgeArguments(ArgModelBase):
        model_config = ConfigDict(extra='allow')

        def model_dump_one_level(self):
            return dict(self.model_extra or {})

    def wrap(name):
        async def invoke(**arguments):
            async def call():
                nonlocal segment_steps
                if rolling_handoff_steps > 0 and name in ('act', 'look') and segment_steps >= rolling_handoff_steps:
                    checkpoint = {
                        'ok': False,
                        'error': {
                            'code': 'rolling_handoff_checkpoint',
                            'action_step_used': False,
                            'world_may_have_changed': False,
                            'message': (
                                f'Rolling context checkpoint reached after {segment_steps} act/look steps in this '
                                'session. Stop calling tools and end this turn briefly now (do NOT call finish); '
                                'the controller will immediately continue this live episode in a fresh context '
                                'window with your state_memory.'
                                + (HANDOFF_NOTE_REQUEST if handoff_note else '')
                            ),
                        },
                    }
                    return [types.TextContent(type='text', text=json.dumps(checkpoint, ensure_ascii=False))]
                target = arguments.get('target') if (enable_click_echo and name == 'act' and isinstance(arguments, dict)) else None
                pre_source = None
                if (isinstance(target, dict) and isinstance(target.get('image_ref'), str)
                        and isinstance(target.get('point'), list) and len(target['point']) == 2):
                    try:
                        pre_source = await asyncio.to_thread(
                            fetch, url.rstrip('/') + '/image/' + quote(target['image_ref'], safe='')
                        )
                    except Exception:
                        pre_source = None
                result = await asyncio.to_thread(rpc, url, name, arguments, str(uuid.uuid4()),timeout=rpc_timeout)
                if proxy_memory is not None and isinstance(result, dict):
                    proxy_memory.observe(name, arguments if isinstance(arguments, dict) else {}, result)
                    if name in ('initialize', 'act', 'look') and 'state_memory' not in result:
                        result['state_memory'] = proxy_memory.public_view(
                            include_handoff=(name == 'initialize' and proxy_memory.steps_observed > 0)
                        )
                if guard is not None:
                    guard.observe(result, name, arguments)
                if local_task_graph is not None:
                    if proxy_memory is not None:
                        local_task_graph.sync_from_state_memory(proxy_memory)
                    local_task_graph.observe_public_action(name, arguments if isinstance(arguments, dict) else None, result)
                if rolling_handoff_steps > 0 and name in ('act', 'look') and isinstance(result, dict):
                    step_used = not (isinstance(result.get('error'), dict)
                                     and result['error'].get('action_step_used') is False)
                    if step_used:
                        segment_steps += 1
                        if segment_steps == rolling_handoff_steps:
                            result['rolling_handoff'] = {
                                'due': True,
                                'segment_steps': segment_steps,
                                'message': (
                                    'Rolling context checkpoint reached. If the task is not yet complete, end this '
                                    'turn briefly without calling finish so the controller can continue in a fresh '
                                    'context window with state_memory.'
                                    + (HANDOFF_NOTE_REQUEST if handoff_note else '')
                                ),
                            }
                content = [types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]
                if pre_source is not None and result.get('observation') is not None:
                    try:
                        echo, size = click_echo(pre_source, target['point'])
                        echo, echo_mime = encode_within_byte_limit(echo, 'image/jpeg', max_inline_image_bytes)
                        content.append(types.TextContent(type='text', text=echo_caption(target['image_ref'], size)))
                        content.append(types.ImageContent(
                            type='image', data=base64.b64encode(echo).decode(), mimeType=echo_mime,
                            _meta={'codex/imageDetail': 'original'}))
                    except Exception:
                        pass
                observation = result.get('observation', {})
                if observation.get('observation_mode') == 'rgb_only':
                    for frame, label in labeled_rgb_images(observation, result.get('reference_image')):
                        data = await asyncio.to_thread(fetch, url.rstrip('/') + '/image/' + quote(frame['image_ref'], safe=''))
                        if hashlib.sha256(data).hexdigest() != frame['sha256']:
                            raise RuntimeError('RGB image content hash mismatch')
                        out_data, out_mime = encode_within_byte_limit(data, frame['mime_type'], max_inline_image_bytes)
                        content.append(types.TextContent(type='text', text=label))
                        content.append(types.ImageContent(
                            type='image', data=base64.b64encode(out_data).decode(), mimeType=out_mime,
                            _meta={'codex/imageDetail': 'original'}))
                return content
            # Finish must reach bridge.request_finish while an action is pending.
            # The bridge sets only stop intent here; simulator access and scoring
            # still happen serially on its owner thread, after action cancellation.
            if name == 'finish':
                deferred = guard.deferral(arguments) if guard is not None else None
                if deferred is not None:
                    return [types.TextContent(type='text', text=json.dumps(deferred, ensure_ascii=False))]
                return await call()
            # Serialize ordinary actions and their RGB downloads to retain refs.
            async with lock:
                return await call()
        return invoke

    registered = [Tool(
        fn=wrap(spec['name']), name=spec['name'], description=spec.get('description', ''),
        parameters=deepcopy(spec['inputSchema']), is_async=True,
        fn_metadata=FuncMetadata(arg_model=BridgeArguments),
        annotations=types.ToolAnnotations(**spec['annotations']) if spec.get('annotations') else None,
        meta=deepcopy(spec.get('_meta')),
    ) for spec in catalog]
    if local_task_graph is not None:
        from .tools import tool_specs
        from .tools.intent import validate_intent
        require_intent = any(
            'intent' in ((spec.get('inputSchema') or {}).get('properties') or {})
            for spec in catalog
        )
        tg_spec = next(
            s for s in tool_specs('minimal', require_intent=require_intent, planning_mode='task_graph')
            if s['name'] == 'plan_task_graph'
        )

        async def invoke_local_task_graph(**arguments):
            async with lock:
                try:
                    if require_intent:
                        validate_intent(arguments)
                    validate(arguments, tg_spec['inputSchema'])
                    exec_args = {k: v for k, v in arguments.items() if k != 'intent'}
                    if proxy_memory is not None:
                        local_task_graph.sync_from_state_memory(proxy_memory)
                    payload = {'ok': True, **local_task_graph.update_and_solve(exec_args)}
                except SkillError as exc:
                    payload = {'ok': False, 'error': {'code': exc.code, 'message': str(exc)}}
                return [types.TextContent(type='text', text=json.dumps(payload, ensure_ascii=False))]

        registered.append(Tool(
            fn=invoke_local_task_graph,
            name=tg_spec['name'],
            description=tg_spec.get('description', ''),
            parameters=deepcopy(tg_spec['inputSchema']),
            is_async=True,
            fn_metadata=FuncMetadata(arg_model=BridgeArguments),
            annotations=types.ToolAnnotations(**tg_spec['annotations']) if tg_spec.get('annotations') else None,
            meta=deepcopy(tg_spec.get('_meta')),
        ))
    return FastMCP(
        'manipulation-agentic-system', tools=registered, host=host, port=port,
        instructions='One shared simulator episode. All clients share image revisions, budgets and closure. '
                     'Select targets from the latest RGB. Tool completion is not task success.',
        log_level='WARNING',
    )


async def run(url: str, *, transport='stdio', host='127.0.0.1', port=29431, finish_guard=False, guard_state=None,
              enable_click_echo=False, max_inline_image_bytes=0, state_memory=False, rolling_handoff_steps=0,
              planning_mode='off', handoff_note=False):
    server = await create_server(url, host=host, port=port, finish_guard=finish_guard, guard_state=guard_state,
                                 enable_click_echo=enable_click_echo,
                                 max_inline_image_bytes=max_inline_image_bytes,
                                 state_memory=state_memory,
                                 rolling_handoff_steps=rolling_handoff_steps,
                                 planning_mode=planning_mode, handoff_note=handoff_note)
    _startup('serving')
    if transport == 'stdio':
        await server.run_stdio_async()
    elif transport == 'sse':
        await server.run_sse_async()
    elif transport == 'streamable-http':
        await server.run_streamable_http_async()
    else:
        raise ValueError('Unknown MCP transport')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', default='http://127.0.0.1:29430')
    parser.add_argument('--transport', choices=('stdio', 'sse', 'streamable-http'), default='stdio')
    parser.add_argument('--host', type=loopback_host, default='127.0.0.1')
    parser.add_argument('--port', type=int, default=29431)
    parser.add_argument('--finish-guard', choices=('off', 'on'), default='off',
                        help='harness option: defer early finishes while act/look budget remains')
    parser.add_argument('--guard-state', default=None, help='finish-guard state file kept across client restarts')
    parser.add_argument('--click-echo', choices=('off', 'on'), default='off',
                        help='harness option: attach enlarged crop around selected pixel on act calls')
    parser.add_argument('--max-inline-image-bytes', type=int, default=0,
                        help='harness option: re-encode MCP images above N bytes to WebP so CLI clients attach them inline (0=off)')
    parser.add_argument('--state-memory', choices=('off', 'on'), default='off',
                        help='harness option: attach structured public state memory and enable memory-aware finish guard')
    parser.add_argument('--rolling-handoff-steps', type=int, default=0,
                        help='harness option: pause session after N act/look steps for rolling context handoff (0=off)')
    parser.add_argument('--planning-mode', choices=('off', 'task_graph'), default='off',
                        help='harness option: general precondition and cost-aware task graph planner (default off)')
    parser.add_argument('--handoff-progress-note', choices=('off', 'on'), default='off',
                        help='harness option: at the rolling checkpoint, ask the session for a factual handoff note')
    args = parser.parse_args()
    asyncio.run(run(args.bridge, transport=args.transport, host=args.host, port=args.port,
                    finish_guard=args.finish_guard == 'on', guard_state=args.guard_state,
                    enable_click_echo=args.click_echo == 'on',
                    max_inline_image_bytes=max(0, int(args.max_inline_image_bytes)),
                    state_memory=args.state_memory == 'on',
                    rolling_handoff_steps=max(0, int(args.rolling_handoff_steps)),
                    planning_mode=args.planning_mode,
                    handoff_note=args.handoff_progress_note == 'on'))


if __name__ == '__main__':
    main()
