"""Tool-free model output contracts for the planning and execution roles."""

import json


def obj(**fields):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": fields,
        "required": list(fields),
    }


def arr(item):
    return {"type": "array", "items": item}


S = {"type": "string"}
I = {"type": "integer"}
F = {"type": "number"}
NODE = obj(
    id=S,
    work=S,
    origin=S,
    destination=S,
    seconds=I,
    after=arr(S),
    assertion=S,
    skill=S,
    load=I,
)
SOURCE = {"type": "string", "enum": ["instruction", "estimate"]}
WINDOW = obj(
    start=S,
    check=S,
    resource=S,
    earliest=I,
    latest=I,
    margin=I,
    earliest_source=SOURCE,
    latest_source=SOURCE,
)
CARRIER = obj(name=S, location=S, capacity=I, handling=I)
TRIP = obj(origin=S, destination=S, seconds=I, metres=F)
GRAPH = obj(
    region=S,
    nodes=arr(NODE),
    required_outcomes=arr(S),
    required_order=arr(obj(first=S, then=S)),
    windows=arr(WINDOW),
    carriers=arr(CARRIER),
    travel=arr(TRIP),
    unknown_travel=I,
    unknown_metres=F,
)
PATCH = obj(
    outcome={"type": "string", "enum": ["repair", "blocked"]},
    reason=S,
    upsert=arr(NODE),
    remove=arr(S),
    windows=arr(WINDOW),
)
VIEWS = {"type": "string", "enum": ["front", "back", "left", "right"]}
FACT = obj(
    subject=S,
    relation=S,
    value=S,
    certainty={"type": "string", "enum": ["seen", "uncertain"]},
    view=VIEWS,
)
DECISION = obj(
    decision={"type": "string", "enum": ["act", "done", "anomaly"]},
    evidence=S,
    primitive=S,
    view=VIEWS,
    x={"type": "number", "minimum": 0, "maximum": 1},
    y={"type": "number", "minimum": 0, "maximum": 1},
    seconds=F,
    yaw=F,
    changes=arr(FACT),
    verified=arr(obj(id=S, evidence=S)),
)

INSPECT_PATCH = obj(
    **PATCH["properties"],
    inspection={
        "anyOf": [
            obj(
                node=S,
                view=VIEWS,
                x=DECISION["properties"]["x"],
                y=DECISION["properties"]["y"],
            ),
            {"type": "null"},
        ]
    },
)
INSPECT_DECISION = obj(
    **{
        **DECISION["properties"],
        "view": {"type": "string", "enum": [*VIEWS["enum"], "inspection"]},
    }
)
INSPECT_REPAIR = """ If you see a plausible candidate for the failed work on a current full RGB view,
attach one optional inspection to an affected node id. Provide its view and normalized [0,1] coordinates
from left/top within that full image, or null if there is no useful visible candidate. It requests digital zoom.
It neither drives the robot nor verifies the assertion."""
INSPECT_GROUND = """The image labelled Current RGB inspection is a magnified crop of the current {source_view} camera,
proposed by the local repair. Inspect its contents yourself. To point inside this crop, choose view=inspection
and report coordinates relative to the entire crop. To point in a full current view, choose that view instead.
A proposed crop alone cannot establish identity or success."""


COMPILE = """Compile the user's request into a single mobile robot's remaining work, using only the
current RGB and user information. Include one required_outcomes node id for every explicit user outcome, even when other work depends on it. List explicit user order relations in required_order. Preserve required order and terminal outcomes. Unobserved facts,
durations, distances and usable carrier capacity are estimates. Name compact stable semantic ids and
regions. Each node is an independently verifiable piece of work: indicate source and ending region,
required predecessor ids, active HANDLING seconds EXCLUDING travel and passive work, a reusable skill
name and an observable postcondition. A different camera direction is not a different region.
Model each autonomous process by a short startup node releasing the robot and a separate return/check
node. Give the relative earliest check-start and latest check-COMPLETION and return margin, in robot
seconds from startup completion. Mark a bound as instruction only when explicitly stated by the user, otherwise estimate. Use the same resource name for jobs sharing a device. These windows
schedule observations; a timer by itself does not prove a physical outcome. Do not require work to wait
unless the user or physical prerequisites require it. Retain uncertainty about occluded things; seeing
an open container is a different assertion from finding a particular object in it.
Use one node per individually transportable item on a common route so the scheduler can compare batching.
load is estimated size units for such a delivery, zero otherwise. Introduce a carrier only if supported
by the user's information or visible evidence; name its probable current region, conservative capacity
in those units, and added handling cost in seconds. Include directed inter-region transit estimates in seconds and route metres.
Choose a compact graph with a checkable final outcome. Do not supply pixels or a simulator identifier."""
REPAIR = """Return only local edits to the supplied unfinished scope. State what new observation changes
the method. Preserve verified work, unchanged user goals and existing ids. Keep terminal assertions
verbatim. You can insert a prerequisite, revise work, reassign a region, or revise a process check
time based on public evidence. A past estimated deadline is a missed forecast: inspect the process
now; do not call it cooked, lost or otherwise resolved without evidence. Include unchanged full fields
for upserted nodes. Substantive replacement nodes must connect to existing obligations. Never treat a
failed placement as a delivered object. If trustworthy progress is impossible, explicitly return blocked.
If equipment or contents are only occluded, consider an observation from a different accessible viewpoint."""
GROUND = """You operate one mobile manipulator using current front/back/left/right RGB and the supplied
public context. Work on the dispatched node only. Inspect current images and return one next action with
a current view and a precise normalized [0,1] interior point, or done with visible evidence of ALL
required outcomes (supply one id and visible evidence per selected assertion in verified; return an empty array while acting), or anomaly if the method must change. The vision-based estimate can be uncertain.
Use the listed primitive catalog. A manipulation can approach the visible target automatically. Open/close require an empty hand. Place selects the receiver or support. A held vessel can carry its supported contents. release and wait need no point. On a navigation-time opportunity before an observation window opens, you may reposition but may not perform or verify the check early. For looking use primitive look and yaw degrees. For wait use seconds.
Irrelevant numeric fields can be zero. Never click a historical image. Inspect access before grasping
contents. Select a current view/approach with a clear target when the target is obscured. Maintaining a
grasp, a successful command, or the mere passage of time does not establish identity, delivery, or
doneness. Before lifting a group verify it is supported, and inspect again after transfer. Use the recent
event and keyframes. When a cached macro_next_primitive is available, ground that step on the current image if its prerequisites hold and it serves the selected work. Otherwise choose a new action or report an anomaly. Report only changed observed object relations as
changes, with view and certainty. If the evidence is insufficient prefer another observation over a guess.
If the same evidence contradicts the current method, report anomaly with your observation."""


class ModelRole:
    def __init__(self, request, model, effort):
        self.request = request
        self.model = model
        self.effort = effort

    def ask(self, instructions, schema, context, images):
        content = [
            {"type": "input_text", "text": json.dumps(context, ensure_ascii=False)},
            *images,
        ]
        response = self.request(
            {
                "model": self.model,
                "reasoning": {"effort": self.effort},
                "instructions": instructions,
                "store": False,
                "input": [{"role": "user", "content": content}],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "decision",
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
        )
        text = "".join(
            part.get("text", "")
            for item in response.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        return json.loads(text)
