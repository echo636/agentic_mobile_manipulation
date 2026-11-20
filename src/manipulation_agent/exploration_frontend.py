"""One owner MCP + Unix-socket frontend for a writable exploration workspace."""
import argparse
import asyncio
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socketserver
import threading
import os
import tempfile
from urllib.parse import quote
from .exploration_session import ExplorationSession
from .exploration_factory import BridgeFactory
from .oracle_presentation import specs as geometry_specs
from .mcp_server import fetch
from .exploration_budget import deliver_rgb

def tool_specs(robot_tools):
    robot_tools = [deepcopy(t) for t in robot_tools if t["name"] != "finish"]
    from .manip_runtime import tool_specs as manip_specs
    present = {t['name'] for t in robot_tools}
    if 'write_manip_program' not in present:
        robot_tools.extend(t for t in manip_specs() if t['name'] not in present)
    lifecycle = []
    for name, properties, required, description in (
        ("read_workspace_file", {"path":{"type":"string"}, 'offset': {'type': 'integer', 'minimum': 0},
                                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 24000}}, ["path"],
         "Read a file slice (default 12000 characters), hash and next_offset. Read only needed portions; unchanged files need no reread."),
        ('patch_workspace_file', {'path': {'type': 'string'}, 'expected_sha256': {'type': 'string'},
                                 'old_text': {'type': 'string', 'maxLength': 24000},
                                 'new_text': {'type': 'string', 'maxLength': 24000}},
         ['path', 'expected_sha256', 'old_text', 'new_text'],
         'Replace one exact text occurrence in a writable file at its read hash. Avoid rewriting complete controller files.'),
        ('take_notes', {'content': {'type': 'string', 'maxLength': 6000},
                        'expected_version': {'type': 'integer', 'minimum': 0}}, [],
         'Read with {}. Replace with content + expected_version. Persist a <=6000-character continuation summary: task constraints, actual live state, evidence, failed hypotheses, code paths and next step. Writing acknowledges without echoing notes; it does not itself compact chat history.'),
        ('checkpoint_subtask', {
            'subtask_id': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'},
            'summary': {'type': 'string', 'minLength': 1, 'maxLength': 1200},
            'postconditions': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 500}, 'minItems': 1, 'maxItems': 12},
            'evidence_refs': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 500}, 'minItems': 1, 'maxItems': 20},
            'next_subtask': {'type': 'string', 'minLength': 1, 'maxLength': 600},
            'expected_notes_version': {'type': 'integer', 'minimum': 1},
            'revision': {'type': 'integer', 'minimum': 0}},
         ['subtask_id', 'summary', 'postconditions', 'evidence_refs', 'next_subtask', 'expected_notes_version', 'revision'],
         'After an observable subtask succeeds, update take_notes at the current revision, then record its postconditions and evidence. This requests a fresh model conversation on the same live episode. After success, return your final response immediately and call no more tools.'),
        ('get_cached_rgb', {'views': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 6},
                            'revision': {'type': 'integer'}}, ['views', 'revision'],
         'Read selected RGB views from the CURRENT cached observation, e.g. left_wrist/right_wrist. No render, robot action or physics advance. All six cameras remain available; use current images metadata for view names.'),
        ("write_workspace_file", {"path":{"type":"string"}, "content":{"type":"string","maxLength":100000}},
         ["path","content"], "Write your own Python controller or memory under code/, memory/, experiments/. No host/repository/credential access."),
        ("run_development", {"project":{"type":"string"}}, ["project"],
         "Run your controller.py:main(ctx) on the CURRENT exploration scene in an offline no-GPU sandbox. Blocks until result; no VLM polling. Failure preserves scene. This is debugging, not independent rehearsal."),
        ("status", {}, [], "Current exploration state, remaining episodes and exact bundle qualification."),
        ("start_episode", {}, [], "Start the next operator-scheduled fresh exploration episode. Do not reset on resume."),
        ("finish", {"outcome": {"type":"string","enum":["achieved","blocked","aborted"]},
                    "reason":{"type":"string"}}, ["outcome","reason"], "Close only the active exploration episode and collect its independent evaluation."),
        ("register", {"project":{"type":"string"}}, ["project"], "Freeze workspace code/<project>/controller.py and its helpers, returning a content-addressed bundle."),
        ("rehearse", {"bundle":{"type":"string"}}, ["bundle"], "Run the exact frozen bundle unattended in a fresh exploration episode. Blocks until result; no model polling."),
        ("submit", {"bundle":{"type":"string"}}, ["bundle"], "Close exploration on an exact cleanly rehearsed bundle. Host formal evaluation begins after the agent exits.")):
        lifecycle.append({"name":name,"description":description,"inputSchema":{
            "type":"object","properties":properties,"required":required,"additionalProperties":False}})
    return [*robot_tools, *lifecycle, *geometry_specs()]

