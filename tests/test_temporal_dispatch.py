"""Policy-side tests have no simulator, layout or task evaluator."""

import json
from itertools import permutations

import pytest

from manipulation_agent.agent.temporal_execution import DispatchController
from manipulation_agent.agent.temporal_graph import TemporalGraph, schedule
from manipulation_agent.agent.temporal_memory import Camera, DeltaMemory

ortools = pytest.importorskip("ortools")


def work(key, src="A", dst=None, seconds=10, after=(), load=0):
    return {
        "id": key,
        "work": key,
        "origin": src,
        "destination": dst or src,
        "seconds": seconds,
        "after": list(after),
        "assertion": key + " visible",
        "skill": "transfer",
        "load": load,
    }


def window(
    start="start",
    check="visit",
    earliest=180,
    latest=250,
    resource="appliance",
    margin=0,
):
    return {
        "start": start,
        "check": check,
        "resource": resource,
        "earliest": earliest,
        "latest": latest,
        "margin": margin,
    }


def graph(nodes, windows=(), carriers=(), travel=15, region="A"):
    return {
        "region": region,
        "nodes": nodes,
        "windows": list(windows),
        "carriers": list(carriers),
        "travel": [],
        "unknown_travel": travel,
    }


def kitchen(distance, length):
    return graph(
        [work("start"), work("visit"), work("rest", "B", seconds=length)],
        [window(latest=500)],
        travel=distance,
        region="B",
    )


def brute(graph_data, serial):
    """Independent integer-time enumeration for one passive process and any route order."""
    tasks = {n["id"]: n for n in graph_data["nodes"]}
    best = float("inf")
    w = graph_data["windows"][0]
    for order in permutations(tasks):
        a = order.index("start")
        b = order.index("visit")
        if a >= b or (serial and b != a + 1):
            continue
        t = 0
        start = 0
        pos = graph_data["region"]
        for key in order:
            n = tasks[key]
            t += 0 if pos == n["origin"] else graph_data["unknown_travel"]
            if key == "visit":
                t = max(t, start + w["earliest"])
                if t + n["seconds"] > start + w["latest"] - w["margin"]:
                    break
            t += n["seconds"]
            pos = n["destination"]
            if key == "start":
                start = t
        else:
            best = min(best, t)
    return best


@pytest.mark.parametrize("distance", [1, 7, 15, 55, 95, 120])
@pytest.mark.parametrize("length", [25, 120, 205])
@pytest.mark.parametrize("serial", [False, True])
def test_calendar_against_independent_enumeration(distance, length, serial):
    g = kitchen(distance, length)
    result = schedule(
        TemporalGraph.read(g), {}, now=0, region="B", serial=serial, solver_seconds=5
    )
    assert result.status == "optimal"
    assert result.slots[-1].end == brute(g, serial)


def carrier(cap=2, loc="A", handling=2):
    return {"name": "tray", "location": loc, "capacity": cap, "handling": handling}


@pytest.mark.parametrize(
    ("cap", "loc", "handling", "expected"),
    [(2, "A", 2, True), (1, "A", 2, False), (2, "B", 2, False), (2, "A", 100, False)],
)
def test_batch_needs_capacity_origin_and_savings(cap, loc, handling, expected):
    g = TemporalGraph.read(
        graph(
            [work("a", "A", "B", load=1), work("b", "A", "B", load=1)],
            carriers=[carrier(cap, loc, handling)],
        )
    )
    result = schedule(g, {}, now=0, region="A")
    assert any(s.job.carrier for s in result.slots) == expected
    assert sum(len(s.job.members) for s in result.slots) == 2


def test_no_group_can_discharge_its_own_dependency():
    g = TemporalGraph.read(
        graph(
            [work("a", "A", "B", load=1), work("b", "A", "B", after=["a"], load=1)],
            carriers=[carrier()],
        )
    )
    assert len(schedule(g, {}, now=0, region="A").slots) == 2


def test_completed_start_is_anchored_and_cannot_gain_a_second_by_rounding():
    g = TemporalGraph.read(
        graph(
            [work("start"), work("visit", seconds=5)], [window(earliest=0, latest=10)]
        )
    )
    assert schedule(g, {"start": 1.1}, now=6.9, region="A").status == "infeasible"
    assert schedule(g, {"start": 1.9}, now=6, region="A").slots[0].end == 11


