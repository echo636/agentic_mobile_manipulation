"""Closed-loop dispatch. Only a motor response can advance the robot clock."""

import math
from dataclasses import asdict

from .temporal_graph import (
    InvalidOrderReferences,
    Job,
    TemporalGraph,
    correct_order_references,
    schedule,
)
from .temporal_memory import CurrentInspection, DeltaMemory
from .temporal_models import (
    CARRIER_GROUND,
    COMPILE,
    DECISION,
    GRAPH,
    GROUND,
    INSPECT_DECISION,
    INSPECT_GROUND,
    INSPECT_PATCH,
    INSPECT_REPAIR,
    METHOD_REPAIR,
    ORDER_CORRECTION,
    PATCH,
    REPAIR,
    carrier_decision,
    method_schema,
    order_correction_schema,
)


class DispatchController:
    def __init__(
        self,
        instruction,
        catalog,
        planner,
        grounder,
        *,
        serial=False,
        grouping=True,
        overlap=True,
        log=None,
        repair_inspection=True,
        repair_motor_options=False,
    ):
        self.instruction = instruction
        self.catalog = tuple(catalog)
        self.planner, self.grounder = planner, grounder
        self.serial = serial
        self.grouping = grouping and not serial
        self.overlap = overlap and not serial
        self.log = log or (lambda kind, **row: None)
        self.memory = DeltaMemory()
        self.repair_inspection = repair_inspection
        self.repair_motor_options = repair_motor_options
        self.methods = {}
        self.inspection = None
        self.completed = {}
        self.carriers = {}
        self.loaded_at = {}
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
            similar = self.memory.motor_readback(op, target, before, camera)
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
            event = {
                "op": op,
                "revision": camera.revision,
                "elapsed_s": round(elapsed, 1),
            }
            if similar is not None and self.active:
                event["four_views_and_target_similar"] = similar
                event["work"] = list(self.active.members)
            self.memory.recent.append(event)
            self.pending = None
            self.repair_events.clear()

    def ask(self, role, instructions, schema, context, *, history=""):
        images = self.camera.parts()
        focus = self.inspection
        if (
            role != "fast"
            or not focus
            or not focus.matches(
                self.camera, [r["id"] for r in context.get("selected", ())]
            )
        ):
            focus = None
        if focus:
            instructions += "\n" + INSPECT_GROUND.format(source_view=focus.source_view)
            schema = INSPECT_DECISION
            context = {
                **context,
                "current_inspection": {
                    "node": focus.node,
                    "view": focus.source_view,
                    "revision": focus.revision,
                    "box": focus.box,
                },
            }
        if history:
            images += self.memory.panel_parts
            remaining = 2 - int(bool(self.memory.panel_parts)) - int(bool(focus))
            if remaining:
                images += self.memory.reference(
                    history, limit=remaining, before=self.camera.revision
                )
        if focus:
            images += focus.parts(self.camera)
        options = context.get("repaired_motor_options") if role == "fast" else None
        if options:
            schema = method_schema(schema, options, repair=False)
            instructions += (
                "\nThe local repair specified motor options for this work. Choose one for act. "
                "Use primitive=none for done or anomaly. Report an anomaly if the method needs "
                "a different action. Completion still requires current visual evidence."
            )
        if role == "fast" and context.get("carrier"):
            schema = carrier_decision(schema, (n["id"] for n in context["selected"]))
            instructions += "\n" + CARRIER_GROUND
        if role == "fast" and context.get("unlocated_carriers"):
            instructions += (
                "\nIf you see a listed unlocated carrier at the current work's source or "
                "destination, report its exact name, relation=location, region name, "
                "certainty=seen and current view in changes."
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
        if (
            focus
            and result.get("view") == "inspection"
            and result.get("decision") == "act"
        ):
            x, y = focus.full_point(result["x"], result["y"])
            self.log(
                "inspection_target",
                revision=focus.revision,
                view=focus.source_view,
                point=[x, y],
                box=focus.box,
            )
            return {**result, "view": focus.source_view, "x": x, "y": y}
        return result

    def compile(self):
        context = {"task": self.instruction, "primitives": list(self.catalog)}
        for _ in range(2):
            raw = self.ask("compile", COMPILE, GRAPH, context)
            try:
                try:
                    self.graph = TemporalGraph.read(raw)
                except InvalidOrderReferences as invalid:
                    nodes = {n["id"]: n["work"] for n in raw["nodes"]}
                    entries = [
                        {"entry": i, **raw["required_order"][i]}
                        for i in invalid.positions
                    ]
                    correction = self.ask(
                        "compile_order",
                        ORDER_CORRECTION,
                        order_correction_schema(nodes, invalid.positions),
                        {"task": self.instruction, "nodes": nodes, "entries": entries},
                    )
                    if correction["status"] != "corrected":
                        raise ValueError(
                            "Named nodes cannot represent the declared explicit order"
                        ) from invalid
                    raw = correct_order_references(
                        raw, invalid.positions, correction["fixes"]
                    )
                    self.graph = TemporalGraph.read(raw)
                    self.log("order_corrected", entries=list(invalid.positions))
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
        members = frozenset(keys)
        bind_method = (
            self.repair_motor_options
            and self.active is not None
            and members == set(self.active.members)
        )
        instructions = REPAIR + INSPECT_REPAIR if self.repair_inspection else REPAIR
        schema = INSPECT_PATCH if self.repair_inspection else PATCH
        if bind_method:
            catalog = list(dict.fromkeys((*self.catalog, "look")))
            context.update(
                method_for=sorted(members),
                available_motors=catalog,
                previous_motor_options=self.methods.get(members),
            )
            instructions += METHOD_REPAIR
            schema = method_schema(schema, catalog, repair=True)
        for _ in range(2):
            patch = self.ask("repair", instructions, schema, context, history=reason)
            if patch["outcome"] == "blocked":
                self.log("reported_blocked", reason=patch["reason"])
                return False
            try:
                graph = self.graph.repair(patch, scope, self.completed)
                methods = {
                    group: options
                    for group, options in self.methods.items()
                    if all(graph.nodes.get(k) == self.graph.nodes[k] for k in group)
                }
                if bind_method:
                    options = patch["motor_options"]
                    methods.pop(members, None)
                    if options is not None:
                        if (
                            not isinstance(options, list)
                            or not options
                            or any(op not in catalog for op in options)
                            or not members <= graph.nodes.keys()
                        ):
                            raise ValueError(
                                "Motor options require public commands and surviving interrupted work"
                            )
                        methods[members] = tuple(op for op in catalog if op in options)
                proposed = patch.get("inspection") if self.repair_inspection else None
                eligible = (
                    scope | (graph.nodes.keys() - self.graph.nodes.keys())
                ) & graph.nodes.keys()
                focus = (
                    CurrentInspection.bind(proposed, self.camera, eligible)
                    if proposed is not None
                    else None
                )
                if (
                    graph == self.graph
                    and methods == self.methods
                    and (focus is None or focus == self.inspection)
                ):
                    raise ValueError("The repair did not change the failed method")
            except (TypeError, ValueError, KeyError) as exc:
                context["edit_error"] = str(exc)[:400]
                continue
            self.graph = graph
            self.methods = methods
            self.inspection = focus
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
            grouping=self.grouping,
            overlap=self.overlap,
        )
        if result.status == "unknown":
            # A solver timeout is not evidence of a physical obstacle. Try the smaller
            # circuit of single trips before requesting a semantic repair.
            self.log(
                "solver_timeout", group_search=self.grouping, solver_s=result.seconds
            )
            result = schedule(
                self.graph,
                self.completed,
                region=self.region,
                now=self.clock,
                carrier_locations=self.carriers,
                grouping=False,
                serial=self.serial,
                overlap=self.overlap,
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
            self.loaded_at.clear()
            self.due_wait = max(0, slot.depart - math.ceil(self.clock))
        return result.status

    def motor(self, op, args):
        self.pending = (op, self.clock, args.get("target"))
        if self.active and self.active.carrier and op not in ("look", "wait"):
            self.carriers[self.active.carrier] = None
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

    def visit_duration(self, key):
        node = self.graph.nodes[key]
        return self.graph.distance(self.region, node.origin) + self.graph.work_duration(
            node
        )

    def wait(self, seconds, windows):
        events = [w["latest_in_s"] - self.visit_duration(w["check"]) for w in windows]
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
        options = self.methods.get(frozenset(self.active.members))
        if options and next_primitive not in options:
            next_primitive = None
        if next_primitive:
            self.log("macro_step", skill=skill, index=cursor, primitive=next_primitive)
        context = {
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
        if options:
            context["primitives"] = list(options)
            context["repaired_motor_options"] = list(options)
        missing = [
            name
            for name, region in self.carriers.items()
            if region is None and name != self.active.carrier
        ]
        if missing:
            context["unlocated_carriers"] = missing
        return context

    def ground(self, nodes, skill, windows):
        context = self.context(nodes, skill, windows)
        work = " ".join(n.work for n in nodes)
        for _ in range(2):
            answer = self.ask("fast", GROUND, DECISION, context, history=work)
            options = context.get("repaired_motor_options")
            if (
                options
                and answer["decision"] == "act"
                and answer["primitive"] not in options
            ):
                raise ValueError(
                    "Proposed motor action is outside the selected work's repaired method"
                )
            if answer["decision"] != "act" or not answer["verified"]:
                return answer
            self.log(
                "grounding_conflict",
                revision=self.camera.revision,
                primitive=answer["primitive"],
                claimed=[item["id"] for item in answer["verified"]],
            )
            context = {
                **context,
                "response_conflict": (
                    "Your response both requested an action and marked work verified. "
                    "Reassess the same current images. Use done only with visible evidence "
                    "for each selected assertion. An action requires an empty verified list. "
                    "If the required state cannot be distinguished, seek an informative "
                    "observation or report an anomaly."
                ),
            }
        raise ValueError(
            "Grounder twice requested action and verification on the same frame"
        )

    def record_changes(self, decision):
        job = self.active
        sighting = decision.get("carrier_observation")
        if sighting is not None:
            if (
                not job
                or not job.carrier
                or sighting["end"] not in ("source", "destination")
            ):
                raise ValueError(
                    "A carrier observation must refer to an endpoint of the assigned trip"
                )
            aboard = sighting["aboard"]
            if (
                sighting["view"] not in self.camera.frames
                or not sighting["evidence"].strip()
                or not isinstance(aboard, list)
                or len(set(aboard)) != len(aboard)
                or not set(aboard).issubset(job.members)
            ):
                raise ValueError(
                    "A carrier observation needs a current camera, visible evidence and unique member ids"
                )
        changes = self.memory.observe(decision["changes"], self.camera)
        if job:
            for fact in decision["changes"][:12]:
                name = fact["subject"].strip()
                location = fact["value"].strip()
                if (
                    name != job.carrier
                    and name in self.carriers
                    and self.carriers[name] is None
                    and fact["relation"].strip() == "location"
                    and fact["certainty"] == "seen"
                    and location in (job.origin, job.destination)
                ):
                    self.carriers[name] = location
                    self.log(
                        "carrier_located",
                        carrier=name,
                        region=location,
                        revision=self.camera.revision,
                        view=fact["view"],
                    )
        if sighting is not None:
            end = sighting["end"]
            region = job.origin if end == "source" else job.destination
            self.carriers[job.carrier] = region
            changes += self.memory.observe(
                [
                    {
                        "subject": job.carrier,
                        "relation": "location",
                        "value": region,
                        "certainty": "seen",
                        "view": sighting["view"],
                    }
                ],
                self.camera,
            )
            if set(aboard) == set(job.members):
                self.loaded_at[end] = (
                    self.camera.revision,
                    len(self.operations.get(job.members, ())),
                )
            elif end == "source":
                self.loaded_at.clear()
            self.log(
                "carrier_observed",
                carrier=job.carrier,
                region=region,
                revision=self.camera.revision,
                view=sighting["view"],
                aboard=aboard,
                evidence=sighting["evidence"],
            )
        if changes:
            self.log("delta", facts=changes)
        return changes

    def complete(self, decision, skill):
        job = self.active
        evidence = {row["id"]: row["evidence"].strip() for row in decision["verified"]}
        if set(evidence) != set(job.members) or not all(evidence.values()):
            raise ValueError(
                "Each dispatched obligation needs its own current visual evidence"
            )
        self.record_changes(decision)
        self.completed.update(dict.fromkeys(job.members, self.clock))
        self.methods = {
            group: value
            for group, value in self.methods.items()
            if group.isdisjoint(job.members)
        }
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
        cacheable = job.members not in self.uncacheable
        ops = self.operations.pop(job.members, [])
        if job.carrier:
            region = self.carriers.setdefault(job.carrier, None)
            start_rev, start_op = self.loaded_at.get("source", (-1, 0))
            end_rev, end_op = self.loaded_at.get("destination", (-1, 0))
            transit = any(
                op == "navigate_to" or op.startswith("place_")
                for op in ops[start_op:end_op]
            )
            shared_trip = 0 <= start_rev < end_rev and transit
            self.log(
                "carrier_trip",
                carrier=job.carrier,
                members=list(job.members),
                region=region,
                loaded_at_revisions={
                    end: row[0] for end, row in self.loaded_at.items()
                },
                visually_supported=shared_trip,
            )
            cacheable = cacheable and shared_trip
        if cacheable:
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
        early = max(
            [0] + [w["earliest_in_s"] for w in windows if w["check"] in job.members]
        )
        if decision["decision"] == "anomaly":
            self.record_changes(decision)
            raise ValueError(evidence[:600])
        if decision["decision"] not in ("act", "done"):
            raise ValueError("Unknown grounding decision")
        if early > 0 and (
            decision["decision"] == "done"
            or decision["primitive"] not in ("look", "navigate_to")
        ):
            return self.wait(early, windows)
        if decision["decision"] == "done":
            self.complete(decision, skill)
            return None
        op, args = self.arguments(decision)
        changes = self.record_changes(decision)
        if self.memory.repeated_hand_request(op, job.members):
            # A revised method may retry even on this same camera frame.
            self.memory.recent.append({"hand_visual_review": self.camera.revision})
            self.log(
                "hand_visual_review",
                op=op,
                selected=list(job.members),
                revision=self.camera.revision,
            )
            raise ValueError(
                f"Three preceding {op} requests on this work left similar RGB in all four "
                "views and at their selected patches. Gripper occupancy is unmeasured. "
                "The fast model requests the same hand command again."
            )
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
            reserve = self.motor_seconds.get(
                op,
                max(
                    1,
                    min(n.seconds for n in (self.graph.nodes[k] for k in job.members)),
                ),
            )
            remaining = windows[0]["latest_in_s"] - self.visit_duration(
                windows[0]["check"]
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
                    <= self.visit_duration(w["check"]) + self.return_reserve
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
                self.active = Job(
                    (key,),
                    node.origin,
                    node.destination,
                    self.graph.work_duration(node),
                )
                self.due_wait = 0
            if self.active is None:
                status = self.choose()
                if self.active is None:
                    reason = "No dispatchable schedule: " + status
                    keys = {w["check"] for w in windows} or (
                        self.graph.nodes.keys() - self.completed.keys()
                    )
                    if status == "infeasible":
                        conflict = self.graph.conflicting_window(
                            self.completed, self.clock
                        )
                        if conflict:
                            key, reason = conflict
                            keys = {key}
                    if self.repair(reason, keys):
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
            try:
                decision = self.ground(nodes, skill, windows)
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