async def serve(root):
    from mcp import types
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.tools import Tool
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
    from pydantic import ConfigDict
    root = Path(root).resolve()
    config = json.loads((root/"config.json").read_text())
    session = ExplorationSession(root, BridgeFactory(config["data_root"]))
    # Commands launched by the coding agent call this same owner through AF_UNIX.
    # No additional simulator or robot controller process is created.
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                raw=self.rfile.readline(65537)
                if len(raw)>65536:raise ValueError("Oversized request")
                d=json.loads(raw)
                # Development scripts get robot tools only; lifecycle stays MCP.
                allowed={t["name"] for t in config["robot_tools"]}-{"finish"}
                if d["name"] not in allowed:raise ValueError("Development Context permits robot tools only")
                with session.lock:
                    if session.state["phase"]!="exploring":raise ValueError("Exploration is closed")
                    result=session.call_robot(d["name"],d["arguments"],d["request_id"])
            except Exception as exc:
                result={"ok":False,"error":{"type":type(exc).__name__,"message":str(exc)}}
            self.wfile.write(json.dumps(result,allow_nan=False).encode()+b"\n")
    socket_root=Path(os.environ.get("MAS_EXPLORATION_SOCKET_ROOT",str(Path(tempfile.gettempdir()) / ("mas-exploration-" + str(os.getuid())))))
    socket_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    socket_path=socket_root/(hashlib.sha256(str(root).encode()).hexdigest()[:20]+".sock")
    if socket_path.exists():socket_path.unlink()
    socket_server=socketserver.ThreadingUnixStreamServer(str(socket_path),Handler)
    thread=threading.Thread(target=socket_server.serve_forever,daemon=True);thread.start()
    class Arguments(ArgModelBase):
        model_config=ConfigDict(extra="allow")
        def model_dump_one_level(self):return dict(self.model_extra or {})
    lock=asyncio.Lock()
    def wrap(name):
        async def invoke(**arguments):
            async with lock:
                try:
                    result=await asyncio.to_thread(session.dispatch,name,arguments)
                except Exception as exc:
                    result={"ok":False,"error":{"type":type(exc).__name__,"message":str(exc)}}
                content=[types.TextContent(type="text",text=json.dumps(result,ensure_ascii=False))]
                observation=result.get("observation",{})
                if session.episode and deliver_rgb(name, config):
                    for frame in observation.get("images",[]):
                        data=await asyncio.to_thread(fetch,session.episode.url+"/image/"+quote(frame["image_ref"],safe=""))
                        if hashlib.sha256(data).hexdigest()!=frame["sha256"]:raise RuntimeError("Image hash changed")
                        content.append(types.TextContent(type="text",text="RGB view="+frame["view"]))
                        content.append(types.ImageContent(type="image",data=base64.b64encode(data).decode(),mimeType=frame["mime_type"]))
                return content
        return invoke
    registered=[Tool(fn=wrap(t["name"]),name=t["name"],description=t.get("description",""),
                     parameters=t["inputSchema"],is_async=True,
                     fn_metadata=FuncMetadata(arg_model=Arguments))
                for t in tool_specs(config["robot_tools"])]
    try:
        await FastMCP("oracle-exploration",tools=registered,log_level="WARNING").run_stdio_async()
    finally:
        socket_server.shutdown();socket_server.server_close();thread.join(timeout=2)
        socket_path.unlink(missing_ok=True);session.close()

def main():
    p=argparse.ArgumentParser();p.add_argument("--session",required=True)
    args=p.parse_args();asyncio.run(serve(args.session))
if __name__=="__main__":main()
