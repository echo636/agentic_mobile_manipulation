"""Read-only MCP initialization/tool discovery before starting a model budget."""
import hashlib
import json
import os
import selectors
import subprocess
import time
from .records import now,write_json


def check_server(command,args,expected,output,timeout=90):
    start=time.monotonic();record={'status':'running','started_at':now(),'read_only':True,'tool_calls':0}
    process=None;selector=None
    try:
        with (output/'mcp_preflight.stderr.log').open('wb') as stderr:
            process=subprocess.Popen([command,*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,bufsize=0)
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ);buffer=b''
            def send(value):process.stdin.write((json.dumps(value)+'\n').encode())
            def receive(identifier):
                nonlocal buffer
                while time.monotonic()-start<timeout:
                    while b'\n' in buffer:
                        line,buffer=buffer.split(b'\n',1)
                        try:message=json.loads(line)
                        except ValueError:continue
                        if message.get('id')==identifier:
                            if message.get('error'):raise RuntimeError('MCP rejected '+str(identifier)+': '+str(message['error']))
                            return message['result']
                    if process.poll() is not None:raise RuntimeError('MCP process exited during handshake')
                    if selector.select(min(1,max(.01,timeout-(time.monotonic()-start)))):
                        block=os.read(process.stdout.fileno(),65536)
                        if not block:raise RuntimeError('MCP stdout closed during handshake')
                        buffer+=block
                        if len(buffer)>4*1024**2:raise RuntimeError('MCP handshake response exceeds bounded size')
                raise TimeoutError('MCP initialization/tool discovery timed out')
            send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'mas-readonly-preflight','version':'1'}}})
            initialized=receive(1);send({'jsonrpc':'2.0','method':'notifications/initialized'})
            send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}});tools=receive(2)['tools']
            wanted={t['name']:t['inputSchema'] for t in expected};found={t['name']:t['inputSchema'] for t in tools}
            if wanted!=found:raise RuntimeError('MCP tool names or schemas differ from frozen controller catalog')
            record.update(status='passed',protocol_version=initialized.get('protocolVersion'),tool_names=sorted(found),
                          schema_sha256=hashlib.sha256(json.dumps(found,sort_keys=True).encode()).hexdigest())
            selector.close()
    except Exception as exc:
        record.update(status='failed',error_type=type(exc).__name__,error=str(exc))
        raise
    finally:
        if process is not None:
            try:process.stdin.close()
            except (OSError,AttributeError):pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        if selector is not None:selector.close()
        if process is not None and process.stdout is not None:process.stdout.close()
        record.update(finished_at=now(),seconds=time.monotonic()-start)
        write_json(output/'mcp_preflight.json',record)
    return record