def test_multiple_devices_with_conflicting_visits():
    g = TemporalGraph.read(
        graph(
            [work("s1"), work("s2"), work("v1"), work("v2")],
            [window("s1", "v1", 15, 25, "stove"), window("s2", "v2", 15, 25, "oven")],
        )
    )
    assert schedule(g, {"s1": 10, "s2": 10}, region="A", now=10).status == "infeasible"


def test_atomic_local_edit_protects_completed_work_and_distant_goal():
    g = TemporalGraph.read(
        graph(
            [
                work("used"),
                work("open", after=["used"]),
                work("serve", after=["open"]),
                work("other"),
            ],
            [window("used", "serve", 10, 100)],
        )
    )
    patch = {
        "upsert": [work("look", after=["used"]), work("open", after=["look"])],
        "remove": [],
        "windows": [],
    }
    fixed = g.repair(patch, {"open"}, {"used": 10})
    assert fixed.nodes["open"].after == ("look",) and "look" not in g.nodes
    for invalid in [
        {"upsert": [work("used")]},
        {"remove": ["serve"]},
        {"upsert": [work("other")]},
        {"windows": [window("open", "serve", 10, 100)]},
        {"upsert": [work("orphan")]},
    ]:
        with pytest.raises(ValueError):
            g.repair({**patch, **invalid}, {"open"}, {"used": 10})


def camera(rev, t):
    from test_action_evidence import packet

    observed = Camera.read(packet(rev))
    return Camera(rev, observed.frames, t, "unavailable")


def test_only_changes_take_storage_and_history_has_no_current_pixel_ref():
    m = DeltaMemory(keyframe_limit=2)
    fact = {
        "subject": "board",
        "relation": "on",
        "value": "surface",
        "certainty": "seen",
        "view": "front",
    }
    assert m.observe([fact], camera(0, 0)) and not m.observe([fact], camera(1, 0))
    for i in range(6):
        m.observe([{**fact, "subject": "tool" + str(i)}], camera(i + 2, i))
    assert len(m.keyframes) == 2 and len(m.context("tool", limit=3)["beliefs"]) == 3
    assert len(m.reference("tool", before=7)) == 2
    assert "image_ref" not in str(m.reference("tool"))


class Role:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def ask(self, prompt, schema, context, images):
        self.calls.append(context)
        return next(self.results)


def decision(kind="act", op="open", key="x"):
    return {
        "decision": kind,
        "primitive": op,
        "view": "front",
        "x": 0.4,
        "y": 0.5,
        "seconds": 0,
        "yaw": 0,
        "evidence": "front view changed",
        "changes": [],
        "verified": [{"id": key, "evidence": "front view"}] if kind == "done" else [],
    }


def test_normal_episode_once_compiled_current_pixels_and_no_slow_repair():
    slow = Role(graph([work("x")]))
    fast = Role(decision(), decision("done"))
    c = DispatchController("user request", ["open"], slow, fast)
    c.observe(camera(0, 0))
    tool, args = c.next()
    assert tool == "act" and args["target"]["image_ref"] == "front-0"
    with pytest.raises(RuntimeError):
        c.next()
    with pytest.raises(ValueError):
        c.observe(camera(0, 10))
    c.observe(camera(1, 10))
    assert c.next()[1]["outcome"] == "achieved"
    assert len(slow.calls) == 1 and len(fast.calls) == 2


def test_timer_preempts_slow_work_but_early_check_cannot_stop_process():
    g = kitchen(1, 40)
    g["windows"] = [window(earliest=40, latest=51, margin=1)]
    c = DispatchController(
        "test",
        ["toggle_off", "open", "wait"],
        Role(g),
        Role(decision(op="toggle_off"), decision("done", key="visit")),
    )
    c.observe(camera(0, 0))
    c.compile()
    c.completed["start"] = 0
    from manipulation_agent.agent.temporal_graph import Job

    c.active = Job(("rest",), "B", "B", 40)
    c.region = "B"
    c.observe(camera(1, 39))
    name, args = c.next()
    assert name == "act" and args["primitive"] == "wait" and args["wait_seconds"] == 1
    assert c.active.members == ("visit",)


def test_batch_completion_needs_item_specific_evidence():
    g = graph(
        [work("a", "A", "B", load=1), work("b", "A", "B", load=1)], carriers=[carrier()]
    )
    c = DispatchController(
        "test",
        ["release"],
        Role(g, {"outcome": "blocked", "reason": "one item unverified"}),
        Role(decision("done", key="a")),
    )
    c.observe(camera(0, 0))
    assert c.next()[1]["outcome"] == "blocked" and c.completed == {}


