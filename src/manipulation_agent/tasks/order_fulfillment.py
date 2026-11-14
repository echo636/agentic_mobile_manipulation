"""Public finite task model for two processing orders, separate from physics.

Everything in Contract is public. Box retention, in-box cutting and individual
handling are declared capabilities, not inferred OmniGibson properties. This
module has no search, simulator imports, hidden task state, or network access.
"""
from dataclasses import asdict, dataclass
from itertools import permutations

SURFACES = (0, 1, 2)
IN_BOX = (3, 4)
HELD, ABSENT = 5, 6
NAMES = ('box_a', 'box_b', 'tool', 'raw_a', 'raw_b', 'a1', 'a2', 'b1', 'b2')
BOXES, TOOL, RAWS, HALVES = (0, 1), 2, (3, 4), ((5, 6), (7, 8))
State = tuple[int, ...]  # robot station followed by positions in NAMES order


@dataclass(frozen=True)
class Contract:
    name: str
    coordinates: tuple[int, int, int]
    box_start: tuple[int, int] = (1, 2)
    box_goal: tuple[int, int] = (1, 2)
    tool_start: int = 0
    tool_goal: int = 0
    capacity: int = 5
    raw_start: tuple[int, int] = (0, 0)
    robot_start: int = 0

    def __post_init__(self):
        if len(self.coordinates) != 3 or len(set(self.coordinates)) != 3:
            raise ValueError('Three distinct station coordinates are required')
        if any(type(v) is not int for v in self.coordinates):
            raise ValueError('Coordinates must be integers')
        for pair in (self.box_start, self.box_goal, self.raw_start):
            if len(pair) != 2 or any(type(v) is not int or v not in SURFACES for v in pair):
                raise ValueError('Each order needs a station in 0..2')
        if any(type(v) is not int or v not in SURFACES
               for v in (self.tool_start, self.tool_goal, self.robot_start)):
            raise ValueError('Tool and robot stations must be in 0..2')
        if type(self.capacity) is not int or self.capacity < 4:
            raise ValueError('Each box must fit both raw items; capacity >= 4')

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        for key in ('coordinates', 'box_start', 'box_goal', 'raw_start'):
            if key in data:
                data[key] = tuple(data[key])
        return cls(**data)

    def public(self):
        return asdict(self)


def initial(task: Contract) -> State:
    return (task.robot_start, *task.box_start, task.tool_start, *task.raw_start,
            ABSENT, ABSENT, ABSENT, ABSENT)


def location(state: State, item: int) -> int:
    pos = state[item + 1]
    if pos in IN_BOX:
        pos = state[pos - 3 + 1]
    return state[0] if pos == HELD else pos


def used(state: State, box: int) -> int:
    return sum((2 if i in RAWS else 1) for i in range(2, len(NAMES))
               if state[i + 1] == IN_BOX[box])


def assert_state(state: State, task: Contract):
    assert len(state) == len(NAMES) + 1 and state[0] in SURFACES
    assert all(p in (*SURFACES, HELD) for p in state[1:3])
    assert all(p in (*SURFACES, *IN_BOX, HELD, ABSENT) for p in state[3:])
    assert state[TOOL + 1] != ABSENT
    assert state[1:].count(HELD) <= 1
    assert all(used(state, box) <= task.capacity for box in BOXES)
    for raw, halves in zip(RAWS, HALVES):
        present_raw = state[raw + 1] != ABSENT
        assert all((state[h + 1] == ABSENT) == present_raw for h in halves)


def complete(state: State, task: Contract) -> bool:
    return (all(state[raw + 1] == ABSENT for raw in RAWS)
            and all(state[h + 1] == IN_BOX[box] for box, pair in enumerate(HALVES) for h in pair)
            and tuple(state[i + 1] for i in BOXES) == task.box_goal
            and state[TOOL + 1] == task.tool_goal and HELD not in state[1:])


def transitions(state: State, task: Contract):
    """All legal actions; no method constraints or preferred strategy."""
    robot, positions = state[0], state[1:]
    for target in SURFACES:
        if target != robot:
            yield f'move:{target}', (target, *positions), abs(task.coordinates[target] - task.coordinates[robot])
    held = next((i for i, p in enumerate(positions) if p == HELD), None)
    if held is None:
        for item, pos in enumerate(positions):
            accessible = pos == robot or (pos in IN_BOX and positions[pos - 3] == robot)
            if not accessible:
                continue
            after = list(state); after[item + 1] = HELD
            yield f'pick:{NAMES[item]}', tuple(after), 1
    else:
        after = list(state); after[held + 1] = robot
        yield f'put:{NAMES[held]}:surface', tuple(after), 1
        if held not in BOXES:
            size = 2 if held in RAWS else 1
            for box in BOXES:
                if positions[box] == robot and used(state, box) + size <= task.capacity:
                    after = list(state); after[held + 1] = IN_BOX[box]
                    yield f'put:{NAMES[held]}:{NAMES[box]}', tuple(after), 1
    if positions[TOOL] == HELD:
        for raw, halves in zip(RAWS, HALVES):
            pos = positions[raw]
            if pos == robot or (pos in IN_BOX and positions[pos - 3] == robot):
                after = list(state); after[raw + 1] = ABSENT
                for half in halves:
                    after[half + 1] = pos
                yield f'cut:{NAMES[raw]}', tuple(after), 1


