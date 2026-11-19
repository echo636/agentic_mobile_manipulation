"""A bounded view of observations. Beliefs come from visible evidence, never a scene graph."""

from collections import OrderedDict, deque
from dataclasses import dataclass
import math
import base64


@dataclass
class Camera:
    revision: int
    frames: dict
    clock: float | None
    hand: str
    odometer_m: float | None = None

    @classmethod
    def read(cls, result):
        import json
        from ..evidence_server import attachments, planner_parts, public_hand

        stripped, _ = planner_parts(result)
        body = json.loads(stripped.content[0].text)
        if "observation" not in body:
            raise ValueError("Current observation unavailable")
        obs, frames, _, _ = attachments(body, stripped.content)
        clock = (body.get("robot_time") or {}).get("total_s")
        if clock is not None and (
            type(clock) not in (float, int) or not math.isfinite(clock) or clock < 0
        ):
            raise ValueError("Invalid public robot clock")
        metres = (body.get("robot_time") or {}).get("odometer_m")
        if metres is not None and (
            type(metres) not in (int, float) or not math.isfinite(metres) or metres < 0
        ):
            raise ValueError("Invalid public odometer reading")
        return cls(
            obs["revision"],
            {f["view"]: f for f in frames},
            clock,
            public_hand(body),
            metres,
        )

    def image(self, view):
        f = self.frames[view]
        return {
            "type": "input_image",
            "image_url": "data:" + f["mime_type"] + ";base64," + f["data"],
            "detail": "original",
        }

    def parts(self):
        result = []
        for view in self.frames:
            result.extend(
                (
                    {"type": "input_text", "text": "Current RGB " + view},
                    self.image(view),
                )
            )
        return result


class DeltaMemory:
    def __init__(self, keyframe_limit=8):
        self.facts = OrderedDict()
        self.keyframes = OrderedDict()
        self.limit = keyframe_limit
        self.recent = deque(maxlen=5)
        self.macros = {}
        self.travel = {}
        self.metres = {}
        from .action_evidence import EvidenceMemory

        self.selections = EvidenceMemory()
        self.panel_parts = []
        self.access_cycle = False

    def motor_readback(self, op, target, before, after):
        """Compare the selected visible pixels even if the motor returned a private error."""
        from .action_evidence import EvidenceMemory

        self.panel_parts = []
        self.access_cycle = False
        if after.revision != before.revision + 1:
            self.selections = EvidenceMemory()
            return
        previous = {f["image_ref"]: f for f in before.frames.values()}
        current = {f["image_ref"]: f for f in after.frames.values()}
        self.selections.action(
            op, target, previous, list(current.values()), before.revision, after.hand
        )
        info, data = self.selections.panel(current)
        if not data:
            return
        self.access_cycle = bool(info.get("repeated_access"))
        self.panel_parts = [
            {
                "type": "input_text",
                "text": "Your earlier visual selections: source view, selected patch and current pixels at the same coordinates. Objects and camera may move. Current full views alone supply action coordinates.",
            },
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,"
                + base64.b64encode(data).decode("ascii"),
                "detail": "original",
            },
        ]

    def observe(self, changes, camera):
        changed = []
        for entry in changes[:12]:
            key = (entry["subject"].strip()[:120], entry["relation"].strip()[:80])
            value = entry["value"].strip()[:220]
            view = entry["view"]
            if (
                not all(key)
                or not value
                or view not in camera.frames
                or entry["certainty"] not in ("seen", "uncertain")
            ):
                raise ValueError(
                    "A belief needs a current view and an epistemic status"
                )
            prior = self.facts.get(key)
            if prior and (prior["value"], prior["certainty"]) == (
                value,
                entry["certainty"],
            ):
                continue
            fact = dict(
                subject=key[0],
                relation=key[1],
                value=value,
                certainty=entry["certainty"],
                revision=camera.revision,
                view=view,
                image_ref=camera.frames[view]["image_ref"],
            )
            self.facts[key] = fact
            self.facts.move_to_end(key)
            changed.append(fact)
            self.remember_frame(key, camera, view)
        return changed

    def remember_frame(self, key, camera, view):
        self.keyframes[key] = (camera.revision, view, camera.image(view))
        self.keyframes.move_to_end(key)
        while len(self.keyframes) > self.limit:
            self.keyframes.popitem(last=False)

    def context(self, work, *, limit=12):
        words = set(work.lower().split())
        facts = list(reversed(self.facts.values()))
        facts.sort(
            key=lambda row: bool(
                words & set((row["subject"] + " " + row["value"]).lower().split())
            ),
            reverse=True,
        )
        return {"beliefs": facts[:limit], "recent_events": list(self.recent)}

    def reference(self, work, limit=2, *, before=None):
        words = set(work.lower().split())
        frames = list(reversed(self.keyframes.items()))
        frames.sort(
            key=lambda row: bool(words & set(" ".join(row[0]).lower().split())),
            reverse=True,
        )
        seen = set()
        parts = []
        for _, (revision, view, image) in frames:
            if (before is not None and revision >= before) or (revision, view) in seen:
                continue
            seen.add((revision, view))
            parts.extend(
                (
                    {
                        "type": "input_text",
                        "text": f"Historical reference only: revision {revision}, {view}",
                    },
                    image,
                )
            )
            if len(seen) >= limit:
                break
        return parts

    def duration(self, zone1, zone2, elapsed, metres=None):
        if zone1 != zone2 and elapsed > 0:
            if metres is not None:
                self.metres[zone1, zone2] = metres
            old = self.travel.get((zone1, zone2))
            self.travel[zone1, zone2] = math.ceil(
                elapsed if old is None else (old + elapsed) / 2
            )

    def learn_macro(self, skill, operations):
        if 0 < len(operations) <= 16:
            # Only the semantic primitive sequence is reusable. Bind every pixel to new RGB.
            self.macros[skill] = tuple(operations)
