"""General spatial and object state external memory compression from public receipts.

Strictly Harness-side (CPU-only, zero simulator or private scene imports, zero
evaluator access, zero domain-specific rules). Automatically maintains a compact
structured ledger solely from public tool calls (`initialize`, `look`, `act`,
`task_notes`, `remember`) and their public JSON receipts:

  (a) `visited_locations` and measured point-to-point travel cost matrix
      (for both model reference and zero-config consumption by task graph planners);
  (b) `hand_holding` (single-hand occupancy and held item tracking reconciled
      against public gripper proprioception and postcondition release stages);
  (c) `object_states` and `container_states` (observed item placements/transforms,
      enclosure open/closed states, verified items placed inside closed enclosures,
      active public process elapsed time, and public tool state receipts).

Also provides:
  - `render_memory_summary()`: bounded ~100-150 token text digest;
  - `prune_image_window()`: sliding-window trimmer for historical panorama images;
  - `build_handoff_context()`: lossless continuation payload for rolling sessions.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
from typing import Any

from ..records import write_json

HAND_RELEASING_PRIMITIVES = frozenset({
    'place_inside', 'place_on_top', 'place_next_to', 'place_under',
    'attach', 'hang', 'release',
})
PLACEMENT_STATUS_MAP = {
    'place_inside': 'placed_inside',
    'place_on_top': 'placed_on_top',
    'place_next_to': 'placed_next_to',
    'place_under': 'placed_under',
    'attach': 'attached',
    'hang': 'hung',
    'release': 'released',
}
MACRO_TRAVEL_METERS = 1.5
MACRO_TRAVEL_SECONDS = 3.5
SAME_VIEW_CONTAINER_RADIUS = 0.25
IMAGE_BLOCK_TYPES = frozenset({'input_image', 'image', 'image_url'})
OMITTED_IMAGE_PLACEHOLDER = '[omitted historical RGB frame; state retained in StateMemory]'


def _clean_short(value: Any, max_len: int = 48) -> str | None:
    if not isinstance(value, str):
        return None
    text = ' '.join(value.strip().split())
    if not text:
        return None
    return text[:max_len]


def _view_from_ref(image_ref: Any) -> str | None:
    if not isinstance(image_ref, str) or not image_ref:
        return None
    for view in ('front', 'back', 'left', 'right'):
        if image_ref.endswith('-' + view) or image_ref.endswith('_' + view) or f'-{view}-' in image_ref:
            return view
    return None


def _point_pair(target: Any) -> list[float] | None:
    if not isinstance(target, dict):
        return None
    pt = target.get('point')
    if isinstance(pt, (list, tuple)) and len(pt) == 2:
        try:
            x, y = float(pt[0]), float(pt[1])
            if math.isfinite(x) and math.isfinite(y):
                return [round(x, 3), round(y, 3)]
        except (TypeError, ValueError):
            return None
    return None


def _extract_intent_phrase(intent: Any, primitive: str | None = None) -> str | None:
    text = _clean_short(intent, max_len=80)
    if not text:
        return None
    lowered = text.rstrip('.!;:')
    if primitive == 'navigate_to':
        m = re.search(r'\b(?:navigate|move|walk|go|travel|return|head)\s+(?:back\s+)?(?:to|into|toward|towards|near)\s+(?:the\s+)?([A-Za-z0-9_ -]{2,36})', lowered, re.IGNORECASE)
        if m:
            return _clean_short(m.group(1), 36)
    elif primitive in {'open', 'close', 'grasp', 'place_inside', 'place_on_top'}:
        m = re.search(r'\b(?:open|close|grasp|pick up|take|place\s+.+?\s+(?:inside|into|in|on|onto))\s+(?:the\s+)?([A-Za-z0-9_ -]{2,36})', lowered, re.IGNORECASE)
        if m:
            return _clean_short(m.group(1), 36)
    return _clean_short(lowered, 40)


def _norm_key(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')
    return cleaned if len(cleaned) >= 3 else None


def _is_image_part(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get('type')
    if isinstance(kind, str) and kind.lower() in IMAGE_BLOCK_TYPES:
        return True
    source = node.get('source')
    if isinstance(source, dict) and str(source.get('media_type', '')).startswith('image/'):
        return True
    mime = node.get('mimeType') or node.get('mime_type')
    return isinstance(mime, str) and mime.startswith('image/') and isinstance(node.get('data'), str)



def _placed_by_robot(info):
    """Memory only counts items the robot itself placed inside; it never observes other contents."""
    n = len(info.get('items_inside') or [])
    return f"robot placed {n} item(s) inside; other contents not tracked"

class StateMemory:
    """Automatic public-receipt spatial and object state external memory."""

    def __init__(self, *, output_dir: Path | str | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.steps_observed: int = 0
        self.last_revision: int = 0
        self.robot_clock_s: float = 0.0
        self._loc_seq: int = 0
        self._item_seq: int = 0
        self._container_seq: int = 0
        self._surface_seq: int = 0

        self.current_location: str = 'loc_0'
        self.visited_locations: dict[str, dict[str, Any]] = {
            'loc_0': {
                'id': 'loc_0',
                'first_step': 0,
                'last_step': 0,
                'visits': 1,
                'last_revision': 0,
                'latest_image_refs': [],
                'hint': None,
            }
        }
        self.travel_records: dict[str, dict[str, Any]] = {}
        self.hand_holding: dict[str, Any] = {
            'status': 'empty',
            'item': None,
            'since_step': 0,
            'from_location': None,
            'hint': None,
        }
        self.object_states: dict[str, dict[str, Any]] = {}
        self.container_states: dict[str, dict[str, Any]] = {}
        self.process_state: dict[str, Any] = {
            'any_running': False,
            'active_since_clock_s': None,
            'elapsed_active_s': None,
            'active_location': None,
            'completed_transitions': 0,
            'recent_messages': [],
        }
        self.tool_state: dict[str, Any] = {
            'level': None,
            'state': None,
            'violation_occurred': False,
            'last_message': None,
            'last_step': None,
            'location': None,
        }
        self.unresolved_notes_goals: list[str] = []
        self.failure_streak: dict[str, Any] = {
            'count': 0,
            'primitives': [],
            'codes': [],
            'locations': [],
            'same_primitive_in_place': 0,
        }
        self._ingested_call_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Serialization & Persistence
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            'steps_observed': self.steps_observed,
            'last_revision': self.last_revision,
            'robot_clock_s': round(self.robot_clock_s, 2),
            'counters': {
                'loc': self._loc_seq,
                'item': self._item_seq,
                'container': self._container_seq,
                'surface': self._surface_seq,
            },
            'current_location': self.current_location,
            'visited_locations': copy.deepcopy(self.visited_locations),
            'travel_records': copy.deepcopy(self.travel_records),
            'travel_cost_matrix': self.travel_cost_matrix(),
            'hand_holding': copy.deepcopy(self.hand_holding),
            'object_states': copy.deepcopy(self.object_states),
            'container_states': copy.deepcopy(self.container_states),
            'process_state': copy.deepcopy(self.process_state),
            'tool_state': copy.deepcopy(self.tool_state),
            'unresolved_notes_goals': list(self.unresolved_notes_goals),
            'failure_streak': copy.deepcopy(self.failure_streak),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, output_dir: Path | str | None = None) -> StateMemory:
        mem = cls(output_dir=output_dir)
        if not isinstance(data, dict):
            return mem
        mem.steps_observed = int(data.get('steps_observed') or 0)
        mem.last_revision = int(data.get('last_revision') or 0)
        mem.robot_clock_s = float(data.get('robot_clock_s') or 0.0)
        counters = data.get('counters') or {}
        mem._loc_seq = int(counters.get('loc') or 0)
        mem._item_seq = int(counters.get('item') or 0)
        mem._container_seq = int(counters.get('container') or 0)
        mem._surface_seq = int(counters.get('surface') or 0)
        if isinstance(data.get('current_location'), str) and data['current_location']:
            mem.current_location = data['current_location']
        if isinstance(data.get('visited_locations'), dict) and data['visited_locations']:
            mem.visited_locations = copy.deepcopy(data['visited_locations'])
        if isinstance(data.get('travel_records'), dict):
            mem.travel_records = copy.deepcopy(data['travel_records'])
        if isinstance(data.get('hand_holding'), dict):
            mem.hand_holding.update(copy.deepcopy(data['hand_holding']))
        if isinstance(data.get('object_states'), dict):
            mem.object_states = copy.deepcopy(data['object_states'])
        if isinstance(data.get('container_states'), dict):
            mem.container_states = copy.deepcopy(data['container_states'])
        if isinstance(data.get('process_state'), dict):
            mem.process_state.update(copy.deepcopy(data['process_state']))
        if isinstance(data.get('tool_state'), dict):
            mem.tool_state.update(copy.deepcopy(data['tool_state']))
        if isinstance(data.get('unresolved_notes_goals'), list):
            mem.unresolved_notes_goals = [str(x) for x in data['unresolved_notes_goals']]
        if isinstance(data.get('failure_streak'), dict):
            mem.failure_streak.update(copy.deepcopy(data['failure_streak']))
        return mem

    def _persist(self) -> None:
        if self.output_dir is None:
            return
        try:
            write_json(self.output_dir / 'state_memory.json', self.to_dict())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Spatial Graph & Planner Export (Zero-Config Route B Synergy)
    # ------------------------------------------------------------------
    @staticmethod
    def _edge_key(u: str, v: str) -> str:
        return f'{u}->{v}'

    def _record_travel(self, src: str, dst: str, seconds: float, meters: float, step: int) -> None:
        if src == dst or not math.isfinite(seconds) or seconds < 0:
            return
        for a, b in ((src, dst), (dst, src)):
            key = self._edge_key(a, b)
            existing = self.travel_records.get(key)
            if existing is None:
                self.travel_records[key] = {
                    'from_location': a,
                    'to_location': b,
                    'seconds': round(seconds, 2),
                    'meters': round(max(0.0, meters), 2),
                    'min_seconds': round(seconds, 2),
                    'max_seconds': round(seconds, 2),
                    'samples': 1,
                    'last_step': step,
                }
            else:
                n = int(existing['samples']) + 1
                avg_s = (float(existing['seconds']) * (n - 1) + seconds) / n
                avg_m = (float(existing['meters']) * (n - 1) + max(0.0, meters)) / n
                existing.update(
                    seconds=round(avg_s, 2),
                    meters=round(avg_m, 2),
                    min_seconds=round(min(float(existing['min_seconds']), seconds), 2),
                    max_seconds=round(max(float(existing['max_seconds']), seconds), 2),
                    samples=n,
                    last_step=step,
                )

    def travel_edges(self) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        edges: list[dict[str, Any]] = []
        for rec in self.travel_records.values():
            u, v = rec['from_location'], rec['to_location']
            pair = (u, v) if u <= v else (v, u)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({
                'from_location': u,
                'to_location': v,
                'seconds': rec['seconds'],
                'meters': rec['meters'],
                'samples': rec['samples'],
            })
        return edges

    def travel_cost_matrix(self, default_unobserved_s: float | None = None) -> dict[str, dict[str, float]]:
        locs = list(self.visited_locations.keys())
        n = len(locs)
        idx = {loc: i for i, loc in enumerate(locs)}
        dist = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
        for rec in self.travel_records.values():
            u, v = rec['from_location'], rec['to_location']
            if u in idx and v in idx:
                s = float(rec['seconds'])
                i, j = idx[u], idx[v]
                if s < dist[i][j]:
                    dist[i][j] = s
        for k in range(n):
            for i in range(n):
                for j in range(n):
                    if dist[i][k] + dist[k][j] < dist[i][j] - 1e-9:
                        dist[i][j] = dist[i][k] + dist[k][j]
        matrix: dict[str, dict[str, float]] = {}
        for i, u in enumerate(locs):
            row: dict[str, float] = {}
            for j, v in enumerate(locs):
                if math.isfinite(dist[i][j]):
                    row[v] = round(dist[i][j], 2)
                elif default_unobserved_s is not None:
                    row[v] = round(float(default_unobserved_s), 2)
            matrix[u] = row
        return matrix

    def verified_closed_containers(self) -> dict[str, dict[str, Any]]:
        return {
            cid: copy.deepcopy(info)
            for cid, info in self.container_states.items()
            if info.get('state') == 'closed' and info.get('verified_closed') is True
        }

    def open_containers(self) -> dict[str, dict[str, Any]]:
        return {
            cid: copy.deepcopy(info)
            for cid, info in self.container_states.items()
            if info.get('state') == 'open'
        }

    def _regrasped_unverified_item(self) -> str | None:
        """Id to reuse when a grasp picks an item up again where an unverified release happened.

        Public evidence only: an earlier receipt reported a release whose placement was not
        verified at the robot's current memory location, and a grasp now completes at that same
        location. The most recent such item is assumed to be the one picked up again, so it keeps
        its id instead of leaving a stale unverified record behind.
        """
        candidates = [
            (int(info.get('last_step') or 0), oid)
            for oid, info in self.object_states.items()
            if info.get('status') == 'unverified_after_release' and info.get('area') == self.current_location
        ]
        return max(candidates)[1] if candidates else None

    def unverified_items(self) -> list[str]:
        return [
            oid for oid, info in self.object_states.items()
            if info.get('status') == 'unverified_after_release'
        ]

    def for_planner(self, *, default_unobserved_travel_s: float | None = None) -> dict[str, Any]:
        """Structured zero-assumption state export for task graph planners."""
        return {
            'current_location': self.current_location,
            'locations': list(self.visited_locations.keys()),
            'visited_locations': copy.deepcopy(self.visited_locations),
            'travel_cost': self.travel_cost_matrix(default_unobserved_s=default_unobserved_travel_s),
            'travel_edges': self.travel_edges(),
            'hand_holding': copy.deepcopy(self.hand_holding),
            'hand_occupied': self.hand_holding.get('status') == 'holding_item',
            'object_states': copy.deepcopy(self.object_states),
            'container_states': copy.deepcopy(self.container_states),
            'verified_closed_containers': self.verified_closed_containers(),
            'process_state': copy.deepcopy(self.process_state),
            'tool_state': copy.deepcopy(self.tool_state),
            'robot_clock_s': round(self.robot_clock_s, 2),
        }

    # ------------------------------------------------------------------
    # Public Receipt Observation
    # ------------------------------------------------------------------
    def _resolve_location_on_move(self, arguments: dict[str, Any], hint: str | None) -> str:
        explicit = (
            _clean_short(arguments.get('to_location'), 40)
            or _clean_short(arguments.get('location'), 40)
            or _clean_short(arguments.get('destination'), 40)
        )
        if explicit:
            return explicit
        norm_hint = _norm_key(hint)
        if norm_hint:
            for loc_id, info in self.visited_locations.items():
                if _norm_key(info.get('hint')) == norm_hint or _norm_key(loc_id) == norm_hint:
                    return loc_id
        self._loc_seq += 1
        return f'loc_{self._loc_seq}'

    # ------------------------------------------------------------------
    # Harness options --place-names / --place-return (2026-11-21, default off). Names come only from the model.
    # ------------------------------------------------------------------
    @staticmethod
    def place_key(name: Any) -> str | None:
        if not isinstance(name, str):
            return None
        words = name.strip().lower().split()
        while words and words[0] in ('a', 'an', 'the'):
            words = words[1:]
        return ' '.join(words) or None

    def named_places(self) -> dict[str, str]:
        return {info['name']: loc for loc, info in self.visited_locations.items() if info.get('name')}

    def location_for_name(self, name: Any) -> str | None:
        key = self.place_key(name)
        if key is None:
            return None
        for loc, info in self.visited_locations.items():
            if info.get('name') and self.place_key(info['name']) == key:
                return loc
        return None

    def name_location(self, loc_id: str, name: str) -> dict[str, Any]:
        """Bind `name` to `loc_id`; one name per location and one location per name (latest wins)."""
        entry = self.visited_locations.get(loc_id)
        if entry is None:
            return {'ignored': True, 'message': f'No location {loc_id} to name.'}
        clean = ' '.join(name.strip().split())
        note: dict[str, Any] = {'location': loc_id, 'name': clean}
        previous_owner = self.location_for_name(clean)
        if previous_owner is not None and previous_owner != loc_id:
            self.visited_locations[previous_owner].pop('name', None)
            note['message'] = f"name '{clean}' moved from {previous_owner} to {loc_id}"
        old = entry.get('name')
        if old and self.place_key(old) != self.place_key(clean):
            note['message'] = f"{loc_id} renamed '{old}' -> '{clean}'"
        entry['name'] = clean
        note.setdefault('message', f"{loc_id} is now named '{clean}'")
        self._persist()
        return note

    def resolve_place(self, place: Any) -> tuple[str, int] | None:
        """A model-given place (a name or a loc id) -> (loc id, observation revision of its latest visit)."""
        if not isinstance(place, str) or not place.strip():
            return None
        loc = self.location_for_name(place)
        if loc is None and place.strip() in self.visited_locations:
            loc = place.strip()
        if loc is None:
            return None
        revision = self.visited_locations[loc].get('last_revision')
        return (loc, int(revision)) if isinstance(revision, int) else None

    def _touch_location(self, loc_id: str, step: int, revision: int, image_refs: list[str], hint: str | None = None) -> None:
        entry = self.visited_locations.get(loc_id)
        if entry is None:
            self.visited_locations[loc_id] = {
                'id': loc_id,
                'first_step': step,
                'last_step': step,
                'visits': 1,
                'last_revision': revision,
                'latest_image_refs': list(image_refs[:4]),
                'hint': hint,
            }
        else:
            if loc_id != self.current_location:
                entry['visits'] = int(entry.get('visits', 1)) + 1
            entry['last_step'] = step
            entry['last_revision'] = revision
            if image_refs:
                entry['latest_image_refs'] = list(image_refs[:4])
            if hint and not entry.get('hint'):
                entry['hint'] = hint
        self.current_location = loc_id

    def _match_or_create_container(
        self,
        arguments: dict[str, Any],
        *,
        view: str | None,
        point: list[float] | None,
        hint: str | None,
        expected_current_state: str | None,
        create_if_missing: bool = True,
    ) -> str | None:
        explicit = _clean_short(arguments.get('container_label') or arguments.get('target_label'), 40)
        if explicit:
            if explicit not in self.container_states and create_if_missing:
                self.container_states[explicit] = {
                    'id': explicit,
                    'location': self.current_location,
                    'state': 'unknown',
                    'verified_closed': False,
                    'items_inside': [],
                    'partial_opening': False,
                    'needs_reinspection': False,
                    'opened_at_step': None,
                    'closed_at_step': None,
                    'last_step': self.steps_observed,
                    'last_revision': self.last_revision,
                    'view': view,
                    'point': point,
                    'hint': hint,
                }
            return explicit if explicit in self.container_states else None

        candidates_here = [
            info for info in self.container_states.values()
            if info.get('location') == self.current_location
        ]
        norm_hint = _norm_key(hint)
        if norm_hint:
            for info in reversed(candidates_here):
                if _norm_key(info.get('hint')) == norm_hint:
                    return info['id']

        if view and point:
            best_id = None
            best_dist = SAME_VIEW_CONTAINER_RADIUS
            for info in candidates_here:
                if info.get('view') == view and isinstance(info.get('point'), list) and len(info['point']) == 2:
                    if expected_current_state and info.get('state') not in {expected_current_state, 'unknown'}:
                        continue
                    d = math.dist(point, info['point'])
                    if d <= best_dist:
                        best_dist = d
                        best_id = info['id']
            if best_id is not None:
                return best_id

        if expected_current_state:
            matching_state = [
                info for info in candidates_here
                if info.get('state') == expected_current_state
            ]
            if len(matching_state) == 1:
                return matching_state[0]['id']
            if matching_state and expected_current_state == 'open':
                matching_state.sort(key=lambda r: int(r.get('last_step') or 0))
                return matching_state[-1]['id']

        if not create_if_missing:
            return None
        self._container_seq += 1
        cid = f'container_{self._container_seq}'
        self.container_states[cid] = {
            'id': cid,
            'location': self.current_location,
            'state': 'unknown',
            'verified_closed': False,
            'items_inside': [],
            'partial_opening': False,
            'needs_reinspection': False,
            'opened_at_step': None,
            'closed_at_step': None,
            'last_step': self.steps_observed,
            'last_revision': self.last_revision,
            'view': view,
            'point': point,
            'hint': hint,
        }
        return cid

    def _observe_notes(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        if not result.get('ok'):
            return
        notes_obj = result.get('notes') if isinstance(result.get('notes'), dict) else None
        raw_content = (notes_obj or {}).get('content') or arguments.get('content')
        if not isinstance(raw_content, str) or not raw_content.strip():
            return
        try:
            parsed = json.loads(raw_content)
        except ValueError:
            return
        if not isinstance(parsed, dict) or not isinstance(parsed.get('goals'), list):
            return
        done_statuses = {'done', 'completed', 'achieved', 'verified', 'satisfied'}
        unresolved: list[str] = []
        for g in parsed['goals']:
            if not isinstance(g, dict):
                continue
            gid = _clean_short(str(g.get('id') or g.get('description') or ''), 48)
            status = str(g.get('status') or 'pending').strip().lower()
            if gid and status not in done_statuses:
                unresolved.append(gid)
        self.unresolved_notes_goals = unresolved[:20]

    def observe(self, name: str, arguments: dict[str, Any] | None, result: dict[str, Any]) -> None:
        """Update structured external memory from one public tool call and receipt."""
        if not isinstance(result, dict):
            return
        args = arguments if isinstance(arguments, dict) else {}

        obs = result.get('observation') if isinstance(result.get('observation'), dict) else {}
        if isinstance(obs.get('revision'), int):
            self.last_revision = obs['revision']
        image_refs = [
            str(img['image_ref'])
            for img in (obs.get('images') or [])
            if isinstance(img, dict) and isinstance(img.get('image_ref'), str)
        ]

        budget = result.get('execution_budget') if isinstance(result.get('execution_budget'), dict) else {}
        if isinstance(budget.get('steps_used'), int):
            self.steps_observed = max(self.steps_observed, budget['steps_used'])

        rt = result.get('robot_time') if isinstance(result.get('robot_time'), dict) else {}
        # Only the public robot clock is read (v10): simulator physics time is not an interface a real robot has.
        if isinstance(rt.get('total_s'), (int, float)) and math.isfinite(float(rt['total_s'])):
            self.robot_clock_s = max(self.robot_clock_s, float(rt['total_s']))

        if name == 'initialize':
            init_loc = _clean_short(args.get('location'), 40) or self.current_location
            self._touch_location(init_loc, self.steps_observed, self.last_revision, image_refs)
            hand = (result.get('robot') or {}).get('hand') if isinstance(result.get('robot'), dict) else None
            if hand in {'empty', 'holding_item'}:
                self.hand_holding['status'] = hand
            self._persist()
            return

        if name in {'task_notes', 'remember'}:
            self._observe_notes(name, args, result)
            self._persist()
            return

        if name not in {'act', 'look'}:
            return

        error = result.get('error') if isinstance(result.get('error'), dict) else {}
        step_used = error.get('action_step_used')
        if step_used is False:
            # Validation/stale-ref refusal consumed no action step and did not change physics.
            hand = (result.get('robot') or {}).get('hand') if isinstance(result.get('robot'), dict) else None
            if hand in {'empty', 'holding_item'}:
                self._reconcile_hand_proprioception(hand, error)
            self._persist()
            return

        if not isinstance(budget.get('steps_used'), int):
            self.steps_observed += 1
        step = self.steps_observed

        primitive = str(args.get('primitive') or name)
        target = args.get('target') if isinstance(args.get('target'), dict) else None
        view = _view_from_ref((target or {}).get('image_ref'))
        point = _point_pair(target)
        hint = _extract_intent_phrase(args.get('intent'), primitive)

        this_call = rt.get('this_call') if isinstance(rt.get('this_call'), dict) else {}
        travel_m = float(this_call.get('travel_m') or 0.0)
        travel_s = float(this_call.get('travel_s') or 0.0)

        ok = bool(result.get('ok'))
        effect = result.get('effect') if (ok and isinstance(result.get('effect'), dict)) else {}
        completed = ok and (name == 'look' or effect.get('status') == 'completed')

        # 1. Location & measured travel matrix update
        prev_loc = self.current_location
        if primitive == 'navigate_to' and completed:
            nav_status = effect.get('navigation_status')
            has_clock = bool(this_call)
            moved_macro = (
                (has_clock and (travel_m >= MACRO_TRAVEL_METERS or travel_s >= MACRO_TRAVEL_SECONDS))
                or (not has_clock and nav_status != 'already_at_closest_approach')
                or any(k in args for k in ('location', 'to_location', 'destination'))
            )
            if moved_macro and nav_status != 'already_at_closest_approach':
                dst_loc = self._resolve_location_on_move(args, hint)
                measured_s = travel_s if has_clock else 0.0
                if dst_loc != prev_loc and has_clock:
                    self._record_travel(prev_loc, dst_loc, measured_s, travel_m, step)
                self._touch_location(dst_loc, step, self.last_revision, image_refs, hint=hint)
            else:
                self._touch_location(prev_loc, step, self.last_revision, image_refs)
        else:
            explicit_loc = _clean_short(args.get('location'), 40)
            if explicit_loc and explicit_loc != prev_loc:
                if travel_s > 0 or travel_m > 0:
                    self._record_travel(prev_loc, explicit_loc, travel_s, travel_m, step)
                self._touch_location(explicit_loc, step, self.last_revision, image_refs)
            else:
                self._touch_location(prev_loc, step, self.last_revision, image_refs)

        # 2. Failure streak & error-derived factual updates
        if not completed:
            code = str(error.get('code') or ('incomplete_navigation' if ok else 'unknown_error'))
            self.failure_streak['count'] = int(self.failure_streak['count']) + 1
            self.failure_streak['primitives'] = [*self.failure_streak['primitives'][-9:], primitive]
            self.failure_streak['codes'] = [*self.failure_streak['codes'][-9:], code]
            self.failure_streak['locations'] = [*self.failure_streak['locations'][-9:], self.current_location]
            same_count = 0
            for p, loc in zip(reversed(self.failure_streak['primitives']), reversed(self.failure_streak['locations'])):
                if p == primitive and loc == self.current_location:
                    same_count += 1
                else:
                    break
            self.failure_streak['same_primitive_in_place'] = same_count

            if code == 'container_closed':
                cid = self._match_or_create_container(
                    args, view=view, point=point, hint=hint, expected_current_state=None, create_if_missing=True
                )
                if cid:
                    cinfo = self.container_states[cid]
                    cinfo.update(state='closed', last_step=step, last_revision=self.last_revision)
            if error.get('stage') == 'articulation_contents_unconfirmed':
                cid = self._match_or_create_container(
                    args, view=view, point=point, hint=hint, expected_current_state=None, create_if_missing=False
                )
                if cid:
                    self.container_states[cid].update(verified_closed=False, needs_reinspection=True, last_step=step)
        else:
            if name == 'act' and primitive != 'wait':
                self.failure_streak = {
                    'count': 0,
                    'primitives': [],
                    'codes': [],
                    'locations': [],
                    'same_primitive_in_place': 0,
                }

        # 3. Container, Object & Hand transitions on completed `act`
        if name == 'act' and completed:
            self._apply_completed_act(primitive, args, effect, step=step, view=view, point=point, hint=hint)

        # 4. Public process & tool state receipts (present on any completed/reported act)
        if isinstance(effect.get('process'), dict):
            self._update_process_state(effect['process'], step=step)
        tool_rcpt = effect.get('tool_state') or effect.get('tool_cleanliness')
        if isinstance(tool_rcpt, dict):
            self._update_tool_state(tool_rcpt, step=step)

        # 5. Reconcile against public gripper proprioception & postcondition release errors
        hand_prop = (result.get('robot') or {}).get('hand') if isinstance(result.get('robot'), dict) else None
        self._reconcile_hand_proprioception(hand_prop, error, step=step)

        self._persist()

    def _apply_completed_act(
        self,
        primitive: str,
        args: dict[str, Any],
        effect: dict[str, Any],
        *,
        step: int,
        view: str | None,
        point: list[float] | None,
        hint: str | None,
    ) -> None:
        if primitive == 'open':
            cid = self._match_or_create_container(
                args, view=view, point=point, hint=hint, expected_current_state='closed', create_if_missing=True
            )
            if cid:
                partial = bool((effect.get('opening') or {}).get('other_panels_not_operated'))
                cinfo = self.container_states[cid]
                cinfo.update(
                    state='open',
                    verified_closed=False,
                    partial_opening=partial,
                    needs_reinspection=False,
                    opened_at_step=step,
                    last_step=step,
                    last_revision=self.last_revision,
                    view=view or cinfo.get('view'),
                    point=point or cinfo.get('point'),
                )
                if hint and not cinfo.get('hint'):
                    cinfo['hint'] = hint

        elif primitive == 'close':
            cid = self._match_or_create_container(
                args, view=view, point=point, hint=hint, expected_current_state='open', create_if_missing=True
            )
            if cid:
                cinfo = self.container_states[cid]
                cinfo.update(
                    state='closed',
                    verified_closed=True,
                    partial_opening=False,
                    needs_reinspection=False,
                    closed_at_step=step,
                    last_step=step,
                    last_revision=self.last_revision,
                    view=view or cinfo.get('view'),
                    point=point or cinfo.get('point'),
                )
                if hint and not cinfo.get('hint'):
                    cinfo['hint'] = hint

        elif primitive == 'grasp':
            explicit_item = _clean_short(
                args.get('item_label') or args.get('object_label') or args.get('target_label'), 40
            )
            if explicit_item:
                item_id = explicit_item
            else:
                item_id = self._regrasped_unverified_item()
                if item_id is None:
                    self._item_seq += 1
                    item_id = f'item_{self._item_seq}'

            # If grasping from a currently open container in the same view/proximity, remove one matching item if tracked
            src_cid = self._match_or_create_container(
                args, view=view, point=point, hint=None, expected_current_state='open', create_if_missing=False
            )
            if src_cid and self.container_states[src_cid]['items_inside']:
                inside_list = self.container_states[src_cid]['items_inside']
                if item_id in inside_list:
                    inside_list.remove(item_id)

            self.hand_holding = {
                'status': 'holding_item',
                'item': item_id,
                'since_step': step,
                'from_location': self.current_location,
                'hint': hint,
            }
            prev_obj = self.object_states.get(item_id, {})
            self.object_states[item_id] = {
                **prev_obj,
                'id': item_id,
                'location': 'in_hand',
                'area': self.current_location,
                'status': 'held',
                'verified_placement': False,
                'container_id': None,
                'last_step': step,
                'last_revision': self.last_revision,
                'hint': hint or prev_obj.get('hint'),
            }

        elif primitive in HAND_RELEASING_PRIMITIVES:
            held_id = self.hand_holding.get('item')
            if not held_id:
                self._item_seq += 1
                held_id = f'item_{self._item_seq}'
            held_hint = self.hand_holding.get('hint')
            status_label = PLACEMENT_STATUS_MAP.get(primitive, 'placed')
            dest_str = f'at:{self.current_location}'
            cid_used = None

            if primitive == 'place_inside':
                cid_used = self._match_or_create_container(
                    args, view=view, point=point, hint=hint, expected_current_state='open', create_if_missing=True
                )
                if cid_used:
                    cinfo = self.container_states[cid_used]
                    if cinfo.get('state') == 'unknown':
                        cinfo['state'] = 'open'
                    if held_id not in cinfo['items_inside']:
                        cinfo['items_inside'].append(held_id)
                    cinfo.update(
                        verified_closed=False,
                        last_step=step,
                        last_revision=self.last_revision,
                    )
                    dest_str = f'inside:{cid_used}'
            elif primitive == 'place_on_top':
                surf_label = _clean_short(args.get('surface_label') or args.get('target_label'), 40)
                if not surf_label:
                    self._surface_seq += 1
                    surf_label = f'surface@{self.current_location}'
                dest_str = f'on_top:{surf_label}'
            else:
                dest_str = f'{status_label}:{self.current_location}'

            prev_obj = self.object_states.get(held_id, {})
            self.object_states[held_id] = {
                **prev_obj,
                'id': held_id,
                'location': dest_str,
                'area': self.current_location,
                'status': status_label,
                'verified_placement': primitive != 'release',
                'container_id': cid_used,
                'last_step': step,
                'last_revision': self.last_revision,
                'hint': held_hint or prev_obj.get('hint') or hint,
            }
            self.hand_holding = {
                'status': 'empty',
                'item': None,
                'since_step': step,
                'from_location': None,
                'hint': None,
            }

        elif primitive == 'cut':
            cut_info = effect.get('cut') if isinstance(effect.get('cut'), dict) else {}
            target_id = _clean_short(args.get('target_label'), 40) or f'workpiece@{self.current_location}#{step}'
            self.object_states[target_id] = {
                'id': target_id,
                'location': f'at:{self.current_location}',
                'area': self.current_location,
                'status': str(cut_info.get('result') or 'cut_completed'),
                'verified_placement': True,
                'pieces_created': cut_info.get('pieces_created'),
                'last_step': step,
                'last_revision': self.last_revision,
                'hint': hint,
            }

        elif primitive in {'pour', 'scoop'}:
            transfer = effect.get('transfer') if isinstance(effect.get('transfer'), dict) else {}
            vessel_id = self.hand_holding.get('item') or f'vessel@{self.current_location}'
            prev_obj = self.object_states.get(vessel_id, {})
            self.object_states[vessel_id] = {
                **prev_obj,
                'id': vessel_id,
                'location': 'in_hand',
                'area': self.current_location,
                'status': 'transferred_contents',
                'source_has_more_material': transfer.get('source_has_more_material'),
                'some_material_not_retained': bool(transfer.get('some_material_not_retained')),
                'last_step': step,
                'last_revision': self.last_revision,
                'hint': prev_obj.get('hint') or hint,
            }

        elif primitive in {'toggle_on', 'toggle_off'}:
            target_id = (
                _clean_short(args.get('target_label'), 40)
                or (self.hand_holding.get('item') if self.hand_holding.get('status') == 'holding_item' else None)
                or f'unit@{self.current_location}'
            )
            prev_obj = self.object_states.get(target_id, {})
            self.object_states[target_id] = {
                **prev_obj,
                'id': target_id,
                'location': prev_obj.get('location') or f'at:{self.current_location}',
                'area': self.current_location,
                'status': 'toggled_on' if primitive == 'toggle_on' else 'toggled_off',
                'last_step': step,
                'last_revision': self.last_revision,
                'hint': hint or prev_obj.get('hint'),
            }

    def _reconcile_hand_proprioception(
        self,
        hand_prop: str | None,
        error: dict[str, Any],
        *,
        step: int | None = None,
    ) -> None:
        cur_step = self.steps_observed if step is None else step
        stage = str(error.get('stage') or '')
        code = str(error.get('code') or '')
        released_on_error = stage.startswith('released_') or code == 'empty_hand'

        if hand_prop == 'empty' or released_on_error:
            if self.hand_holding.get('status') == 'holding_item':
                dropped_id = self.hand_holding.get('item')
                if dropped_id and code != 'empty_hand':
                    prev_obj = self.object_states.get(dropped_id, {})
                    self.object_states[dropped_id] = {
                        **prev_obj,
                        'id': dropped_id,
                        'location': f'unverified_at:{self.current_location}',
                        'area': self.current_location,
                        'status': 'unverified_after_release',
                        'verified_placement': False,
                        'last_step': cur_step,
                        'last_revision': self.last_revision,
                    }
                self.hand_holding = {
                    'status': 'empty',
                    'item': None,
                    'since_step': cur_step,
                    'from_location': None,
                    'hint': None,
                }
        elif hand_prop == 'holding_item' or code == 'hand_occupied':
            if self.hand_holding.get('status') != 'holding_item':
                self._item_seq += 1
                inferred_id = f'item_{self._item_seq}'
                self.hand_holding = {
                    'status': 'holding_item',
                    'item': inferred_id,
                    'since_step': cur_step,
                    'from_location': self.current_location,
                    'hint': None,
                }

    def _update_process_state(self, proc: dict[str, Any], *, step: int) -> None:
        was_running = bool(self.process_state.get('any_running'))
        running = bool(proc.get('any_process_running', False))
        msgs = proc.get('messages') or ([proc['message']] if isinstance(proc.get('message'), str) else [])

        if running and not was_running:
            self.process_state['active_since_clock_s'] = round(self.robot_clock_s, 1)
            self.process_state['active_location'] = self.current_location
        elif not running and was_running:
            self.process_state['completed_transitions'] = int(self.process_state.get('completed_transitions') or 0) + 1
            self.process_state['active_since_clock_s'] = None
            self.process_state['active_location'] = None

        self.process_state['any_running'] = running
        since = self.process_state.get('active_since_clock_s')
        self.process_state['elapsed_active_s'] = (
            round(max(0.0, self.robot_clock_s - float(since)), 1)
            if running and isinstance(since, (int, float))
            else None
        )
        recent = list(self.process_state.get('recent_messages') or [])
        for m in msgs:
            if isinstance(m, str) and m.strip():
                recent.append({
                    'step': step,
                    'clock_s': round(self.robot_clock_s, 1),
                    'location': self.current_location,
                    'message': _clean_short(m, 96),
                })
        self.process_state['recent_messages'] = recent[-4:]

    def _update_tool_state(self, rcpt: dict[str, Any], *, step: int) -> None:
        lvl = rcpt.get('level') if 'level' in rcpt else rcpt.get('tool_dirtiness_level')
        st = rcpt.get('state') or rcpt.get('tool_state')
        viol = bool(rcpt.get('violation') or rcpt.get('cross_contamination_occurred'))
        msg = _clean_short(rcpt.get('message'), 96)
        self.tool_state = {
            'level': int(lvl) if isinstance(lvl, int) else None,
            'state': str(st) if isinstance(st, str) else None,
            'violation_occurred': viol,
            'last_message': msg,
            'last_step': step,
            'location': self.current_location,
        }

    # ------------------------------------------------------------------
    # Compact Summary, Sliding Image Window & Rolling Handoff Payload
    # ------------------------------------------------------------------
    def render_memory_summary(self, *, max_chars: int = 620) -> str:
        """Render a compact structured summary (~100-150 tokens) bounded by `max_chars`."""
        hand_desc = (
            f"holding({self.hand_holding['item']})"
            if self.hand_holding.get('status') == 'holding_item'
            else 'empty'
        )
        header = (
            f"[StateMemory | step={self.steps_observed} | clock={self.robot_clock_s:.1f}s "
            f"| loc={self.current_location} | hand={hand_desc}]"
        )
        lines = [header]

        # Line 1: Visited locations & measured point-to-point travel times
        locs = list(self.visited_locations.values())
        # Harness option --place-names (default off): named places are listed besides the last 6; without any
        # name the output is unchanged.
        shown = [r for r in locs[:-6] if r.get('name')] + locs[-6:]
        loc_parts = [
            f"{r['id']}" + (f' "{r["name"]}"' if r.get('name') else '')
            + (f"({r['hint']})" if r.get('hint') else '') + f":v{r['visits']}"
            for r in shown
        ]
        loc_prefix = f"Locations({len(locs)}): " + ', '.join(loc_parts)
        edges = self.travel_edges()
        if edges:
            edge_str = ', '.join(
                f"{e['from_location']}<->{e['to_location']}={e['seconds']:.1f}s"
                for e in edges[-4:]
            )
            lines.append(f"- {loc_prefix} | travel: {edge_str}")
        else:
            lines.append(f"- {loc_prefix}")

        # Line 2: Containers (verified closed with items vs currently open)
        closed_verified = [
            f"{cid}@{info['location']}[closed, {_placed_by_robot(info)}]"
            for cid, info in self.container_states.items()
            if info.get('state') == 'closed' and info.get('verified_closed')
        ]
        open_now = [
            f"{cid}@{info['location']}[open, {_placed_by_robot(info)}"
            + (', partial_open' if info.get('partial_opening') else '') + ']'
            for cid, info in self.container_states.items()
            if info.get('state') == 'open'
        ]
        if closed_verified or open_now:
            c_parts = []
            if closed_verified:
                c_parts.append('verified_closed=' + ', '.join(closed_verified[-4:]))
            if open_now:
                c_parts.append('open=' + ', '.join(open_now[-4:]))
            lines.append('- Enclosures: ' + '; '.join(c_parts))

        # Line 3: Verified object placements / transforms & unverified alerts
        verified_objs = [
            info for info in self.object_states.values()
            if info.get('verified_placement') and info.get('status') != 'held'
        ]
        unverified = self.unverified_items()
        if verified_objs or unverified:
            recent_v = [
                f"{o['id']}->{o['location']}"
                + (f"({o['pieces_created']}pcs)" if o.get('pieces_created') else '')
                for o in verified_objs[-4:]
            ]
            obj_line = f"- Verified items({len(verified_objs)}): " + (', '.join(recent_v) if recent_v else 'none')
            if unverified:
                obj_line += f" | UNVERIFIED_RELEASE: {', '.join(unverified[:3])}"
            lines.append(obj_line)

        # Line 4: Process, tool state, and failure loop alert
        alerts: list[str] = []
        if self.process_state.get('any_running'):
            el = self.process_state.get('elapsed_active_s') or 0.0
            ploc = self.process_state.get('active_location') or '?'
            alerts.append(f"process_running=True(elapsed={el:.1f}s @{ploc})")
        elif self.process_state.get('completed_transitions'):
            alerts.append(f"processes_completed={self.process_state['completed_transitions']}")
        if self.tool_state.get('state') is not None:
            alerts.append(
                f"tool={self.tool_state['state']}"
                + (f"(lvl={self.tool_state['level']})" if self.tool_state.get('level') is not None else '')
                + ('[VIOLATION]' if self.tool_state.get('violation_occurred') else '')
            )
        same_fail = int(self.failure_streak.get('same_primitive_in_place') or 0)
        if same_fail >= 2 and self.failure_streak.get('primitives'):
            last_p = self.failure_streak['primitives'][-1]
            last_c = self.failure_streak['codes'][-1]
            alerts.append(f"streak_alert={last_p}x{same_fail}({last_c})")
        if alerts:
            lines.append('- Status: ' + '; '.join(alerts))

        summary = '\n'.join(lines)
        if len(summary) <= max_chars:
            return summary
        return summary[: max(80, max_chars - 3)].rstrip() + '...'

    def public_view(self, *, include_handoff: bool = False, instruction: str | None = None) -> dict[str, Any]:
        """Compact JSON block attached to public `initialize` / `look` / `act` receipts."""
        view: dict[str, Any] = {
            'summary': self.render_memory_summary(),
            'current_location': self.current_location,
            'visited_locations': list(self.visited_locations.keys())[-12:],
            'measured_travel_s': {
                f"{e['from_location']}<->{e['to_location']}": e['seconds']
                for e in self.travel_edges()[-10:]
            },
            'hand_holding': {
                'status': self.hand_holding['status'],
                'item': self.hand_holding.get('item'),
            },
            'verified_closed_containers': {
                cid: {
                    'location': info['location'],
                    'items_count': len(info['items_inside']),
                    'items': info['items_inside'][-6:],
                    'closed_at_step': info.get('closed_at_step'),
                }
                for cid, info in self.container_states.items()
                if info.get('state') == 'closed' and info.get('verified_closed')
            },
            'open_containers': [
                cid for cid, info in self.container_states.items() if info.get('state') == 'open'
            ],
            'verified_placements_count': sum(
                1 for o in self.object_states.values()
                if o.get('verified_placement') and o.get('status') != 'held'
            ),
            'unverified_items': self.unverified_items(),
            'any_process_running': bool(self.process_state.get('any_running')),
            'process_elapsed_s': self.process_state.get('elapsed_active_s'),
        }
        named = self.named_places()
        if named:
            view['named_places'] = named
        if self.tool_state.get('state') is not None:
            view['tool_state'] = {
                'level': self.tool_state.get('level'),
                'state': self.tool_state.get('state'),
                'violation_occurred': self.tool_state.get('violation_occurred'),
            }
        if include_handoff:
            view['handoff_context'] = self.build_handoff_context(instruction or '')
        return view

    def prune_image_window(
        self,
        messages: list[dict[str, Any]],
        *,
        keep_latest_captures: int = 4,
        include_memory_header: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Sliding-window historical panorama image pruner for message arrays.

        Keeps full RGB pixels only for the latest `keep_latest_captures` image-bearing
        items while replacing older historical panorama frames with lightweight text
        markers and retaining structured `StateMemory` context.
        """
        if keep_latest_captures < 0:
            raise ValueError('keep_latest_captures must be nonnegative')

        def _count_imgs(node: Any) -> int:
            if _is_image_part(node):
                return 1
            if isinstance(node, dict):
                return sum(_count_imgs(v) for v in node.values())
            if isinstance(node, list):
                return sum(_count_imgs(v) for v in node)
            return 0

        def _strip_imgs(node: Any) -> Any:
            if _is_image_part(node):
                return {'type': 'input_text', 'text': OMITTED_IMAGE_PLACEHOLDER}
            if isinstance(node, dict):
                return {k: _strip_imgs(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_strip_imgs(v) for v in node]
            return node

        bearing = [(idx, _count_imgs(msg)) for idx, msg in enumerate(messages)]
        img_turns = [(idx, cnt) for idx, cnt in bearing if cnt > 0]
        keep_indices = (
            {idx for idx, _ in img_turns[-keep_latest_captures:]}
            if keep_latest_captures > 0
            else set()
        )
        pruned_list: list[dict[str, Any]] = []
        kept_images = 0
        pruned_images = 0
        for idx, msg in enumerate(messages):
            cnt = bearing[idx][1]
            if cnt == 0 or idx in keep_indices:
                pruned_list.append(copy.deepcopy(msg))
                kept_images += cnt
            else:
                pruned_list.append(_strip_imgs(msg))
                pruned_images += cnt

        if include_memory_header and pruned_images > 0:
            anchor = {
                'role': 'user',
                'content': [{'type': 'input_text', 'text': self.render_memory_summary()}],
            }
            insert_pos = 1 if pruned_list and pruned_list[0].get('role') == 'user' else 0
            pruned_list.insert(insert_pos, anchor)

        stats = {
            'image_turns': len(img_turns),
            'kept_images': kept_images,
            'pruned_images': pruned_images,
        }
        return pruned_list, stats

    def build_handoff_context(
        self,
        instruction: str,
        *,
        segment_index: int = 1,
        steps_remaining: int | None = None,
        planning_mode: str = 'off',
        progress_note: str | None = None,
    ) -> str:
        """Build a compact lossless continuation prompt for rolling session handoffs."""
        lines = [
            f'=== ROLLING SESSION HANDOFF (Segment #{segment_index} — Same Live Episode) ===',
            'Prior historical RGB frames were dropped to prevent context bloat and single-session image limits.',
            'The simulator episode was NOT reset; all prior world changes remain live.',
        ]
        if steps_remaining is not None:
            lines.append(f'Remaining step budget: {steps_remaining} act/look steps.')
        lines.append('')
        lines.append(self.render_memory_summary(max_chars=750))

        closed_map = self.verified_closed_containers()
        if closed_map:
            closed_desc = '; '.join(
                f"{cid} at {info['location']} ({len(info['items_inside'])} verified item(s) inside: {', '.join(info['items_inside'][:6])})"
                for cid, info in list(closed_map.items())[:8]
            )
            lines.append(
                f'Verified closed enclosures (DO NOT reopen unless adding/removing items): {closed_desc}.'
            )

        open_map = self.open_containers()
        if open_map:
            open_desc = ', '.join(f"{cid}@{info['location']}" for cid, info in list(open_map.items())[:8])
            lines.append(f'Currently open enclosures: {open_desc}.')

        named = self.named_places()
        if named:
            lines.append('Named places: ' + ', '.join(f'"{n}" = {loc}' for n, loc in named.items()) + '.')

        if self.unresolved_notes_goals:
            lines.append(f"Unfinished subgoals from your task_notes: {', '.join(self.unresolved_notes_goals[:8])}.")

        if progress_note and progress_note.strip():
            # Harness option: the previous session's own note, passed verbatim.
            lines.extend([
                '',
                'Handoff note written by the previous session (its own account; confirm against current RGB):',
                progress_note.strip(),
            ])

        lines.extend([
            '',
            'Next actions:',
            '1. Call `initialize({})` first (0 action steps; does not reset the scene) to receive the latest 4 RGB views and current revision.',
            '2. Continue only the remaining unfinished requirements from the current state, then verify and call `finish`.',
        ])
        if planning_mode == 'task_graph':
            # Harness option (tier 3 only): the planner session lives in the episode, not the chat.
            lines.append(
                '3. Any task graph you declared with `plan_task_graph` persists in this episode '
                '(see `task_graph_status` in tool responses). Call `plan_task_graph` with only deltas '
                '(e.g. `completed_operations`, updated `state_vars`) to get the next step; declare one '
                'now if `graph_declared` is false.'
            )
        if instruction and instruction.strip():
            lines.extend(['', 'Original Task Instruction:', instruction.strip()])
        return '\n'.join(lines)

    def ingest_compat_events(self, compat_events: list[dict[str, Any]], *, namespace: str = '') -> int:
        """Replay public MCP tool calls/results from controller `compat` events.

        Client item ids (for example codex ``item_7``) restart in every new session, so callers
        replaying several sessions pass a distinct ``namespace`` per session; de-duplication is
        only within one namespace.
        """
        newly_ingested = 0
        for event in compat_events:
            if not isinstance(event, dict) or event.get('type') != 'item.completed':
                continue
            item = event.get('item') or {}
            if not isinstance(item, dict) or item.get('type') != 'mcp_tool_call':
                continue
            raw_id = str(item.get('id') or '')
            call_id = f'{namespace}#{raw_id}' if raw_id else ''
            if call_id and call_id in self._ingested_call_ids:
                continue
            tool = str(item.get('tool') or '')
            if tool not in {'initialize', 'look', 'act', 'task_notes', 'remember'}:
                continue
            args = item.get('arguments') if isinstance(item.get('arguments'), dict) else {}
            res_obj = item.get('result') if isinstance(item.get('result'), dict) else {}
            parsed_receipt = None
            for block in (res_obj.get('content') or []):
                if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
                    try:
                        cand = json.loads(block['text'])
                        if isinstance(cand, dict) and ('ok' in cand or 'initialized' in cand):
                            parsed_receipt = cand
                            break
                    except ValueError:
                        continue
            if parsed_receipt is None:
                continue
            if (parsed_receipt.get('error') or {}).get('code') == 'rolling_handoff_checkpoint':
                if call_id:
                    self._ingested_call_ids.add(call_id)
                continue
            if call_id:
                self._ingested_call_ids.add(call_id)
            self.observe(tool, args, parsed_receipt)
            if tool in {'act', 'look'}:
                newly_ingested += 1
        return newly_ingested


PublicStateMemory = StateMemory