def replay(task: Contract, actions: list[str]):
    state, cost, history = initial(task), 0, []
    if not isinstance(actions, list) or len(actions) > 100:
        return dict(valid=False, complete=False, error='Expected at most 100 actions')
    for step, action in enumerate(actions, 1):
        match = next(((after, weight) for label, after, weight in transitions(state, task)
                      if action == label), None)
        if match is None:
            return dict(valid=False, complete=False, error=f'Illegal action at step {step}: {action}', history=history)
        before = state
        state, weight = match
        assert_state(state, task)
        cost += weight
        history.append(dict(step=step, action=action, before=before, after=state, cost=cost))
    return dict(valid=True, complete=complete(state, task), cost=cost,
                manipulations=sum(not a.startswith('move:') for a in actions),
                travel=cost - sum(not a.startswith('move:') for a in actions), history=history,
                cut_sites=[r['before'][0] for r in history if r['action'].startswith('cut:')])


def lower_bound(state: State, task: Contract) -> int:
    """Admissible handling plus shortest route through unavoidable stations.

One unit per remaining cut; one pick/place per misplaced existing product or
uncut input is a relaxation (handling inputs can avoid handling outputs).
Every box with unfinished contents must be visited, wherever it is eventually
loaded. Moving that box still requires visiting its present and final stations.
"""
    required, handling = set(), 0
    positions = state[1:]
    cuts_left = any(positions[i] != ABSENT for i in RAWS)
    unfinished_boxes = set()
    for box, (raw, halves) in enumerate(zip(RAWS, HALVES)):
        members = (raw,) if positions[raw] != ABSENT else halves
        if positions[raw] != ABSENT:
            handling += 1
            required.add(location(state, raw))
        for item in members:
            if positions[item] != IN_BOX[box]:
                handling += 1 if positions[item] == HELD else 2
                required.add(location(state, item))
                unfinished_boxes.add(box)
    for box in BOXES:
        if positions[box] != task.box_goal[box]:
            handling += 1 if positions[box] == HELD else 2
            required.update((location(state, box), task.box_goal[box]))
        if box in unfinished_boxes:
            required.update((location(state, box), task.box_goal[box]))
    if cuts_left or positions[TOOL] != task.tool_goal:
        handling += 1 if positions[TOOL] == HELD else 2
        required.update((location(state, TOOL), task.tool_goal))
    required.discard(state[0])
    travel = min((sum(abs(task.coordinates[a] - task.coordinates[b])
                      for a, b in zip((state[0], *order), order))
                  for order in permutations(required)), default=0)
    return handling + travel


def public_prompt(task: Contract) -> str:
    """Full public semantics, without methods, reference costs, or solution paths."""
    import json
    return f'''Complete two material orders in a deterministic discrete environment.
Public task parameters: {json.dumps(task.public(), ensure_ascii=False)}
Stations are 0, 1, 2. Their coordinates are one-dimensional route coordinates,
not private simulator geometry. The robot starts at robot_start. A move from i
to j costs abs(coordinates[i] - coordinates[j]); every other action costs 1.
Minimize the sum. There is one hand and unlimited staging space at each station.

Objects: box_a, box_b, tool, raw_a, raw_b. The boxes start at box_start; the
raw items at raw_start; the tool at tool_start. Initially a1,a2,b1,b2 do not
exist. One cut transforms raw_a into a1,a2, or raw_b into b1,b2, at exactly its
current surface or inside its current box. The tool remains held. A raw uses
2 capacity units, each half 1, the tool 1. Each box has the declared capacity.
Both boxes are already open and stay open. Cuts preserve occupied capacity.

Final goals, all simultaneous: a1 and a2 inside box_a; b1 and b2 inside box_b;
box_a at box_goal[0], box_b at box_goal[1]; tool directly on the tool_goal surface;
no raw items remaining; empty hand. Robot final station is free. Each order's
halves have the same goal, but the two orders are distinct. Boxes can temporarily
carry either order or the tool. Box contents remain inside during box transport.

Legal action strings:
- move:N (N=0,1,2): move to a different station, carrying what is held.
- pick:OBJECT: hand must be empty; object must be on the current surface or
  inside a box resting at this station. Picking a box carries its contents.
- put:OBJECT:surface: put the held object onto the current surface, free hand.
- put:OBJECT:box_a or put:OBJECT:box_b: put the held non-box object inside a box
  resting at the current station, with enough remaining capacity; free hand.
- cut:raw_a or cut:raw_b: hold tool; the raw must be on the current surface
  or inside a box resting at the current station. Replace it with its two halves.

Nothing else moves the robot. The hand holds one loose object OR one loaded box.
Boxes cannot nest; loose-object stacking, pouring, remote actions and implicit
loading/unloading are outside this finite model. These are explicit abstract
capabilities, not a statement about an unrestricted physical robot. All legal
actions succeed; no failures or hidden state exist. Return JSON with actions
(the complete ordered list of action strings) and notes (a brief public plan).
'''
