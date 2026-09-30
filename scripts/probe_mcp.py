"""Real stdio MCP + HTTP + single-owner bridge contract probe against the CPU fixture."""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 29439
    url = f"http://127.0.0.1:{port}"
    log = (output / "bridge.log").open("w")
    process = subprocess.Popen([sys.executable, "-m", "manipulation_agent.cli", "--backend", "mock", "--policy", "serve",
                                "--port", str(port), "--output", str(output / "episode")], stdout=log, stderr=subprocess.STDOUT)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(600):
            try:
                with opener.open(url + "/healthz", timeout=1) as response:
                    assert json.load(response)["ready"]
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("Bridge startup failed; see bridge.log")
                await asyncio.sleep(0.2)
        else:
            raise TimeoutError("Bridge startup timed out")
        server = StdioServerParameters(command=sys.executable, args=["-m", "manipulation_agent.mcp_server", "--bridge", url],
                                       env={"PYTHONPATH": os.environ["PYTHONPATH"]})
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                specs = await session.list_tools()
                assert {s.name for s in specs.tools} == {"observe", "act", "update_plan", "remember", "recall", "finish"}
                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError, result
                    return json.loads(next(c.text for c in result.content if c.type == "text"))
                first = await call("observe", {})
                assert first["observation"]["sim_steps"] == 0
                nav = await call("act", {"skill": "navigate_to", "target": "radio.n.01_1", "revision": 0})
                assert nav["ok"]
                bad = await call("act", {"skill": "toggle_on", "target": "radio.n.01_1", "revision": 0})
                assert bad["error"]["code"] == "stale_observation"
                good = await call("act", {"skill": "toggle_on", "target": "radio.n.01_1", "revision": 1})
                assert good["ok"]
                end = await call("finish", {"outcome": "achieved", "reason": "CPU transport probe"})
                assert end["closed"]
        process.wait(timeout=20)
        record = json.loads((output / "episode" / "run.json").read_text())
        assert record["task_success"] and record["config"]["backend"] == "mock"
        result = {"status": "passed", "level": "real_mcp_transport_mock_backend", "tool_count": len(specs.tools),
                  "simulator_task_success": None, "bridge_pid": process.pid, "interpreter": sys.executable}
        (output / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        log.close()


asyncio.run(main())
