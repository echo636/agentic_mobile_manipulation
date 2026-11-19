"""Policy-side tests have no simulator, layout or task evaluator."""

from itertools import permutations
import pytest

from manipulation_agent.agent.temporal_graph import TemporalGraph, schedule
from manipulation_agent.agent.temporal_execution import DispatchController
from manipulation_agent.agent.temporal_memory import Camera, DeltaMemory

ortools = pytest.importorskip("ortools")


def work(key, src="A", dst=None, seconds=10, after=(), load=0):
    return dict(
        id=key,
        work=key,
        origin=src,
        destination=dst or src,
        seconds=seconds,
        after=list(after),
        assertion=key + " visible",
        skill="transfer",
        load=load,
    )


def window(
    start="start",
    check="visit",
    earliest=180,
    latest=250,
    resource="appliance",
    margin=0,
):
    return dict(
        start=start,
        check=check,
        resource=resource,
        earliest=earliest,
        latest=latest,
        margin=margin,
    )


def graph(nodes, windows=(), carriers=(), travel=15, region="A"):
    return dict(
        region=region,
        nodes=nodes,
        windows=list(windows),
        carriers=list(carriers),
        travel=[],
        unknown_travel=travel,
    )


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
    result = schedule(TemporalGraph.read(g), {}, now=0, region="B", serial=serial)
    assert result.status == "optimal"
    assert result.slots[-1].end == brute(g, serial)


def carrier(cap=2, loc="A", handling=2):
    return dict(name="tray", location=loc, capacity=cap, handling=handling)


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
    patch = dict(
        upsert=[work("look", after=["used"]), work("open", after=["look"])],
        remove=[],
        windows=[],
    )
    fixed = g.repair(patch, {"open"}, {"used": 10})
    assert fixed.nodes["open"].after == ("look",) and "look" not in g.nodes
    for invalid in [
        dict(upsert=[work("used")]),
        dict(remove=["serve"]),
        dict(upsert=[work("other")]),
        dict(windows=[window("open", "serve", 10, 100)]),
        dict(upsert=[work("orphan")]),
    ]:
        with pytest.raises(ValueError):
            g.repair({**patch, **invalid}, {"open"}, {"used": 10})


def camera(rev, t):
    return Camera(
        rev,
        {
            view: dict(
                image_ref=f"{rev}_{view}",
                sha256="example",
                data="",
                mime_type="image/png",
            )
            for view in ("front", "left", "back", "right")
        },
        t,
        "unavailable",
    )


def test_only_changes_take_storage_and_history_has_no_current_pixel_ref():
    m = DeltaMemory(keyframe_limit=2)
    fact = dict(
        subject="board", relation="on", value="surface", certainty="seen", view="front"
    )
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
    return dict(
        decision=kind,
        primitive=op,
        view="front",
        x=0.4,
        y=0.5,
        seconds=0,
        yaw=0,
        evidence="front view changed",
        changes=[],
        verified=[dict(id=key, evidence="front view")] if kind == "done" else [],
    )


def test_normal_episode_once_compiled_current_pixels_and_no_slow_repair():
    slow = Role(graph([work("x")]))
    fast = Role(decision(), decision("done"))
    c = DispatchController("user request", ["open"], slow, fast)
    c.observe(camera(0, 0))
    tool, args = c.next()
    assert tool == "act" and args["target"]["image_ref"] == "0_front"
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
        Role(g, dict(outcome="blocked", reason="one item unverified")),
        Role(decision("done", key="a")),
    )
    c.observe(camera(0, 0))
    assert c.next()[1]["outcome"] == "blocked" and c.completed == {}
