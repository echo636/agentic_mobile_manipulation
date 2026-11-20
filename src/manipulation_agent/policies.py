from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .contracts import tool_specs


SYSTEM_PROMPT = """You control a mobile manipulation robot through structured tools.
First observe, then create a revisable plan. Use only object IDs from observations.
Navigate near a target before manipulation. Open containers BEFORE grasping items;
open/close/toggle require an empty default hand. Grasp exactly one object, then place
it. Use fresh revisions. A failed skill can change the world: inspect its returned
observation, update your plan and choose a bounded recovery. Remember discoveries
and failed approaches. Done subgoal claims require successful action evidence IDs.
Memory and plans are your beliefs, not ground truth. Read state feedback to verify
effects. Do not repeatedly retry an unchanged failed action. You have no shell,
Python execution, arbitrary file reader, reset, or evaluator access. Finish only
when observations support the whole instruction, or honestly report blocked.
You MUST call the manipulation tool named finish(outcome, reason), receive
closed=true, and only then write a final response. A text answer is not a finish.
This experiment explicitly uses oracle task-object state and ideal symbolic
execution. It does not measure visual perception or physical control capability.
"""


def scripted_episode(harness, task: str) -> None:
    """A task-specific integration probe, NOT an LLM planning baseline."""
    counter = 0
    def call(name, **arguments):
        nonlocal counter
        counter += 1
        return harness.call(name, arguments, f"script-{counter}")
    obs = call("observe")["observation"]
    ids = [o["id"] for o in obs["objects"]]
    if task == "turning_on_radio":
        targets = [o["id"] for o in obs["objects"] if "radio" in o["category"] or "radio" in o["id"]]
        if not targets:
            raise ValueError("No radio in task scope")
        actions = [("navigate_to", targets[0]), ("toggle_on", targets[0])]
    elif task in {"picking_up_trash", "putting_dirty_dishes_in_sink"}:
        destination_words = ("trash", "wastebasket", "ashcan") if task == "picking_up_trash" else ("sink",)
        item_words = ("can", "pop.n", "soda") if task == "picking_up_trash" else ("bowl", "plate", "dish")
        destinations = [o["id"] for o in obs["objects"] if any(w in o["id"] or w in o["category"] for w in destination_words)]
        items = [o["id"] for o in obs["objects"] if o["id"] not in destinations and any(w in o["id"] or w in o["category"] for w in item_words)]
        if len(destinations) != 1 or not items:
            raise ValueError(f"Ambiguous integration fixture objects: {ids}")
        destination = destinations[0]
        actions = []
        container = next(o for o in obs["objects"] if o["id"] == destination)
        if container.get("states", {}).get("open") is False:
            actions.extend([("navigate_to", destination), ("open", destination)])
        for item in sorted(items):
            actions.extend([("navigate_to", item), ("grasp", item), ("navigate_to", destination), ("place_inside", destination)])
    else:
        raise ValueError(f"No scripted probe for {task}; use a model policy")
    plan = [{"id": f"g{i}", "description": f"{skill} {target}", "dependencies": [f"g{i-1}"] if i else [],
             "status": "pending", "evidence": None} for i, (skill, target) in enumerate(actions)]
    call("update_plan", reason="Deterministic integration recipe", subgoals=plan)
    for i, (skill, target) in enumerate(actions):
        result = call("act", skill=skill, target=target, revision=obs["revision"])
        obs = result.get("observation", obs)
        if not result["ok"]:
            call("remember", key="last_failure", text=json.dumps(result["error"]), revision=obs["revision"])
            call("finish", outcome="blocked", reason=f"Integration probe stopped at {skill}: {result['error']['code']}")
            return
        plan[i].update(status="done", evidence=result["evidence_id"])
        call("update_plan", reason="Action returned with postcondition evidence", subgoals=plan)
    call("finish", outcome="achieved", reason="All scripted probe actions completed; independent evaluator decides success")


class ResponsesPolicy:
    """Minimal Responses-compatible tool loop. Credentials are never written to records."""

    def __init__(self, *, model: str, base_url: str, key: str, max_turns=100, max_tokens=150000, timeout=180):
        from urllib.parse import urlsplit
        url = urlsplit(base_url)
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Model endpoint must use HTTPS unless it is loopback")
        if url.username or url.password or url.query:
            raise ValueError("Do not put credentials in endpoint URLs")
        self.model, self.url, self.key = model, base_url.rstrip("/") + "/responses", key
        self.max_turns, self.max_tokens, self.timeout = max_turns, max_tokens, timeout

    @classmethod
    def from_env(cls):
        missing = [k for k in ("LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY") if not os.environ.get(k)]
        if missing:
            raise ValueError("Missing model configuration: " + ", ".join(missing))
        return cls(model=os.environ["LLM_MODEL"], base_url=os.environ["LLM_BASE_URL"], key=os.environ["LLM_API_KEY"])

    def _request(self, body, *, deadline=None):
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        # No automatic mutation retry. Only model requests may be retried on transient status.
        for attempt in range(3):
            if deadline is not None:
                deadline.check()
            timeout = self.timeout if deadline is None else deadline.remaining(self.timeout)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    value = json.load(response)
                    if deadline is not None:
                        deadline.check()  # A slow response may finish after its absolute deadline.
                    return value
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise RuntimeError(f"Model HTTP {exc.code}; response body omitted to protect credentials") from None
                delay = 2 ** attempt
                if deadline is not None:
                    delay = deadline.remaining(delay)
                time.sleep(delay)
            except (TimeoutError, urllib.error.URLError):
                if deadline is not None:
                    deadline.check()  # Map only an expired episode; preserve independent network errors.
                raise
        raise RuntimeError("Unreachable model retry state")

    def run(self, harness, instruction: str):
        tools = [{"type": "function", "name": t["name"], "description": t["description"],
                  "parameters": t["inputSchema"], "strict": True} for t in tool_specs()]
        history = [{"role": "user", "content": instruction}]
        tokens = 0
        for turn in range(self.max_turns):
            if harness.closed:
                return
            if tokens >= self.max_tokens or time.monotonic() - harness.started >= harness.budget.wall_seconds:
                break
            body = {"model": self.model, "instructions": SYSTEM_PROMPT, "input": history, "tools": tools,
                    "parallel_tool_calls": False, "store": False, "max_output_tokens": 4000}
            started = time.monotonic()
            response = self._request(body)
            usage = response.get("usage") or {}
            tokens += usage.get("total_tokens", 0)
            output = response.get("output", [])
            # Save public assistant text and function calls only, never hidden reasoning items.
            public = [o for o in output if o.get("type") in {"message", "function_call"}]
            harness.recorder.event("model_response", {"turn": turn, "model": self.model, "output": public,
                                                       "usage": usage, "cumulative_tokens": tokens,
                                                       "latency_seconds": time.monotonic() - started})
            history.extend(output)
            calls = [o for o in output if o.get("type") == "function_call"]
            if not calls:
                history.append({"role": "user", "content": "Continue through the tools and formally call finish; text alone does not close the episode."})
                continue
            for call in calls:
                try:
                    arguments = json.loads(call["arguments"])
                except (json.JSONDecodeError, KeyError):
                    result = {"ok": False, "error": {"code": "invalid_json", "message": "Function arguments must be JSON"}}
                else:
                    result = harness.call(call["name"], arguments, call["call_id"])
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)})
                if harness.closed:
                    break
        if not harness.closed:
            harness.call("finish", {"outcome": "aborted", "reason": "Model turn/token/wall budget exhausted"}, "runner-budget-stop")
