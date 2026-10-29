"""Exercise real MCP transport against a running four-camera bridge.

This is a scripted interface probe, not an LLM task-success experiment.
"""
import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import os
import socket
from datetime import datetime, timezone
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def now(): return datetime.now(timezone.utc).isoformat()


async def probe(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    trace = []
    async with stdio_client(StdioServerParameters(command=sys.executable,
            args=['-m','manipulation_agent.mcp_server','--bridge',args.bridge],
            env={'PYTHONPATH':os.environ.get('PYTHONPATH',str(Path(__file__).resolve().parents[1]/'src'))})) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {'start_observation','get_observation','cancel_observation'} <= tools
            async def call(name, parameters):
                at = now(); start = time.monotonic()
                r = await client.call_tool(name, parameters)
                payload = json.loads(r.content[0].text)
                row = {'tool':name,'arguments':parameters,'started_at':at,'returned_at':now(),
                       'duration_seconds':time.monotonic()-start,'result':payload,
                       'image_hashes':[hashlib.sha256(base64.b64decode(c.data)).hexdigest() for c in r.content if c.type=='image']}
                trace.append(row)
                (output/'calls.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in trace))
                obs = payload.get('observation')
                if obs:
                    assert len(row['image_hashes']) == 4
                    assert row['image_hashes'] == [f['sha256'] for f in obs['images']]
                    assert {f['view'] for f in obs['images']} == {'front','back','left','right'}
                else: assert not row['image_hashes']
                return payload

            start = await call('start_observation',{})
            assert start['job']['status']=='planned' and 'observation' not in start
            await call('list_skills',{})
            await call('read_skill',{'name':'visual-exploration','resource':'SKILL.md'})
            job_id = start['job']['job_id']
            for _ in range(80):
                ready = await call('get_observation',{'job_id':job_id})
                if ready['job']['status'] not in {'planned','running'}: break
                await asyncio.sleep(.1)
            assert ready['job']['status']=='passed' and not ready['job']['stale']
            assert ready['observation']['capture']['sim_step']==0
            first = ready['observation']
            await call('observe',{})
            stale = await call('get_observation',{'job_id':job_id})
            assert stale['job']['stale']
            rejected = await call('act',{'primitive':'navigate_to','target':{'image_ref':first['images'][0]['image_ref'],'point':[.5,.5]},'revision':first['revision']})
            assert rejected['error']['code']=='stale_image_ref'
            cancelled = await call('start_observation',{})
            ident = cancelled['job']['job_id']
            await call('cancel_observation',{'job_id':ident})
            for _ in range(80):
                terminal = await call('get_observation',{'job_id':ident})
                if terminal['job']['status'] not in {'planned','running'}:break
                await asyncio.sleep(.1)
            assert terminal['job']['status'] in {'passed','cancelled'}  # capture may win the cancellation race
            final = await call('observe',{})
            assert final['observation']['capture']['sim_step']==0
            closed = await call('finish',{'outcome':'aborted','reason':'Read-only four-camera observation interface probe; no task attempt.'})
            assert closed['closed']
            result = {'status':'passed','level':'real_mcp_scripted_observation_probe','at':now(),
                'task_success_claim':False,'four_rgb_images_transported_and_hashed':True,
                'start_status':'planned','start_latency_seconds':trace[0]['duration_seconds'],
                'no_physics_steps':True,'stale_image_rejected':True,'cancel_race_terminal':terminal['job']['status'],
                'tool_calls':len(trace),'interpreter':sys.executable}
            result.update(host=socket.gethostname(),pid=os.getpid(),gpu_uuid=None,
                          bridge=args.bridge,output_path=str(output.resolve()),
                          source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            (output/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bridge',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    try:asyncio.run(probe(a))
    except Exception as e:
        if a.output.exists():(a.output/'validation.json').write_text(json.dumps({'status':'failed','error':f'{type(e).__name__}: {e}'},indent=2)+'\n')
        raise
