"""Closed-loop dispatch. Only a motor response can advance the robot clock."""

import math
from dataclasses import asdict

from .temporal_graph import Job, TemporalGraph, schedule
from .temporal_memory import DeltaMemory
from .temporal_models import COMPILE, DECISION, GRAPH, GROUND, PATCH, REPAIR


class DispatchController:
    def __init__(
        self, instruction, catalog, planner, grounder, *, serial=False, log=None
    ):
        self.instruction = instruction
        self.catalog = tuple(catalog)
        self.planner, self.grounder = planner, grounder
        self.serial = serial
        self.log = log or (lambda kind, **row: None)
        self.memory = DeltaMemory()
        self.completed = {}
        self.carriers = {}
        self.operations = {}
        self.uncacheable = set()
        self.pending = self.active = self.graph = self.camera = self.region = None
        self.clock = self.due_wait = 0
        self.repair_events = set()
        self.previous_selection = None
        self.repeat = 0
        self.travel_sample = None
        self.motor_seconds = {}
        self.return_reserve = 0

    def observe(self, camera):
        if self.camera and camera.revision <= self.camera.revision:
            raise ValueError("A motor request requires a new camera revision")
        if (
            self.camera
            and camera.odometer_m is not None
            and self.camera.odometer_m is not None
            and camera.odometer_m < self.camera.odometer_m
        ):
            raise ValueError("Odometer moved backwards")
        if camera.clock is None and self.camera:
            raise ValueError(
                "A robot elapsed-time reading is required after a motor action"
            )
        if camera.clock is not None:
            if camera.clock < self.clock:
                raise ValueError("Robot clock moved backwards")
            self.clock = camera.clock
        before = self.camera
        self.camera = camera
        if self.pending:
            op, prior, target = self.pending
            self.memory.motor_readback(op, target, before, camera)
            elapsed = self.clock - prior
            self.motor_seconds[op] = max(elapsed, self.motor_seconds.get(op, 0))
            if op == "navigate_to" and self.travel_sample is not None:
                self.travel_sample["seconds"] += elapsed
                self.travel_sample["count"] += 1
            else:
                self.travel_sample = None
            self.log(
                "action_observed", op=op, revision=camera.revision, robot_s=self.clock
            )
            self.memory.recent.append(
                {"op": op, "revision": camera.revision, "elapsed_s": round(elapsed, 1)}
            )
            self.pending = None
            self.repair_events.clear()

    def ask(self, role, instructions, schema, context, *, history=""):
        images = self.camera.parts()
        if history:
            images += self.memory.panel_parts
            images += self.memory.reference(
                history,
                limit=1 if self.memory.panel_parts else 2,
                before=self.camera.revision,
            )
        self.log(
            "model_call",
            role=role,
            revision=self.camera.revision,
            context=context,
            image_count=sum(i["type"] == "input_image" for i in images),
        )
        model = self.grounder if role == "fast" else self.planner
        result = model.ask(instructions, schema, context, images)
        self.log("model_answer", role=role, result=result)
        return result

    def compile(self):
        context = {"task": self.instruction, "primitives": list(self.catalog)}
        for _ in range(2):
            raw = self.ask("compile", COMPILE, GRAPH, context)
            try:
                self.graph = TemporalGraph.read(raw)
            except (ValueError, TypeError, KeyError) as exc:
                context["schema_error"] = str(exc)[:400]
                self.log("invalid_graph", reason=context["schema_error"])
                continue
            self.region = self.graph.region
            self.reset_travel_sample()
            self.log("compiled", graph=raw)
            return True
        return False

    def repair(self, reason, keys):
        scope = self.graph.affected(keys) - self.completed.keys()
        event = (frozenset(keys), reason[:100])
        if not scope or event in self.repair_events:
            return False
        self.repair_events.add(event)
        if self.active:
            self.uncacheable.add(self.active.members)
        context = {
            "task": self.instruction,
            "event": reason[:600],
            "robot_s": self.clock,
            "region": self.region,
            "time_windows": self.window_status(),
            "affected": [
                asdict(self.graph.nodes[k]) for k in self.graph.nodes if k in scope
            ],
            "boundary_prerequisites": sorted(
                {d for k in scope for d in self.graph.dependencies(k)} - scope
            ),
            "required_order": [
                [a, b] for a, b in self.graph.required_order if a in scope or b in scope
            ],
            "protected_terminal_assertions": {
                k: self.graph.nodes[k].assertion
                for k in scope
                if k in self.graph.required_outcomes
                or not any(k in self.graph.dependencies(n) for n in self.graph.nodes)
            },
            "windows": [
                asdict(w) for w in self.graph.windows.values() if w.check in scope
            ],
            "memory": self.memory.context(reason),
        }
        self.memory.remember_frame(("anomaly", reason[:60]), self.camera, "front")
        for _ in range(2):
            patch = self.ask("repair", REPAIR, PATCH, context, history=reason)
            if patch["outcome"] == "blocked":
                self.log("reported_blocked", reason=patch["reason"])
                return False
            try:
                graph = self.graph.repair(patch, scope, self.completed)
                if graph == self.graph:
                    raise ValueError("The repair did not change the failed method")
            except (TypeError, ValueError, KeyError) as exc:
                context["edit_error"] = str(exc)[:400]
                continue
            self.graph = graph
            self.active = None
            self.previous_selection = None
            self.repeat = 0
            self.log("repaired", scope=sorted(scope), patch=patch)
            return True
        return False

    def window_status(self):
        return sorted(
            (
                {
                    "check": w.check,
                    "resource": w.resource,
                    "earliest_in_s": round(
                        self.completed[w.start] + w.earliest - self.clock, 3
                    ),
                    "latest_in_s": round(
                        self.completed[w.start] + w.latest - w.margin - self.clock, 3
                    ),
                }
                for w in self.graph.windows.values()
                if w.start in self.completed and w.check not in self.completed
            ),
            key=lambda w: w["latest_in_s"],
        )

    def reset_travel_sample(self):
        self.travel_sample = {
            "from": self.region,
            "seconds": 0.0,
            "odometer": self.camera.odometer_m,
            "count": 0,
        }

    def choose(self):
        self.graph.travel.update(self.memory.travel)
        self.graph.route_metres.update(self.memory.metres)
        result = schedule(
            self.graph,
            self.completed,
            region=self.region,
            now=self.clock,
            carrier_locations=self.carriers,
            serial=self.serial,
        )
        if result.status == "unknown":
            # A solver timeout is not evidence of a physical obstacle. Try the smaller
            # circuit of single trips before requesting a semantic repair.
            self.log(
                "solver_timeout", group_search=not self.serial, solver_s=result.seconds
            )
            result = schedule(
                self.graph,
                self.completed,
                region=self.region,
                now=self.clock,
                carrier_locations=self.carriers,
                grouping=False,
                serial=self.serial,
                solver_seconds=4,
            )
        self.log(
            "schedule",
            status=result.status,
            solver_s=result.seconds,
            robot_s=self.clock,
            slots=[asdict(s) for s in result.slots],
        )
        if result.slots:
            slot = result.slots[0]
            self.active = slot.job
            self.due_wait = max(0, slot.depart - math.ceil(self.clock))
        return result.status

    def motor(self, op, args):
        self.pending = (op, self.clock, args.get("target"))
        self.log(
            "motor_request",
            op=op,
            arguments=args,
            revision=self.camera.revision,
            selected=list(self.active.members) if self.active else [],
        )
        return ("look" if op == "look" else "act"), args

    def act_args(self, op, target=None, wait=None):
        return {
            "primitive": op,
            "revision": self.camera.revision,
            "target": target,
            "placement_yaw_degrees": None,
            "placement_roll_degrees": None,
            "wait_seconds": wait,
        }

    def arguments(self, decision):
        op = decision["primitive"]
        if op == "look":
            yaw = decision["yaw"]
            if type(yaw) not in (int, float) or not 0 < abs(yaw) <= 90:
                raise ValueError("Use a nonzero look angle within 90 degrees")
            return op, {"revision": self.camera.revision, "yaw_degrees": yaw}
        if op not in self.catalog:
            raise ValueError("Unknown motor primitive")
        if op == "wait":
            seconds = decision["seconds"]
            if type(seconds) not in (int, float) or not 0.1 <= seconds <= 120:
                raise ValueError("Use a wait from 0.1 to 120 robot seconds")
            return op, self.act_args(op, wait=seconds)
        if op == "release":
            return op, self.act_args(op)
        view = decision["view"]
        point = [decision["x"], decision["y"]]
        if view not in self.camera.frames or not all(
            type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in point
        ):
            raise ValueError("A current view and normalized point are required")
        return op, self.act_args(
            op, {"image_ref": self.camera.frames[view]["image_ref"], "point": point}
        )

    def wait(self, seconds, windows):
        events = [
            w["latest_in_s"]
            - self.graph.distance(self.region, self.graph.nodes[w["check"]].origin)
            - self.graph.nodes[w["check"]].seconds
            for w in windows
        ]
        amount = max(0.1, min(120, seconds, *(t for t in events if t >= 0.1)))
        return self.motor("wait", self.act_args("wait", wait=amount))

    def context(self, nodes, skill, windows):
        performed = self.operations.get(self.active.members, [])
        macro = self.memory.macros.get(skill, ())
        cursor = len(performed)
        next_primitive = (
            macro[cursor]
            if cursor < len(macro) and tuple(performed) == macro[:cursor]
            else None
        )
        if next_primitive:
            self.log("macro_step", skill=skill, index=cursor, primitive=next_primitive)
        return {
            "task": self.instruction,
            "selected": [
                {"id": n.id, "work": n.work, "assertion": n.assertion} for n in nodes
            ],
            "carrier": self.active.carrier,
            "source": self.active.origin,
            "destination": self.active.destination,
            "primitives": list(self.catalog),
            "time_windows": windows,
            "robot_s": self.clock,
            "gripper_sensor": self.camera.hand,
            "memory": self.memory.context(" ".join(n.work for n in nodes)),
            "prior_confirmed_macro": macro,
            "macro_next_primitive": next_primitive,
            "actions_on_this_work": self.operations.get(self.active.members, [])[-8:],
        }

    def complete(self, decision, skill):
        job = self.active
        evidence = {row["id"]: row["evidence"].strip() for row in decision["verified"]}
        if set(evidence) != set(job.members) or not all(evidence.values()):
            raise ValueError(
                "Each dispatched obligation needs its own current visual evidence"
            )
        self.completed.update(dict.fromkeys(job.members, self.clock))
        trip = self.travel_sample
        if trip and trip["count"]:
            metres = (
                self.camera.odometer_m - trip["odometer"]
                if self.camera.odometer_m is not None and trip["odometer"] is not None
                else None
            )
            self.memory.duration(trip["from"], job.destination, trip["seconds"], metres)
        self.region = job.destination
        self.reset_travel_sample()
        if job.carrier:
            self.carriers[job.carrier] = job.destination
        ops = self.operations.pop(job.members, [])
        if job.members not in self.uncacheable:
            self.memory.learn_macro(skill, ops)
        self.uncacheable.discard(job.members)
        self.memory.recent.append(
            {"verified": list(job.members), "evidence": decision["evidence"][:350]}
        )
        self.log(
            "verified", ids=list(job.members), evidence=evidence, robot_s=self.clock
        )
        self.active = self.previous_selection = None
        self.repeat = 0

    def advance(self, decision, skill, windows):
        """Return a motor request, or None after verifying a graph node."""
        job = self.active
        evidence = decision["evidence"].strip()
        if not evidence:
            raise ValueError("A grounding decision requires an observation")
        changes = self.memory.observe(decision["changes"], self.camera)
        if changes:
            self.log("delta", facts=changes)
        early = max(
            [0] + [w["earliest_in_s"] for w in windows if w["check"] in job.members]
        )
        if decision["decision"] == "anomaly":
            raise ValueError(evidence[:600])
        if early > 0 and (
            decision["decision"] == "done"
            or decision["primitive"] not in ("look", "navigate_to")
        ):
            return self.wait(early, windows)
        if decision["decision"] == "done":
            self.complete(decision, skill)
            return None
        if decision["decision"] != "act":
            raise ValueError("Unknown grounding decision")
        op, args = self.arguments(decision)
        signature = (
            op,
            decision["view"],
            round(decision["x"], 2),
            round(decision["y"], 2),
            job.members,
        )
        self.repeat = self.repeat + 1 if signature == self.previous_selection else 1
        self.previous_selection = signature
        if self.repeat >= 2:
            self.uncacheable.add(job.members)
        if self.repeat >= 3 and not changes and op not in ("wait", "look"):
            raise ValueError(
                "Repeated motor selection without a recorded state change: "
                + evidence[:350]
            )
        if windows and op != "wait" and windows[0]["check"] not in job.members:
            check = self.graph.nodes[windows[0]["check"]]
            reserve = self.motor_seconds.get(
                op,
                max(
                    1,
                    min(n.seconds for n in (self.graph.nodes[k] for k in job.members)),
                ),
            )
            remaining = (
                windows[0]["latest_in_s"]
                - self.graph.distance(self.region, check.origin)
                - check.seconds
            )
            if reserve >= remaining:
                self.return_reserve = reserve
                return None
        self.operations.setdefault(job.members, []).append(op)
        if op == "wait":
            return self.wait(args["wait_seconds"], windows)
        return self.motor(op, args)

    def next(self):
        if self.pending:
            raise RuntimeError("Read the motor result before requesting another action")
        if self.graph is None and not self.compile():
            return "finish", {
                "outcome": "blocked",
                "reason": "Could not compile a valid graph.",
            }
        while len(self.completed) < len(self.graph.nodes):
            windows = self.window_status()
            due = next(
                (
                    w
                    for w in windows
                    if w["latest_in_s"]
                    <= self.graph.distance(
                        self.region, self.graph.nodes[w["check"]].origin
                    )
                    + self.graph.nodes[w["check"]].seconds
                    + self.return_reserve
                ),
                None,
            )
            self.return_reserve = 0
            if due and (not self.active or due["check"] not in self.active.members):
                key = due["check"]
                node = self.graph.nodes[key]
                if not self.graph.dependencies(key) <= self.completed.keys():
                    if self.repair(
                        "Upcoming process check has unfinished prerequisites", {key}
                    ):
                        continue
                    return "finish", {
                        "outcome": "blocked",
                        "reason": "Process visit has unmet prerequisites.",
                    }
                self.log(
                    "timer_interrupt",
                    check=key,
                    remaining_s=due["latest_in_s"],
                    interrupted=list(self.active.members) if self.active else [],
                )
                self.active = Job((key,), node.origin, node.destination, node.seconds)
                self.due_wait = 0
            if self.active is None:
                status = self.choose()
                if self.active is None:
                    keys = {w["check"] for w in windows} or (
                        self.graph.nodes.keys() - self.completed.keys()
                    )
                    if self.repair("No dispatchable schedule: " + status, keys):
                        continue
                    return "finish", {
                        "outcome": "blocked",
                        "reason": "Scheduling ended with " + status,
                    }
            if self.due_wait >= 0.1:
                delay = self.due_wait
                self.active = None
                self.due_wait = 0
                return self.wait(delay, windows)
            job = self.active
            if self.memory.access_cycle:
                self.memory.access_cycle = False
                event = "Alternating motor commands twice restored matching earlier RGB views and points without verifying this work."
                if self.repair(event, set(job.members)):
                    continue
                return "finish", {"outcome": "blocked", "reason": event}
            nodes = [self.graph.nodes[k] for k in job.members]
            skill = ("batch " if job.carrier else "") + nodes[0].skill
            context = self.context(nodes, skill, windows)
            try:
                decision = self.ask(
                    "fast",
                    GROUND,
                    DECISION,
                    context,
                    history=" ".join(n.work for n in nodes),
                )
                request = self.advance(decision, skill, windows)
            except (ValueError, KeyError, TypeError) as exc:
                reason = str(exc)[:600]
                if self.repair(reason, set(job.members)):
                    continue
                return "finish", {"outcome": "blocked", "reason": reason}
            if request:
                return request
        return "finish", {
            "outcome": "achieved",
            "reason": "Grounder reported visual evidence for every scheduled obligation.",
        }