def test_user_requirement_survives_nonterminal_edits_and_explicit_time_is_frozen():
    raw = graph(
        [work("on"), work("v"), work("end", after=["v"])],
        [dict(window("on", "v", 3, 100), earliest_source="instruction")],
    )
    raw["required_outcomes"] = ["v"]
    g = TemporalGraph.read(raw)
    patch = {"remove": [], "upsert": [], "windows": []}
    with pytest.raises(ValueError, match="assertions"):
        g.repair(
            {**patch, "upsert": [dict(work("v"), assertion="assumed")]},
            {"v"},
            {"on": 2},
        )
    with pytest.raises(ValueError, match="time bounds"):
        g.repair({**patch, "windows": [window("on", "v", 1, 100)]}, {"v"}, {"on": 2})
    revised = dict(raw["windows"][0], latest=130)
    assert (
        g.repair({**patch, "windows": [revised]}, {"v"}, {"on": 2}).windows["v"].latest
        == 130
    )


def test_public_camera_is_invariant_to_private_errors_scores_and_attachment_state():
    from test_action_evidence import packet

    a = packet(1, hand="holding")
    b = packet(1, hand="holding", poison=True)
    for v in (a, b):
        raw = json.loads(v.content[0].text)
        raw["robot_time"] = {"total_s": 15.2, "private": "hidden"}
        raw["hand"]["source"] = "attachment_constraint"
        v.content[0].text = json.dumps(raw)
    assert Camera.read(a) == Camera.read(b)
    assert Camera.read(b).hand == "unavailable" and Camera.read(b).clock == 15.2
    b.content[1].data += "###"
    with pytest.raises(ValueError):
        Camera.read(b)


def test_mcp_session_consumes_current_images_once_and_keeps_finish_separate_from_score():
    import asyncio
    from types import SimpleNamespace

    from test_action_evidence import packet, txt, types

    from manipulation_agent.clients.temporal import episode
    from manipulation_agent.tools import tool_specs

    class Session:
        def __init__(self):
            self.calls = []

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name=t["name"], inputSchema=t["inputSchema"])
                    for t in tool_specs("basic-skills")
                ]
            )

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            if name == "finish":
                return types.CallToolResult(
                    content=[txt({"closed": True, "score": "secret"})]
                )
            response = packet(int(name == "act"), poison=name == "act")
            body = json.loads(response.content[0].text)
            body["robot_time"] = {"total_s": 10 * int(name == "act")}
            response.content[0].text = json.dumps(body)
            return response

    s = Session()
    fast = Role(decision(), decision("done"))
    controller = DispatchController("visible task", [], Role(graph([work("x")])), fast)
    assert asyncio.run(episode(s, controller, lambda: None)) == "achieved"
    assert [n for n, _ in s.calls] == ["initialize", "act", "finish"]
    assert "PRIVATE" not in json.dumps(fast.calls)
    assert s.calls[1][1]["target"]["image_ref"] == "front-0"


def test_due_forecast_prevents_starting_a_long_atom_before_return():
    from manipulation_agent.agent.temporal_graph import Job

    g = graph(
        [work("start"), work("visit"), work("other", seconds=30)],
        [window(earliest=30, latest=60)],
    )
    c = DispatchController(
        "task", ["open", "wait"], Role(g), Role(decision(), decision(op="open"))
    )
    c.observe(camera(0, 0))
    c.compile()
    c.completed["start"] = 0
    c.active = Job(("other",), "A", "A", 30)
    c.observe(camera(1, 21))
    name, args = c.next()
    assert name == "act" and args["primitive"] == "wait" and args["wait_seconds"] == 9
    assert c.active.members == ("visit",)


def test_resource_shared_by_checkpoints_is_kept_until_last_check():
    g = TemporalGraph.read(
        graph(
            [work("on"), work("v1"), work("v2", after=["v1"]), work("s2"), work("v3")],
            [
                window("on", "v1", 10, 50),
                window("on", "v2", 30, 65),
                window("s2", "v3", 10, 40),
            ],
        )
    )
    res = schedule(g, {"on": 10}, region="A", now=10)
    by_id = {s.job.members[0]: s for s in res.slots}
    assert by_id["s2"].start >= by_id["v2"].end


