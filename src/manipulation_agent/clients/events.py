"""Normalize public client streams without rewriting the raw stdout evidence.

Adapters preserve call IDs, MCP content blocks (including images), and source lines.
Unknown records remain in model_events.jsonl. Missing clocks are not manufactured.
"""
from __future__ import annotations

import json
from pathlib import Path
from .types import ParsedEvents


def event_path(directory: Path) -> Path:
    compat = directory / "model_events.compat.jsonl"
    return compat if compat.exists() else directory / "model_events.jsonl"


def _object(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _tool(name, server=None):
    name = str(name or "")
    for prefix in ("mcp__manipulation__", "mcp_manipulation_", "manipulation_", "manipulation."):
        if name.startswith(prefix):
            return "manipulation", name[len(prefix):]
    return server or "unknown", name


def _result(value):
    value = _object(value)
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        return value
    if isinstance(value, list):
        return {"content": value}
    if isinstance(value, dict) and "output" in value:
        return _result(value["output"])
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value)}]}


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _normalize_usage(usage: dict | None, client: str) -> dict | None:
    if not isinstance(usage, dict):
        return None
    normalized = dict(usage)
    if client == "claude":
        raw_in = int(usage.get("input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        total_in = raw_in + cache_create + cache_read
        normalized["unbinned_input_tokens"] = raw_in
        normalized["input_tokens"] = total_in
        normalized["cached_input_tokens"] = cache_read
        normalized["total_tokens"] = total_in + out
    elif client == "agy":
        raw_in = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        total_in = raw_in + cache_read
        normalized["uncached_input_tokens"] = raw_in
        normalized["input_tokens"] = total_in
        normalized["cached_input_tokens"] = cache_read
        normalized["total_tokens"] = total_in + out
    return normalized


def _closed_receipt_in_blocks(blocks):
    for block in blocks or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            value = _object(block.get('text'))
            if isinstance(value, dict) and value.get('ok') is True and value.get('closed') is True:
                return True
    return False


def successful_finish_event(event):
    """Only a completed successful receipt closes the policy, never its request."""
    if not isinstance(event, dict):
        return False
    # Codex / compat format
    if event.get('type') == 'item.completed':
        item = event.get('item') or {}
        if not isinstance(item, dict) or item.get('type') != 'mcp_tool_call':
            return False
        if (item.get('server'), item.get('tool')) != ('manipulation', 'finish') or item.get('error'):
            return False
        result = item.get('result') or {}
        if not isinstance(result, dict) or result.get('isError'):
            return False
        return _closed_receipt_in_blocks(result.get('content'))
    # Claude raw stream-json user tool_result (standalone stateless check when full context has single finish)
    if event.get('type') == 'user':
        msg = event.get('message') or {}
        for block in msg.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'tool_result' and not block.get('is_error'):
                content = block.get('content')
                blocks = content if isinstance(content, list) else [{'type': 'text', 'text': str(content or '')}]
                if _closed_receipt_in_blocks(blocks):
                    return True
    # Agy raw stream-json format (carries ServerName + ToolName + output in the DONE step_update)
    if event.get('event') == 'step_update':
        su = event.get('step_update') or {}
        if su.get('step_type') != 'tool' or su.get('state') != 'DONE':
            return False
        info = su.get('tool_info') or {}
        if info.get('error'):
            return False
        params = info.get('parameters') or {}
        tool_name = info.get('name') or su.get('tool_name') or ''
        if tool_name == 'call_mcp_tool':
            server, tool = params.get('ServerName'), params.get('ToolName')
        else:
            server, tool = _tool(tool_name)
        if (server, tool) != ('manipulation', 'finish'):
            return False
        out = _object(info.get('output'))
        return isinstance(out, dict) and out.get('ok') is True and out.get('closed') is True
    return False


def completed_robot_action_event(event) -> bool:
    """True when one raw stream line marks completion of a step-charging act/look tool call."""
    if not isinstance(event, dict):
        return False
    if event.get('type') == 'item.completed':
        item = event.get('item') or {}
        if not isinstance(item, dict) or item.get('type') != 'mcp_tool_call':
            return False
        if item.get('tool') not in {'act', 'look'}:
            return False
        blocks = ((item.get('result') or {}).get('content') or []) if isinstance(item.get('result'), dict) else []
        for b in blocks:
            if isinstance(b, dict) and b.get('type') == 'text':
                val = _object(b.get('text'))
                if isinstance(val, dict) and isinstance(val.get('error'), dict):
                    if val['error'].get('action_step_used') is False:
                        return False
        return True
    if event.get('event') == 'step_update':
        su = event.get('step_update') or {}
        if su.get('step_type') != 'tool' or su.get('state') != 'DONE':
            return False
        info = su.get('tool_info') or {}
        params = info.get('parameters') or {}
        tool_name = info.get('name') or su.get('tool_name') or ''
        if tool_name == 'call_mcp_tool':
            server, tool = params.get('ServerName'), params.get('ToolName')
        else:
            server, tool = _tool(tool_name)
        if server != 'manipulation' or tool not in {'act', 'look'}:
            return False
        out = _object(info.get('output'))
        if isinstance(out, dict) and isinstance(out.get('error'), dict) and out['error'].get('action_step_used') is False:
            return False
        return True
    return False


def parse_events(path: Path, client: str) -> ParsedEvents:
    rows = []; invalid = []; canonical = []; compat = []
    calls = {}; emitted = set(); completed = set(); turn = 0
    codex_stream = 0; codex_stream_started = False
    claude_pending_turn_usages: list[dict] = []
    claude_seen_msg_ids: set[str] = set()
    agy_text_buffers: dict[tuple[str, int], list[str]] = {}
    agy_pending_step_usages: list[dict] = []

    def emit(kind, line, at, **values):
        event = {"at": at, "kind": kind, "client": client, "turn": turn or None,
                 "source": "model_events.jsonl", "source_line": line, **values}
        canonical.append(event)
        return event

    def message(content, line, at):
        text = _text(content)
        if text:
            emit("assistant_text", line, at, content=text)
            if client != "codex":
                compat.append({"type": "item.completed", "at": at, "source": client + "_adapter",
                    "source_line": line, "item": {"type": "agent_message", "id": f"message-{line}", "text": text}})

    def tool_call(call_id, name, arguments, line, at, server=None):
        server, name = _tool(name, server)
        call_id = str(call_id or f"call-{line}")
        item = {"id": call_id, "type": "mcp_tool_call", "server": server, "tool": name,
                "arguments": _object(arguments) if arguments is not None else {}}
        calls[call_id] = item
        if call_id not in emitted:
            emit("tool_call", line, at, call_id=call_id, server=server, tool=name, arguments=item["arguments"])
            if client != "codex":
                compat.append({"type": "item.started", "at": at, "source": client + "_adapter",
                               "source_line": line, "item": item.copy()})
            emitted.add(call_id)
        return call_id

    def tool_result(call_id, value, line, at, error=None):
        call_id = str(call_id or f"call-{line}")
        if call_id in completed:
            return
        item = calls.get(call_id, {"id": call_id, "type": "mcp_tool_call", "server": "unknown",
                                   "tool": "unknown", "arguments": {}})
        result = _result(value) if value is not None else None
        emit("tool_result", line, at, call_id=call_id, server=item["server"], tool=item["tool"],
             result=result, error=error)
        if client != "codex":
            compat.append({"type": "item.completed", "at": at, "source": client + "_adapter",
                           "source_line": line, "item": {**item, "result": result, "error": error}})
        completed.add(call_id)

    if not path.exists():
        return ParsedEvents([], [], [], [], [], False)
    for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if not line.lstrip().startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            invalid.append(line_no); continue
        if not isinstance(data, dict):
            continue
        rows.append(data)
        at = data.get("at", data.get("timestamp", data.get("time")))
        typ = data.get("type")
        if client == "codex":
            compat.append(data)
            if typ == "thread.started":
                # Each CLI invocation, including resume, can reuse item IDs.
                if codex_stream_started or calls:
                    codex_stream += 1
                codex_stream_started = True
            if typ == "turn.started":
                turn += 1
            item = data.get("item") or {}
            if typ == "item.completed" and item.get("type") == "agent_message":
                message(item.get("text"), line_no, at)
            if item.get("type") == "mcp_tool_call" and typ in {"item.started", "item.completed"}:
                call_id = item.get("id")
                if codex_stream and call_id is not None:
                    call_id = f"codex-stream-{codex_stream}:{call_id}"
                call_id = tool_call(call_id, item.get("tool"), item.get("arguments"), line_no, at, item.get("server"))
                if typ == "item.completed":
                    tool_result(call_id, item.get("result"), line_no, at, item.get("error"))
            if typ == "thread.started":
                emit("session_started", line_no, at, content={"thread_id": data.get("thread_id")})
        elif client == "opencode":
            part = data.get("part") or {}; state = part.get("state") or {}
            if typ == "step_start":
                turn += 1
            if typ == "text" or part.get("type") == "text":
                message(part.get("text", data.get("text")), line_no, at)
            if typ == "tool_use" or part.get("type") == "tool":
                call_id = tool_call(part.get("callID", part.get("id")), part.get("tool", data.get("name")),
                                    state.get("input", data.get("input")), line_no, at)
                if state.get("status") in {"completed", "error"} or "output" in state:
                    value = state.get("output")
                    if state.get("attachments"):
                        # Preserve attachments as recorded; do not invent delivered image bytes.
                        value = {**_result(value), "attachments": state["attachments"]}
                    tool_result(call_id, value, line_no, at, state.get("error"))
        elif client == "kimi":
            if typ == "context.append_loop_event":
                data = data.get("event") or {}; typ = data.get("type"); at = at or data.get("time")
            elif isinstance(data.get("payload"), dict):
                data = data["payload"]; typ = data.get("type", typ)
            role = data.get("role")
            if role == "assistant" or typ in {"assistant_message", "assistant", "message"}:
                turn += 1
                message(data.get("content", data.get("text")), line_no, at)
                for call in data.get("tool_calls") or []:
                    function = call.get("function") or call
                    tool_call(call.get("id"), function.get("name"), function.get("arguments", function.get("args")), line_no, at)
            if typ in {"tool.call", "tool_call", "function_call"}:
                tool_call(data.get("toolCallId", data.get("id", data.get("call_id"))), data.get("name", data.get("tool")),
                          data.get("args", data.get("arguments", data.get("input"))), line_no, at)
            if role == "tool" or typ in {"tool.result", "tool_result", "tool_call_result", "function_call_output"}:
                call_id = data.get("tool_call_id", data.get("toolCallId", data.get("call_id", data.get("id"))))
                tool_result(call_id, data.get("result", data.get("content", data.get("output"))), line_no, at, data.get("error"))
            if typ == "content.part":
                part = data.get("part") or {}
                if part.get("type") == "text":
                    message(part.get("text"), line_no, at)
        elif client == "claude":
            if typ == "system" and data.get("subtype") == "init":
                if claude_pending_turn_usages:
                    totals = {"input_tokens": 0, "cached_input_tokens": 0, "unbinned_input_tokens": 0,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                              "output_tokens": 0, "total_tokens": 0}
                    for nu in claude_pending_turn_usages:
                        for k in totals:
                            totals[k] += int(nu.get(k) or 0)
                    claude_pending_turn_usages.clear()
                    emit("usage", line_no, at, usage=totals)
                    compat.append({"type": "turn.completed", "at": at, "source": "claude_adapter_partial", "usage": totals})
                sid = data.get("session_id")
                emit("session_started", line_no, at, content={"session_id": sid})
            elif typ == "assistant":
                msg = data.get("message") or {}
                mid = str(msg.get("id") or data.get("uuid") or line_no)
                if mid not in claude_seen_msg_ids and isinstance(msg.get("usage"), dict):
                    claude_seen_msg_ids.add(mid)
                    nu = _normalize_usage(msg["usage"], "claude")
                    if nu:
                        claude_pending_turn_usages.append(nu)
                blocks = msg.get("content") or []
                text_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
                if text_blocks:
                    message(text_blocks, line_no, at)
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        turn += 1
                        tool_call(b.get("id"), b.get("name"), b.get("input"), line_no, at)
            elif typ == "user":
                msg = data.get("message") or {}
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        err = {"message": _text(b.get("content")) or "tool_error"} if b.get("is_error") else None
                        tool_result(b.get("tool_use_id"), b.get("content"), line_no, at, err)
            elif typ == "result":
                u = _normalize_usage(data.get("usage"), "claude")
                if u:
                    claude_pending_turn_usages.clear()
                    emit("usage", line_no, at, usage=u)
                    compat.append({"type": "turn.completed", "at": at, "source": "claude_adapter", "usage": u})
                if data.get("is_error"):
                    err_msg = data.get("result") or data.get("error") or "claude_result_error"
                    emit("client_error", line_no, at, error=err_msg)
                    compat.append({"type": "turn.failed", "at": at, "source": "claude_adapter", "message": str(err_msg)})
                emit("run_finished", line_no, at)
            continue
        elif client == "agy":
            ev = data.get("event")
            if ev == "init":
                if agy_pending_step_usages:
                    totals = {"input_tokens": 0, "cached_input_tokens": 0, "uncached_input_tokens": 0,
                              "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
                    for nu in agy_pending_step_usages:
                        for k in totals:
                            totals[k] += int(nu.get(k) or 0)
                    agy_pending_step_usages.clear()
                    emit("usage", line_no, at, usage=totals)
                    compat.append({"type": "turn.completed", "at": at, "source": "agy_adapter_partial", "usage": totals})
                cid = data.get("conversation_id")
                emit("session_started", line_no, at, content={"conversation_id": cid})
            elif ev == "step_update":
                su = data.get("step_update") or {}
                cid = str(su.get("conversation_id") or "")
                sidx = su.get("step_index")
                stype = su.get("step_type")
                state = su.get("state")
                if stype == "agent_response":
                    key = (cid, int(sidx) if isinstance(sidx, int) else line_no)
                    delta = su.get("text_delta")
                    if isinstance(delta, str) and delta:
                        agy_text_buffers.setdefault(key, []).append(delta)
                    if state == "DONE":
                        turn += 1
                        if isinstance(su.get("usage"), dict):
                            nu = _normalize_usage(su["usage"], "agy")
                            if nu:
                                agy_pending_step_usages.append(nu)
                        buf = "".join(agy_text_buffers.pop(key, []))
                        if buf.strip():
                            message(buf, line_no, at)
                elif stype == "tool":
                    info = su.get("tool_info") or {}
                    params = info.get("parameters") or {}
                    raw_name = info.get("name") or su.get("tool_name") or ""
                    if raw_name == "call_mcp_tool":
                        srv = params.get("ServerName") or "unknown"
                        tname = params.get("ToolName") or "unknown"
                        args = params.get("Arguments") if "Arguments" in params else {}
                    else:
                        srv, tname = _tool(raw_name)
                        args = params
                    call_id = f"agy-{cid[:8] if cid else 'run'}-step-{sidx}" if sidx is not None else f"call-{line_no}"
                    if state in {"ACTIVE", "DONE", "ERROR"}:
                        tool_call(call_id, tname, args, line_no, at, srv)
                    if state in {"DONE", "ERROR"}:
                        err = info.get("error") if state == "ERROR" or info.get("error") else None
                        out_val = info.get("output") if "output" in info else (json.dumps(err) if err else "")
                        tool_result(call_id, out_val, line_no, at, err)
            elif ev == "result":
                res = data.get("result") or {}
                u = _normalize_usage(res.get("usage"), "agy")
                if u:
                    agy_pending_step_usages.clear()
                    emit("usage", line_no, at, usage=u)
                    compat.append({"type": "turn.completed", "at": at, "source": "agy_adapter", "usage": u})
                status = str(res.get("status") or "").upper()
                if status and status != "SUCCESS":
                    err_msg = res.get("error") or res.get("response") or f"agy_status_{status}"
                    emit("client_error", line_no, at, error=err_msg)
                    compat.append({"type": "turn.failed", "at": at, "source": "agy_adapter", "message": str(err_msg)})
                emit("run_finished", line_no, at)
            elif ev == "error":
                err_msg = data.get("error") or data.get("message") or "agy_stream_error"
                emit("client_error", line_no, at, error=err_msg)
                compat.append({"type": "turn.failed", "at": at, "source": "agy_adapter", "message": str(err_msg)})
            continue
        # Only an explicitly identified summary is exported as a summary. Reasoning
        # token counters, encrypted content and think/reasoning blocks are not summaries.
        summary = data.get("reasoning_summary")
        if isinstance(summary, str):
            emit("reasoning_summary", line_no, at, reasoning_summary=summary)
        usage = data.get("usage") or (data.get("part") or {}).get("tokens")
        if isinstance(usage, dict):
            emit("usage", line_no, at, usage=usage)
            if client != "codex":
                compat.append({"type": "turn.completed", "at": at, "source": client + "_adapter", "usage": usage})
        if typ in {"error", "turn.failed"}:
            emit("client_error", line_no, at, error=data.get("error", data.get("message")))
        if typ in {"turn.completed", "step_finish", "step.end", "done", "result"}:
            emit("run_finished" if typ in {"done", "result"} else "turn_finished", line_no, at)

    if client == "claude" and claude_pending_turn_usages:
        totals = {"input_tokens": 0, "cached_input_tokens": 0, "unbinned_input_tokens": 0,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "output_tokens": 0, "total_tokens": 0}
        for nu in claude_pending_turn_usages:
            for k in totals:
                totals[k] += int(nu.get(k) or 0)
        emit("usage", len(rows), None, usage=totals)
        compat.append({"type": "turn.completed", "at": None, "source": "claude_adapter_partial", "usage": totals})

    if client == "agy" and agy_pending_step_usages:
        # Final invocation interrupted before emitting terminal result: sum its completed step usages.
        totals = {"input_tokens": 0, "cached_input_tokens": 0, "uncached_input_tokens": 0,
                  "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
        for nu in agy_pending_step_usages:
            for k in totals:
                totals[k] += int(nu.get(k) or 0)
        emit("usage", len(rows), None, usage=totals)
        compat.append({"type": "turn.completed", "at": None, "source": "agy_adapter_partial", "usage": totals})

    tools = []; closed = False
    for event in compat:
        item = event.get("item") or {}
        if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call":
            continue
        tools.append(item.get("tool"))
        closed = closed or successful_finish_event(event)
    return ParsedEvents(rows, canonical, compat, invalid, tools, closed)


def write_derived_events(output: Path, parsed: ParsedEvents, client: str):
    def write(path, events):
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in events))
    write(output / "canonical_events.jsonl", parsed.canonical)
    if client != "codex":
        write(output / "model_events.compat.jsonl", parsed.compat)
