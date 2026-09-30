"""Real MCP transport test: image bytes, schema boundary, workflow docs and formal close."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def probe(output):
    output.mkdir(parents=True,exist_ok=False)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    log=(output/'bridge.log').open('w')
    process=subprocess.Popen([sys.executable,'-m','manipulation_agent.vision_cli','--backend','mock','--port',str(port),'--output',str(output/'episode')],stdout=log,stderr=subprocess.STDOUT)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(120):
            try:
                with opener.open(url+'/healthz',timeout=1) as r:json.load(r)
                break
            except Exception:await asyncio.sleep(1)
        else:raise TimeoutError('RGB bridge not ready')
        params=StdioServerParameters(command=sys.executable,args=['-m','manipulation_agent.mcp_server','--bridge',url],env=dict(os.environ))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize();tools=await client.list_tools();assert len(tools.tools)==9
                skill=await client.call_tool('read_skill',{'name':'visual-manipulation','resource':'SKILL.md'})
                assert 'Visual manipulation workflow' in skill.content[0].text
                result=await client.call_tool('observe',{})
                payload=json.loads(result.content[0].text);frame=payload['observation']['images'][0]
                images=[c for c in result.content if c.type=='image'];assert len(images)==1
                binary=base64.b64decode(images[0].data)
                assert hashlib.sha256(binary).hexdigest()==frame['sha256']
                assert binary.startswith(b'\x89PNG')
                args={'primitive':'toggle_on','target':{'image_ref':frame['image_ref'],'point':[0.5,0.5]},'revision':payload['observation']['revision']}
                action=await client.call_tool('act',args);a=json.loads(action.content[0].text);assert a['ok']
                assert len([c for c in action.content if c.type=='image'])==1
                stale=await client.call_tool('act',args);assert not json.loads(stale.content[0].text)['ok']
                finish=await client.call_tool('finish',{'outcome':'achieved','reason':'CPU image transport fixture complete'})
                assert json.loads(finish.content[0].text)['closed']
        (output/'validation.json').write_text(json.dumps({'status':'passed','level':'real_mcp_cpu_rgb_fixture','tools':len(tools.tools),'image_bytes':len(binary),'image_hash_verified':True,'pixel_payload_delivered':True,'task_success_claim':'mock only'},indent=2))
        print((output/'validation.json').read_text())
    finally:
        if process.poll() is None:
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        log.close()

if __name__=='__main__':asyncio.run(probe(Path(sys.argv[1])))