def test_route_length_breaks_equal_makespan_ties_and_required_order_is_immutable():
    g = graph([work("a", "A"), work("b", "B")], region="S", travel=5)
    g.update(
        unknown_metres=12,
        travel=[
            {"origin": a, "destination": b, "seconds": 5, "metres": m}
            for a, b, m in [("S", "A", 5), ("A", "B", 4), ("S", "B", 1), ("B", "A", 1)]
        ],
    )
    assert schedule(TemporalGraph.read(g), {}, region="S", now=0).slots[
        0
    ].job.members == ("b",)
    g["nodes"][1]["after"] = ["a"]
    g["required_order"] = [{"first": "a", "then": "b"}]
    with pytest.raises(ValueError, match="precedence"):
        TemporalGraph.read(g).repair(
            {"upsert": [work("b", "B")], "remove": [], "windows": []}, {"b"}, {}
        )


def test_macro_prefix_is_rebound_to_current_images_after_verified_success():
    g = graph([work("one"), work("two", after=["one"])])
    fast = Role(
        decision(op="grasp"),
        decision("done", key="one"),
        dict(decision(op="grasp"), x=0.72),
        decision("done", key="two"),
    )
    slow = Role(g)
    c = DispatchController("move both", ["grasp"], slow, fast)
    c.observe(camera(0, 0))
    c.next()
    c.observe(camera(1, 10))
    _, args = c.next()
    assert fast.calls[-1]["macro_next_primitive"] == "grasp"
    assert args["target"] == {"image_ref": "front-1", "point": [0.72, 0.5]}
    c.observe(camera(2, 20))
    assert c.next()[1]["outcome"] == "achieved"
    assert len(slow.calls) == 1


def test_visual_navigation_calibrates_only_a_pure_confirmed_trip():
    c = DispatchController(
        "go",
        ["navigate_to"],
        Role(graph([work("trip", "A", "B")])),
        Role(decision(op="navigate_to"), decision("done", key="trip")),
    )
    a, b = camera(0, 0), camera(1, 11)
    a.odometer_m = 3
    b.odometer_m = 7
    c.observe(a)
    c.next()
    c.observe(b)
    c.next()
    assert c.memory.travel == {("A", "B"): 11} and c.memory.metres == {("A", "B"): 4}


def test_time_limited_search_can_dispatch_a_complete_feasible_route():
    g = TemporalGraph.read(kitchen(15, 120))
    res = schedule(g, {}, region="B", now=0, solver_seconds=0.000001)
    assert res.status in ("heuristic_feasible", "optimal", "feasible")
    slots = {k: s for s in res.slots for k in s.job.members}
    assert (
        set(slots) == g.nodes.keys()
        and slots["visit"].start >= slots["start"].end + 180
    )
    assert slots["visit"].end <= slots["start"].end + 500
    assert all(
        a.end + g.distance(a.job.destination, b.job.origin) <= b.start
        for a, b in zip(res.slots, res.slots[1:])
    )


def test_qwen_declared_pixels_map_to_the_original_view_and_reject_padding():
    from manipulation_agent.agent.temporal_models import DECISION, GROUND
    from manipulation_agent.clients.visual_coordinates import (
        motor_decision,
        pixel_request,
    )

    obs = camera(0, 0)
    req, views = pixel_request(
        {"instructions": GROUND, "schema": DECISION, "images": obs.parts()}, 512 * 512
    )
    assert (
        "x_pixel" in req["schema"]["properties"]
        and "x" not in req["schema"]["properties"]
    )
    left, top, w, h = views["front"]
    raw = {**decision(), "x_pixel": left + w - 1, "y_pixel": top + h - 1}
    assert motor_decision(raw, views)["x"] == 1 and motor_decision(raw, views)["y"] == 1
    assert top > 0
    with pytest.raises(ValueError):
        motor_decision({**raw, "y_pixel": 0}, views)


def test_invalid_fast_decision_reaches_local_repair_without_motor_action():
    class BadVisualRole:
        def ask(self, *args):
            raise ValueError("The visual grounder could not return a valid decision")

    slow = Role(
        graph([work("x")]), {"outcome": "blocked", "reason": "No visual alternative"}
    )
    controller = DispatchController("task", ["open"], slow, BadVisualRole())
    controller.observe(camera(0, 0))
    tool, args = controller.next()
    assert tool == "finish" and args["outcome"] == "blocked"
    assert "visual grounder" in slow.calls[-1]["event"] and controller.clock == 0
